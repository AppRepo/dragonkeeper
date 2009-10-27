# Copyright (c) 2008, Opera Software ASA
# license see LICENSE.

"""Overview:

The proxy / server is build as an extension of the asyncore.dispatcher class.
There are two instantiation of SimpleServer to listen on the given ports
for new connection, on for HTTP and the other for STP (ScopeTransferProtocol).
They do dispatch a connection to the appropriate classes, HTTPScopeInterface
for HTTP and ScopeConnection for STP. The client is the application which uses
the HTTP interface to connect to scope, the host is the Opera instance
which exposes the scope interface as a STP connection.

There are also two queues, one for HTTP and one for STP to return a scope
message to the client. Getting a new scope message is performed as GET request
with the path /get-message. If the STP queue is not empty then
the first of that queue is returned, otherwise the request is put
in the HTTP waiting-queue. If a new message arrives on the STP sockets it works
the other way around: if the waiting-queue is not empty, the message is
returned to the first waiting connection, otherwise it's put on
the STP message queue.

In contrast to the previous Java version there is only one waiting connection
for scope messages, the messages are dispatched to the target service
on the client side. The target service is added to the response
as custom header 'X-Scope-Message-Service'.

STP/1 messages have a header and a payload. The header is translated
to custom header fields, the payload is the body of the response:

    X-Scope-Message-Service for the service name
    X-Scope-Message-Command for the command id
    X-Scope-Message-Status for the status code
    X-Scope-Message-Tag for the tag

The server is named Dragonkeeper to stay in the started names pace.

The server supports only one host and one client. The main purpose is
developing Opera Dragonfly.

See also http://dragonfly.opera.com/app/scope-interface for more details.
"""

import re
import httpconnection
from time import time
from common import CRLF, RESPONSE_BASIC, RESPONSE_OK_CONTENT
from common import NOT_FOUND, BAD_REQUEST, get_timestamp, Singleton
# from common import pretty_dragonfly_snapshot
from maps import status_map, format_type_map, message_type_map, command_map

test_command_map = {}

# the two queues
connections_waiting = []
scope_messages = []

SERVICE_LIST = """<services>%s</services>"""
SERVICE_ITEM = """<service name="%s"/>"""
XML_PRELUDE = """<?xml version="1.0"?>%s"""


class Scope(Singleton):
    """Access layer for HTTPScopeInterface instances to the scope connection"""

    def __init__(self):
        self.send_command = self.empty_call
        self.services_enabled = {}
        self.version = 'stp-0'
        self._service_list = []
        self._connection = None
        self._http_connection = None

    def empty_call(self, msg):
        pass

    def set_connection(self, connection):
        """ to register the scope connection"""
        self._connection = connection
        self.send_command = connection.send_command_STP_0

    def set_service_list(self, list):
        """to register the service list"""
        self._service_list = list

    def return_service_list(self, http_connection):
        """to get the service list.
        in STP/1 the request of the service list does trigger to (re) connect
        the client. Only after the Connect command was performed successfully
        the service list is returned to the client"""
        if self.version == 'stp-0':
            http_connection.return_service_list(self._service_list)
        elif self.version == 'stp-1':
            if self._connection:
                self._http_connection = http_connection
                self._connection.connect_client(self._connect_callback)
            else:
                http_connection.return_service_list(self._service_list)
        else:
            print "Unsupported version in scope.return_service_list(conn)"

    def set_STP_version(self, version):
        """to register the STP version.
        the version gets set as soon as the STP/1 token is received"""
        if version == "stp-1":
            self.version = "stp-1"
            self.send_command = self._connection.send_command_STP_1
        else:
            print "This stp version is not jet supported"

    def reset(self):
        self._service_list = []
        self.send_command = self.empty_call
        self.services_enabled = {}
        self._connection = None

    def _connect_callback(self):
        if test_command_map:
            self._http_connection.return_service_list(self._service_list)
            self._http_connection = None
        else:
            CommandMap(self._service_list, test_command_map, self._connection, 
                self._connect_callback, self._http_connection.print_command_map)

scope = Scope()

MSG_KEY_TYPE = 0
MSG_KEY_SERVICE = 1
MSG_KEY_COMMAND_ID = 2
MSG_KEY_FORMAT = 3
MSG_KEY_STATUS = 4
MSG_KEY_TAG = 5
MSG_KEY_CLIENT_ID = 6
MSG_KEY_UUID = 7
MSG_KEY_PAYLOAD = 8
MSG_VALUE_COMMAND = 1
MSG_VALUE_FORMAT_JSON = 1

class TagManager(Singleton):

    def __init__(self):
        self._counter = 1
        self._tags = {}
    
    def _get_empty_tag(self):
        tag = 1
        while True:
            if not tag in self._tags:
                return tag
            tag += 1
        
    def set_callback(self, callback, args={}):
        tag = self._get_empty_tag()
        self._tags[tag] = (callback, args)
        return tag

    def handle_message(self, msg):
        if msg[MSG_KEY_TAG] in self._tags:
            callback, args = self._tags.pop(msg[MSG_KEY_TAG])
            callback(msg, **args)
            return True
        return False
        
tag_manager = TagManager()


class CommandMap(object):

    COMMAND_INFO = 7
    COMMAND_MESSAGE_INFO = 11
    MESSAGE_STATUS = 4
    MESSAGE_PAYLOAD = 8
    
    def __init__(self, services, map, connection, callback, print_map):
        self._services = services
        self._services_parsed = {}
        self._map = map
        self._connection = connection
        self._callback = callback
        self._print_map = print_map
        self._connection.set_msg_handler(self.default_msg_handler)
        self.get_command_map()

    def default_msg_handler(self, msg):
        if not tag_manager.handle_message(msg):
            pretty_print(
                "handling of message failed in default_msg_handler in CommandMap:", 
                msg, 1, 0) 

    def check_command_map_complete(self):
        for service in self._services_parsed:
            if not self._services_parsed[service]['parsed']:
                return False
        return True
                
    def get_commands(self, service, tag):
        self._connection.send_command_STP_1({
            MSG_KEY_TYPE: MSG_VALUE_COMMAND,
            MSG_KEY_SERVICE: "scope",
            MSG_KEY_COMMAND_ID: self.COMMAND_INFO,
            MSG_KEY_FORMAT: MSG_VALUE_FORMAT_JSON,
            MSG_KEY_TAG: tag,
            MSG_KEY_PAYLOAD: '["%s"]' % service
            })

    def handle_commands(self, msg, service):
        if not msg[self.MESSAGE_STATUS] and service in self._services_parsed:
            command_list = None
            try:
                command_list = eval(msg[self.MESSAGE_PAYLOAD].replace("null", "None"))
            except:
                print "evaling message failed in handle_commands in CommandMap"
            if command_list:
                self._services_parsed[service]['raw_commands'] = command_list
                tag = tag_manager.set_callback(self.handle_messages, {'service': service})
                self.get_messages(service, tag)
        else:
            pretty_print(
                "handling of message failed in handle_commands in CommandMap:", 
                msg, 1, 0)

    def get_messages(self, service, tag):
        self._connection.send_command_STP_1({
            MSG_KEY_TYPE: MSG_VALUE_COMMAND,
            MSG_KEY_SERVICE: "scope",
            MSG_KEY_COMMAND_ID: self.COMMAND_MESSAGE_INFO,
            MSG_KEY_FORMAT: MSG_VALUE_FORMAT_JSON,
            MSG_KEY_TAG: tag,
            MSG_KEY_PAYLOAD: '["%s", [], 1, 1]' % service
            })



    def handle_messages(self, msg, service):
        if not msg[self.MESSAGE_STATUS] and service in self._services_parsed:
            message_list = None
            try:
                message_list = eval(msg[self.MESSAGE_PAYLOAD].replace("null", "None"))
            except:
                print "evaling message failed in handle_messages in CommandMap"
            if message_list:
                self._services_parsed[service]['raw_messages'] = message_list
                self.parse_raw_lists(service)
                self._services_parsed[service]['parsed'] = True
                if self.check_command_map_complete():
                    if self._print_map:
                        print test_command_map
                    self._connection.clear_msg_handler()
                    self._map['test'] = True
                    self._callback()
                    self._services = None
                    self._services_parsed = None
                    self._map = None
                    self._connection = None
                    self._callback = None
        else:
            pretty_print(
                "handling of message failed in handle_messages in CommandMap:", 
                msg, 1, 0)

    def get_command_map(self):
        for service in self._services:
            if not service.startswith('core-') and not service.startswith('stp-'):
                self._services_parsed[service] = {
                    'parsed': False,
                    'raw_commands': None,
                    'raw_messages': None
                    }
                tag = tag_manager.set_callback(self.handle_commands, 
                                    {"service": service})
                self.get_commands(service, tag)

    # message parsing

    def get_msg(self, list, id):
        MSG_ID = 0
        for msg in list:
            if msg[MSG_ID] == id:
                return msg
        return None
            
    def parse_msg(self, msg, msg_list, parsed_list):
        NAME = 1
        FIELD_LIST = 2
        FIELD_NAME = 0
        FIELD_TYPE = 1
        FIELD_NUMBER = 2
        FIELD_Q = 3
        FIELD_ID = 4
        Q_MAP = {
            0: "required",
            1: "optional",
            2: "repeated"
            }
        ret = []
        if msg:
            for field in msg[FIELD_LIST]:
                name = field[FIELD_NAME]
                if name in parsed_list:
                    return parsed_list[name]
                field_obj = {'name': name}
                field_obj['q'] = "required"
                if (len(field) - 1) >= FIELD_Q and field[FIELD_Q]:
                    field_obj['q'] = Q_MAP[field[FIELD_Q]]
                if (len(field) - 1) >= FIELD_ID:
                    parsed_list[name] = field_obj
                    msg = self.get_msg(msg_list, field[FIELD_ID])
                    field_obj['message'] = self.parse_msg(msg, msg_list, parsed_list)
                ret.append(field_obj)
        return ret

    def parse_raw_lists(self, service):
        MSG_TYPE_COMMAND = 1
        MSG_TYPE_RESPONSE = 2
        MSG_TYPE_EVENT = 3
        # Command Info
        COMMAND_LIST = 0
        NAME = 0
        NUMBER = 1 
        MESSAGE_ID = 2
        RESPONSE_ID = 3
        # Command MessageInfo
        MSG_LIST = 0
        MSG_ID = 0
        command_list = self._services_parsed[service]['raw_commands'][COMMAND_LIST]
        raw_msgs = self._services_parsed[service]['raw_messages'][MSG_LIST]
        map = self._map[service] = {}
        for command in command_list:
            command_obj = map[command[NUMBER]] = {}
            command_obj['name'] = command[NAME]
            # workaround for missing event list
            if command[NAME].lower().startswith('on'):
                msg = self.get_msg(raw_msgs, command[MESSAGE_ID])
                command_obj[MSG_TYPE_EVENT] = self.parse_msg(msg, raw_msgs, {})
            else:
                msg = self.get_msg(raw_msgs, command[MESSAGE_ID])
                command_obj[MSG_TYPE_COMMAND] = self.parse_msg(msg, raw_msgs, {})
                msg = self.get_msg(raw_msgs, command[RESPONSE_ID])
                command_obj[MSG_TYPE_RESPONSE] = self.parse_msg(msg, raw_msgs, {})


def pretty_print_XML(in_string):
    """To pretty print STP 0 messages"""
    if in_string.startswith("<"):
        in_string = re.sub(r"<\?[^>]*>", "", in_string)
        ret = []
        indent_count = 0
        INDENT = "  "
        LF = "\n"
        TEXT = 0
        TAG = 1
        CLOSING_TAG = 2
        OPENING_CLOSING_TAG = 3
        OPENING_TAG = 4
        matches_iter = re.finditer(r"([^<]*)(<(\/)?[^>/]*(\/)?>)", in_string)
        try:
            while True:
                m = matches_iter.next()
                matches = m.groups()
                if matches[CLOSING_TAG]:
                    indent_count -= 1
                    if matches[TEXT] or last_match == OPENING_TAG:
                        ret.append(m.group())
                    else:
                        ret.extend([LF, indent_count * INDENT, m.group()])
                    last_match = CLOSING_TAG
                elif matches[OPENING_CLOSING_TAG] or "<![CDATA[" in matches[1]:
                    last_match = OPENING_CLOSING_TAG
                    ret.extend([LF, indent_count * INDENT, m.group()])
                else:
                    last_match = OPENING_TAG
                    ret.extend([LF, indent_count * INDENT, m.group()])
                    indent_count += 1
        except StopIteration:
            pass
        except:
            raise
    else:
        ret = [in_string]
    return "".join(ret)


def pretty_print_payload_item(indent, name, definition, item):
    INDENT = "  "
    return "%s%s: %s" % (
          indent * INDENT,
          name,
          "message" in definition and \
            "\n" + pretty_print_payload(item,
                            definition["message"], indent=indent+2) or \
            (item == None and "null" or isinstance(item, str) and
             "\"%s\"" % item or item))


def pretty_print_payload(payload, definitions, indent=2):
    INDENT = "  "
    ret = []
    type_str = type("")
    # TODO message: self
    if definitions:
        for item, definition in zip(payload, definitions):
            if definition["q"] == "repeated":
                ret.append("%s%s:" % (indent * INDENT, definition['name']))
                for sub_item in item:
                    ret.append(pretty_print_payload_item(
                            indent + 1,
                            definition['name'].replace("List", ""),
                            definition,
                            sub_item))
            else:
                ret.append(pretty_print_payload_item(
                        indent,
                        definition['name'],
                        definition,
                        item))
        return "\n".join(ret)
    else:
        return ""


def pretty_print(prelude, msg, format, format_payload):
    """
    message type: 1 = command, 2 = response, 3 = event, 4 = error
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
    print prelude
    if format:
        service = msg[1]
        command = command_map.get(service, {}).get(msg[2], None)
        print "  message type:", message_type_map[msg[0]]
        print "  service:", service
        print "  command:", command and \
                        command.get("name", None) or '<id: %d>' % msg[2]
        print "  format:", format_type_map[msg[3]]
        if 4 in msg:
            print "  status:", status_map[msg[4]]
        if 6 in msg:
            print "  cid:", msg[6]
        if 7 in msg:
            print "  uuid:", msg[7]
        if 5 in msg:
            print "  tag:", msg[5]
        if format_payload:
            try:
                # a bit a hack
                payload = eval(msg[8].replace(",null", ",None"))
                print "  payload:"
                print pretty_print_payload(payload, command.get(msg[0], None)), '\n'
            except Exception, e:
                print "\n".join([">>>>>>>", e, msg[8]])
        else:
            print "  payload:", msg[8], "\n"
    else:
        print msg


class HTTPScopeInterface(httpconnection.HTTPConnection):
    """To expose a HTTP interface of the scope interface.

    The first part of the path is the command name, other parts are arguments.
    If there is no matching command, the path is served.

    GET methods:
        /services
            to get a list of available services
        /enable/<service name>
            to enable the given service
        /get-message
            to get a pending message or to wait for the next one
            header informations are added as custom header fields like:
                X-Scope-Message-Service for the service name (the only one in STP/0)
                X-Scope-Message-Command for the command id
                X-Scope-Message-Status for the status code
                X-Scope-Message-Tag for the tag
        /quite
            to quit the session, not implemented

    POST methods:
        STP/0:
            /post-command/<service name>
                request body: message
        STP/1:
            /post-command/<service-name>/<command-id>/<tag>
                request body: message
    """

    # scope specific responses

    # RESPONSE_SERVICELIST % ( timestamp, content length content )
    # HTTP/1.1 200 OK
    # Date: %s
    # Server: Dragonkeeper/0.8
    # Cache-Control: no-cache
    # Content-Type: application/xml
    # Content-Length: %s
    #
    # %s

    RESPONSE_SERVICELIST = RESPONSE_OK_CONTENT % (
        '%s',
        'Cache-Control: no-cache' + CRLF,
        'application/xml',
        '%s',
        '%s',
    )

    # RESPONSE_OK_OK % ( timestamp )
    # HTTP/1.1 200 OK
    # Date: %s
    # Server: Dragonkeeper/0.8
    # Cache-Control: no-cache
    # Content-Type: application/xml
    # Content-Length: 5
    #
    # <ok/>

    RESPONSE_OK_OK = RESPONSE_OK_CONTENT % (
        '%s',
        'Cache-Control: no-cache' + CRLF,
        'application/xml',
        len("<ok/>"),
        "<ok/>",
    )

    # RESPONSE_TIMEOUT % ( timestamp )
    # HTTP/1.1 200 OK
    # Date: %s
    # Server: Dragonkeeper/0.8
    # Cache-Control: no-cache
    # Content-Type: application/xml
    # Content-Length: 10
    #
    # <timeout/>

    RESPONSE_TIMEOUT = RESPONSE_OK_CONTENT % (
        '%s',
        'Cache-Control: no-cache' + CRLF,
        'application/xml',
        len('<timeout/>'),
        '<timeout/>',
    )

    # SCOPE_MESSAGE_STP_0 % ( timestamp, service, message length, message )
    # HTTP/1.1 200 OK
    # Date: %s
    # Server: Dragonkeeper/0.8
    # Cache-Control: no-cache
    # X-Scope-Message-Service: %s
    # Content-Type: application/xml
    # Content-Length: %s
    #
    # %s

    SCOPE_MESSAGE_STP_0 = RESPONSE_OK_CONTENT % (
        '%s',
        'Cache-Control: no-cache' + CRLF + \
        'X-Scope-Message-Service: %s' + CRLF,
        'application/xml',
        '%s',
        '%s',
    )

    # SCOPE_MESSAGE_STP_1 % ( timestamp, service, command, status,
    #                                           tag, message length, message )
    # HTTP/1.1 200 OK
    # Date: %s
    # Server: Dragonkeeper/0.8
    # Cache-Control: no-cache
    # X-Scope-Message-Service: %s
    # X-Scope-Message-Command: %s
    # X-Scope-Message-Status: %s
    # X-Scope-Message-Tag: %s
    # Content-Type: text/plain
    # Content-Length: %s
    #
    # %s
    SCOPE_MESSAGE_STP_1 = RESPONSE_OK_CONTENT % (
        '%s',
        'Cache-Control: no-cache' + CRLF + \
        'X-Scope-Message-Service: %s' + CRLF + \
        'X-Scope-Message-Command: %s' + CRLF + \
        'X-Scope-Message-Status: %s' + CRLF + \
        'X-Scope-Message-Tag: %s' + CRLF,
        'text/plain',
        '%s',
        '%s',
    )

    def __init__(self, conn, addr, context):
        httpconnection.HTTPConnection.__init__(self, conn, addr, context)
        self.debug = context.debug
        self.debug_format = context.format
        self.debug_format_payload = context.format_payload
        self.print_command_map = context.print_command_map
        # for backward compatibility
        self.scope_message = self.get_message
        self.send_command = self.post_command

    # ============================================================
    # GET commands ( first part of the path )
    # ============================================================
    def services(self):
        """to get the service list"""
        if connections_waiting:
            print ">>> failed, connections_waiting is not empty"
        scope.return_service_list(self)
        self.timeout = 0

    def return_service_list(self, serviceList):
        content = SERVICE_LIST % "".join(
            [SERVICE_ITEM % service.encode('utf-8')
            for service in serviceList])
        self.out_buffer += self.RESPONSE_SERVICELIST % (
            get_timestamp(),
            len(content),
            content)

    def enable(self):
        """to enable a scope service"""
        service = self.arguments[0]
        if scope.services_enabled[service]:
            print ">>> service is already enabled", service
        else:
            scope.send_command("*enable %s" % service)
            scope.services_enabled[service] = True

            if service.startswith('stp-'):
                scope.set_STP_version(service)

        self.out_buffer += self.RESPONSE_OK_OK % get_timestamp()
        self.timeout = 0

    def get_message(self):
        """general call to get the next scope message"""
        if scope_messages:
            if scope.version == 'stp-1':
                self.return_scope_message_STP_1(scope_messages.pop(0), self)
            else:
                self.return_scope_message_STP_0(scope_messages.pop(0), self)
            self.timeout = 0
        else:
            connections_waiting.append(self)
        # TODO correct?

    # ============================================================
    # POST commands
    # ============================================================
    def post_command(self):
        """send a command to scope"""
        raw_data = self.raw_post_data
        is_ok = False
        if scope.version == "stp-1":
            args = self.arguments
            """
            message type: 1 = command, 2 = response, 3 = event, 4 = error
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
            /send-command/" + service + "/" + command_id + "/" + tag
            """
            scope.send_command({
                    0: 1, # message type
                    1: args[0],
                    2: int(args[1]),
                    3: 1,
                    5: int(args[2]),
                    8: self.raw_post_data,
                })
            is_ok = True
        else:
            service = self.arguments[0]
            if service in scope.services_enabled:
                if not raw_data.startswith("<?xml") and \
                     not raw_data.startswith("STP/1"):
                    raw_data = XML_PRELUDE % raw_data
                msg = "%s %s" % (service, raw_data.decode('UTF-8'))
                scope.send_command(msg)
                is_ok = True
            else:
                print "tried to send a command before %s was enabled" % service
        self.out_buffer += (is_ok and
                            self.RESPONSE_OK_OK or
                            BAD_REQUEST) % get_timestamp()
        self.timeout = 0

    def snapshot(self):
        """store a markup snapshot"""
        raw_data = self.raw_post_data
        if raw_data:
            name, data = raw_data.split(CRLF, 1)
            f = open(name + ".xml", 'wb')
            # f.write(pretty_dragonfly_snapshot(data))
            data = data.replace("'=\"\"", "")
            data = re.sub(r'<script(?:[^/>]|/[^>])*/>[ \r\n]*', '', data)
            f.write(data.replace("'=\"\"", ""))
            f.close()
        self.out_buffer += self.RESPONSE_OK_OK % get_timestamp()
        self.timeout = 0

    # ============================================================
    # STP 0
    # ============================================================
    def return_scope_message_STP_0(self, msg, sender):
        """ return a message to the client"""
        service, payload = msg
        if self.debug:
            if self.debug_format:
                print "\nsend to client:", service, pretty_print_XML(payload)
            else:
                print "send to client:", service, payload
        self.out_buffer += self.SCOPE_MESSAGE_STP_0 % (
            get_timestamp(),
            service,
            len(payload),
            payload)
        self.timeout = 0
        if not sender == self:
            self.handle_write()

    # ============================================================
    # STP 1
    # ============================================================
    def return_scope_message_STP_1(self, msg, sender):
        """ return a message to the client
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
        if not msg[8]:
            # workaround, status 204 does not work
            msg[8] = ' '
        if self.debug:
            pretty_print("send to client:", msg,
                                self.debug_format, self.debug_format_payload)
        self.out_buffer += self.SCOPE_MESSAGE_STP_1 % (
            get_timestamp(),
            msg[1], # service
            msg[2], # command
            msg[4], # status
            msg[5], # tag
            len(msg[8]),
            msg[8], # payload
        )
        self.timeout = 0
        if not sender == self:
            self.handle_write()

    def timeouthandler(self):
        if self in connections_waiting:
            connections_waiting.remove(self)
            if not self.command in ["get_message", "scope_message"]:
                print ">>> failed, wrong connection type in queue"
            self.out_buffer += self.RESPONSE_TIMEOUT % get_timestamp()
        else:
            self.out_buffer += NOT_FOUND % (get_timestamp(), 0, '')
        self.timeout = 0
    
    def flush(self):
        if self.timeout:
            self.timeouthandler()
            
        

    # ============================================================
    # Implementations of the asyncore.dispatcher class methods
    # ============================================================
    def writable(self):
        if self.timeout and time() > self.timeout and not self.out_buffer:
            self.timeouthandler()
        return bool(self.out_buffer)

    def handle_close(self):
        if self in connections_waiting:
            connections_waiting.remove(self)
        self.close()
