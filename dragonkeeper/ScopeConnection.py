import socket
import asyncore
import codecs
from common import BLANK, BUFFERSIZE
from HTTPScopeInterface import connections_waiting, scope_messages, scope
from HTTPScopeInterface import formatXML, prettyPrint

def encode_varuint(value):
    if value == 0:
        return "\0"
    out = ""
    value = value & 0xffffffffffffffff
    while value:
        part = value & 0x7f
        value >>= 7
        if value:
            part |= 0x80
        out += chr(part)
    return out

def decode_varuint(buf):
    """
    >>> decode_varuint("")
    (None, '')
    >>> decode_varuint(chr(0x00))
    (0, '')
    >>> decode_varuint(chr(0xff)+chr(0x01))
    (255, '')
    >>> decode_varuint(chr(0xff)+chr(0x01)+chr(0x80))
    (255, '\\x80')
    >>> decode_varuint("".join([chr(c) for c in ([0xff]*9 + [0x01])]))
    (18446744073709551615L, '')
    >>> decode_varuint("".join([chr(c) for c in ([0xff]*9 + [0x01,0x80])]))
    (18446744073709551615L, '\\x80')
    >>> decode_varuint("".join([chr(c) for c in [0x0,0x3,0x0,0x0,0x65,0x73,0x74,0x70,0x5f,0x31]]))
    (0, '\\x03\\x00\\x00estp_1')
    """
    if len(buf) == 0:
        return None, buf
    shift = 7
    value = ord(buf[0])
    if value & 0x80 != 0x80:
        return value, buf[1:]
    value &= 0x7f;
    for i, c in enumerate(buf[1:10]):
        c = ord(c)
        if c & 0x80:
            value |= ((c & 0x7f) << shift)
        else:
            value |= (c << shift)
            return value, buf[i+1+1:]
        shift += 7
    if shift > 63:
        return False, buf
    return None, buf

def read_stp1_msg_part(msg):
    varint, msg = decode_varuint(msg)
    if not varint == None:
        tag, type = varint >> 3, varint & 7
        if type == 2:
            length, msg = decode_varuint(msg)
            value = msg[0:length]
            return tag, value, msg[length:]
        elif type == 0:
            value, msg = decode_varuint(msg)
            return tag, value, msg 
        else:
            raise Exception("Not valid type in STP 1 message")
    else:
        raise Exception("Cannot read STP 1 message part")

class ScopeConnection(asyncore.dispatcher):
    """To handle the socket connection to scope."""
    STP1_PB_STP1 = "STP\x01"
    STP1_PB_TYPE_COMMAND = encode_varuint(1)

    STP1_PB_SERVICE = encode_varuint( 1 << 3 | 2 )
    STP1_PB_COMMID = encode_varuint( 2 << 3 | 0 )
    STP1_PB_FORMAT = encode_varuint( 3 << 3 | 0 )
    STP1_PB_STATUS = encode_varuint( 4 << 3 | 0 )
    STP1_PB_TAG = encode_varuint( 5 << 3 | 0 )
    STP1_PB_CID = encode_varuint( 6 << 3 | 0 )
    STP1_PB_UUID = encode_varuint( 7 << 3 | 2 )
    STP1_PB_PAYLOAD = encode_varuint( 8 << 3 | 2 )
    STP1_PB_CLIENT_ID = ""

    def __init__(self, conn, addr, context):        
        asyncore.dispatcher.__init__(self, sock=conn)
        self.addr = addr
        self.debug = context.debug
        self.debug_format = context.format
        # STP 0 meassages
        self.in_buffer = u""
        self.out_buffer = ""
        self.handle_read = self.handle_read_STP_0
        self.check_input = self.read_int_STP_0
        self.msg_length = 0
        self.stream = codecs.lookup('UTF-16BE').streamreader(self)
        # STP 1 messages
        self.varint = 0

        scope.setConnection(self)

    # ============================================================
    # STP 0
    # ============================================================
    # Initialisation, command and message flow for STP 0 
    # 
    #             Opera              proxy                 client
    # 
    # *services     ---------------->
    #                                     ----------------->   *services
    #                                     <-----------------   *enable
    # *enable       <----------------
    # data          <--------------------------------------->  data
    #                                 ....
    #                                     <------------------  *quit
    # *disable      <----------------
    # *quit         ---------------->
    #                                     ------------------>  *hostquit
    #                                     ------------------>  *quit

    def send_command_STP_0(self, msg):
        """ to send a message to scope"""
        if self.debug:
            if self.debug_format:
                service, payload = msg.split(BLANK, 1)
                print "\nsend to scope:", service, formatXML(payload)
            else:
                print "send to scope:", msg
        self.out_buffer += ("%s %s" % (len(msg), msg)).encode("UTF-16BE")
        self.handle_write()


    def send_STP0_message_to_client(self, command, msg):
        if connections_waiting:
            connections_waiting.pop(0).sendScopeEventSTP0(
                    (command, msg), self)
        else:
            scope_messages.append((command, msg))

        
    def read_int_STP_0(self):
        """read int STP 0 message"""
        if BLANK in self.in_buffer:
            raw_int, self.in_buffer = self.in_buffer.split(BLANK, 1)
            self.msg_length = int(raw_int)
            self.check_input = self.read_msg_STP_0
            self.check_input()

    def read_msg_STP_0(self):
        """read length STP 0 message"""
        if len(self.in_buffer) >= self.msg_length:
            command, msg = self.in_buffer[0:self.msg_length].split(BLANK, 1)
            msg = msg.encode("UTF-8")
            command = command.encode("UTF-8")
            if command == "*services":
                services = msg.split(',')
                print "services available:\n ", "\n  ".join(services)
                scope.setServiceList(services)
                for service in services:
                    scope.commands_waiting[service] = []
                    scope.services_enabled[service] = False
            elif command in scope.services_enabled:
                self.send_STP0_message_to_client(command, msg)
                """
                if connections_waiting:
                    connections_waiting.pop(0).sendScopeEventSTP0(
                            (command, msg), self)
                else:
                    scope_messages.append((command, msg))
                """
            self.in_buffer = self.in_buffer[self.msg_length:]
            self.msg_length = 0
            self.check_input = self.read_int_STP_0
            self.check_input()

    def read(self, max_length):
        """to let the codec stramreader class treat 
        the class itself like a file object"""
        try: 
            return self.recv(max_length)
        except socket.error: 
            return ''

    def handle_read_STP_0(self):
        """general read event handler for STP 0"""
        self.in_buffer += self.stream.read(BUFFERSIZE)
        self.check_input()

    # ============================================================
    # STP 1
    # ============================================================

    """
    message Command
    {
      required string service = 1;
      required uint32 commandID = 2;
      required uint32 format = 3;
      optional uint32 tag = 5;
      required binary payload = 8;

      // either clientID or uuid must be sent
      optional uint32 clientID = 6;
      optional string uuid = 7;
    }
    """

    def extractID_to_stp1_pb(self, payload):
        #  '["json","uuid:798551239038509750"]']
        id = payload.split(',', 1)[1].strip('"]')
        # print 'extracted id:', id
        return self.STP1_PB_UUID + encode_varuint(len(id)) + id
 


    def send_command_STP_1(self, msg):
        """ to send a message to scope"""
        service, command, format, tag, payload = msg
        if self.debug:
            """
            if self.debug_format:
                print "\nsend to scope:", prettyPrint(
                                (service, command, 0, format, 0, tag, payload))
            
            else:
            """
            print ("send to scope:\n  " +
                    "service:"), service, ("\n  " +
                    "command:"), command, ("\n  " +
                    "format:"), format,  ("\n  " +
                    "tag:"), tag, ("\n  " +
                    "payload:"), payload
                    
        stp_1_msg = "".join([
            self.STP1_PB_TYPE_COMMAND,
            self.STP1_PB_SERVICE, encode_varuint(len(service)), service,
            self.STP1_PB_COMMID, encode_varuint(command), 
            self.STP1_PB_FORMAT, encode_varuint(format), 
            self.STP1_PB_TAG, encode_varuint(tag), 
            ( self.STP1_PB_CLIENT_ID or self.extractID_to_stp1_pb(payload) ), 
            self.STP1_PB_PAYLOAD, encode_varuint(len(payload)), payload
            ])
        self.out_buffer += (
            self.STP1_PB_STP1 + 
            encode_varuint(len(stp_1_msg)) + 
            stp_1_msg
            )
        # print repr(self.out_buffer)
        self.handle_write()

    def setInitializerSTP_1(self):
        """chnge the read handler to the STP/1 read handler"""
        if self.in_buffer or self.out_buffer:
            raise Exception("read or write buffer is not empty "
                                                "in setInitializerSTP_1")
        self.in_buffer = ""
        self.out_buffer = ""
        self.handle_read = self.read_STP_1_initializer
        self.check_input = None
        self.msg_length = 0

    def read_STP_1_initializer(self):
        """read the STP/1 tolken"""
        self.in_buffer += self.recv(BUFFERSIZE)
        if self.in_buffer.startswith("STP/1\n"):
            # print self.in_buffer[0:6]
            self.in_buffer = self.in_buffer[6:]
            self.send_STP0_message_to_client("", "STP/1\n")
            # print 'version:', scope.version
            # TODO this could cause problems, if there is no conection waiting
            
            self.handle_read = self.handle_read_STP_1
            self.check_input = self.read_stp1_token
            if self.in_buffer:
                self.check_input()

    def read_stp1_token(self):
        if self.in_buffer.startswith(self.STP1_PB_STP1):
            self.in_buffer = self.in_buffer[4:]
            self.check_input = self.read_varint
            if self.in_buffer:
                self.check_input()
            
    def read_varint(self):
        """read varint STP 1 message"""

        varint, buffer = decode_varuint(self.in_buffer)
        if not varint == None:
            self.varint = varint
            self.in_buffer = buffer
            self.check_input = self.read_binary
            if self.in_buffer:
                self.check_input()
        """
        while self.in_buffer:
            byte, self.in_buffer = ord(self.in_buffer[0]), self.in_buffer[1:]
            self.varint += ( byte & 0x7f ) << self.bit_count * 7
            self.bit_count += 1
            CHUNKSIZE = 5
            TYPE_FIELD = 2
            if not byte & 0x80:
                if self.parse_state == CHUNKSIZE:
                    if self.varint:
                        self.check_input = self.read_binary
                    else:
                        self.msg_buffer.append(self.binary_buffer)
                        self.handleMessageSTP1()
                else:
                    if self.parse_state == TYPE_FIELD:
                        self.msg_buffer.extend(
                                    [self.varint >> 2, self.varint & 0x3])
                    else:
                        self.msg_buffer.append(self.varint)
                    self.parse_state += 1
                    self.varint = 0
                    self.bit_count = 0
                break
            if self.bit_count > 8:
                raise Exception("broken varint")
        """

        if self.in_buffer:
            self.check_input()

    def read_binary(self):
        """read length STP 1 message"""
        if len(self.in_buffer) >= self.varint:
            stp1_msg = self.in_buffer[0:self.varint]
            self.in_buffer = self.in_buffer[self.varint:]
            self.varint = 0
            self.check_input = self.read_stp1_token
            self.parse_stp1_msg(stp1_msg)
            if self.in_buffer:
                self.check_input()

    def handle_read_STP_1(self):
        """general read event handler for STP 1"""
        self.in_buffer += self.recv(BUFFERSIZE)
        # print repr(self.in_buffer)
        self.check_input()

    def parse_stp1_msg(self, stp1_msg):
        """parse a STP 1 message"""
        msg_type, stp1_msg = decode_varuint(stp1_msg)
        if not msg_type == None:
            # msg_type: 1 = command, 2 = response, 3 = event, 4 = error
            """
            message TransportMessage
            {
                required string service = 1;
                required uint32 commandID = 2;
                required uint32 format = 3;
                optional uint32 status = 4;
                optional uint32 tag = 5;
                optional uint32 clientID = 6;
                optional string uuid = 7;
                required binary payload = 8;
            }
            """
            msg = {
                4: 0, 
                5: 0, 
                6: 0,
                8: '', 
                'type': msg_type
                }
            while stp1_msg:
                tag, value, stp1_msg = read_stp1_msg_part(stp1_msg)
                msg[tag] = value
        else:
            raise Exception("Message type of STP 1 message cannot be parsed") 
        
        # for k in msg: print k,':', msg[k]
        if not self.STP1_PB_CLIENT_ID:
            self.STP1_PB_CLIENT_ID = self.STP1_PB_CID + encode_varuint(msg[6])



        # print msg
        # TODO? check service enabled
        if connections_waiting:
            connections_waiting.pop(0).sendScopeEventSTP1(msg, self)
        else:
            scope_messages.append(msg)
        # store hello message
        """
            message TransportMessage
    {
      required string service = 1;
      required uint32 commandID = 2;
      required uint32 format = 3;
      optional uint32 status = 4;
      optional uint32 tag = 5;
      optional uint32 clientID = 6;
      optional string uuid = 7;
      required binary payload = 8;
    }
    """
        #print "parse_stp1_msg:", msg
        """
        tag, value, msg = read_stp1_msg_part(msg)
        # TODO? check service enabled
        if self.msg_buffer[0] == 0 and self.msg_buffer[1] == 1:
            scope.storeHelloMessage(self.msg_buffer)
        if connections_waiting:
            connections_waiting.pop(0).sendScopeEventSTP1(self.msg_buffer, self)
        else:
            scope_messages.append(self.msg_buffer)
        # store hello message

        self.varint = 0
        # 
        self.bit_count = 0
        self.binary_buffer = ""
        self.msg_buffer = []
        self.parse_state = 0
        self.parse_msg_state = ""
        """
        
    # ============================================================
    # Implementations of the asyncore.dispatcher class methods 
    # ============================================================

    def handle_read(self):
        pass
                
    def writable(self):
        return (len(self.out_buffer) > 0)
        
    def handle_write(self):
        sent = self.send(self.out_buffer)
        self.out_buffer = self.out_buffer[sent:]

    def handle_close(self):
        scope.reset()
        self.close()
