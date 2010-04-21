import re
from common import Singleton
from maps import status_map, format_type_map, message_type_map, message_map

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
MSG_TYPE_ERROR = 4

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




class MessageMap(object):
    """ to create a description map of all messages 
    to be used to pretty print the payloads by adding the keys to all values"""
    COMMAND_INFO = 7
    COMMAND_HOST_INFO = 10
    COMMAND_MESSAGE_INFO = 11
    COMMAND_ENUM_INFO = 12
    INDENT = "    "
    filter = None

    @staticmethod
    def set_filter(filter):
        def create_check(check):
            def check_default(in_str):
                return check == in_str
            def check_endswith(in_str):
                return in_str.endswith(check)
            def check_startswith(in_str):
                return in_str.startswith(check)
            def check_pass(in_str):
                return True
            if check in "*":
                return check_pass
            if check.endswith('*'):
                check = check.strip('*')
                return check_startswith
            if check.startswith('*'):
                check = check.strip('*')
                return check_endswith
            return check_default
        content = filter
        filter_obj = None
        import os
        if os.path.isfile(filter):
            try:
                file = open(filter, 'rb')
                content = file.read()
                file.close()
            except:
                print "reading filter failed"
        try:
            code = compile(content.replace('\r\n', '\n'), filter, 'eval')
            filter_obj = eval(code)
        except:
            print "parsing the specified filter failed"
        print "parsed filter:", filter_obj
        if filter_obj:
            for service in filter_obj:
                for type in filter_obj[service]:
                    filter_obj[service][type] = (
                        [create_check(check) for check in filter_obj[service][type]]
                        )
            MessageMap.filter = filter_obj

    @staticmethod
    def has_map():
        return bool(message_map)
    
    def __init__(self, services, connection, callback, context, map=message_map):
        self._services = services
        self.scope_major_version = 0
        self.scope_minor_version = 0
        self.message_info_payload = '["%s", [], 1, 1]'
        self._services_parsed = {}
        self._map = map
        self._connection = connection
        self._callback = callback
        self._print_map = context.print_message_map
        self._print_map_services = context.print_message_map_services.split(',')
        self._connection.set_msg_handler(self.default_msg_handler)
        self.get_host_info()

    # getting the messages from scope

    def default_msg_handler(self, msg):
        if not tag_manager.handle_message(msg):
            pretty_print(
                "handling of message failed in default_msg_handler in MessageMap:", 
                msg, 1, 0) 
    
    def check_message_map_complete(self):
        for service in self._services_parsed:
            if not self._services_parsed[service]['parsed']:
                return False
        return True

    def check_enum_map_complete(self):
        for service in self._services_parsed:
            if not self._services_parsed[service]['parsed_enums']:
                return False
        return True


    def get_host_info(self):
        for service in self._services:
            if not service.startswith('core-') and not service.startswith('stp-'):
                self._services_parsed[service] = {
                    'parsed': False,
                    'parsed_enums': False,
                    'raw_commands': None,
                    'raw_messages': None
                    }
        self._connection.send_command_STP_1({
            MSG_KEY_TYPE: MSG_VALUE_COMMAND,
            MSG_KEY_SERVICE: "scope",
            MSG_KEY_COMMAND_ID: self.COMMAND_HOST_INFO,
            MSG_KEY_FORMAT: MSG_VALUE_FORMAT_JSON,
            MSG_KEY_TAG: tag_manager.set_callback(self.handle_host_info),
            MSG_KEY_PAYLOAD: '[]'
            })
            
                
    def handle_host_info(self, msg):
        if not msg[MSG_KEY_STATUS]:
            host_info = None
            try:
                host_info = eval(msg[MSG_KEY_PAYLOAD].replace("null", "None"))
            except:
                print "evaling message failed in handle_host_info in MessageMap"
            if host_info:
                for service in host_info[5]:
                    if service[0] == "scope":
                        versions = map(int, service[1].split('.'))
                        self.scope_major_version = versions[0]
                        self.scope_minor_version = versions[1]
                if self.scope_minor_version >= 1:
                    self.message_info_payload = '["%s", [], 1, 1, 1, 1]'
                    self.get_all_enums()
                else:
                    self.get_message_map()
        else:
            print "getting host info failed"
        
        
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
        if not msg[MSG_KEY_STATUS] and service in self._services_parsed:
            command_list = None
            try:
                command_list = eval(msg[MSG_KEY_PAYLOAD].replace("null", "None"))
            except:
                print "evaling message failed in handle_commands in MessageMap"
            if command_list:
                self._services_parsed[service]['raw_commands'] = command_list
                tag = tag_manager.set_callback(self.handle_messages, {'service': service})
                self.get_messages(service, tag)
        else:
            pretty_print(
                "handling of message failed in handle_commands in MessageMap:", 
                msg, 1, 0)

    def get_messages(self, service, tag):
        self._connection.send_command_STP_1({
            MSG_KEY_TYPE: MSG_VALUE_COMMAND,
            MSG_KEY_SERVICE: "scope",
            MSG_KEY_COMMAND_ID: self.COMMAND_MESSAGE_INFO,
            MSG_KEY_FORMAT: MSG_VALUE_FORMAT_JSON,
            MSG_KEY_TAG: tag,
            MSG_KEY_PAYLOAD: self.message_info_payload % service
            })

    def handle_messages(self, msg, service):
        if not msg[MSG_KEY_STATUS] and service in self._services_parsed:
            message_list = None
            try:
                message_list = eval(msg[MSG_KEY_PAYLOAD].replace("null", "None"))
            except:
                print "evaling message failed in handle_messages in MessageMap"
            if message_list:
                self._services_parsed[service]['raw_messages'] = message_list
                self.parse_raw_lists(service)
                self._services_parsed[service]['parsed'] = True
                if self.check_message_map_complete():
                    self.finalize()
        else:
            pretty_print(
                "handling of message failed in handle_messages in MessageMap:", 
                msg, 1, 0)

    def get_all_enums(self):
        for service in self._services_parsed:
            tag = tag_manager.set_callback(self.handle_enums, {'service': service})
            self.get_enums(service, tag)
            

    def get_enums(self, service, tag):
        self._connection.send_command_STP_1({
            MSG_KEY_TYPE: MSG_VALUE_COMMAND,
            MSG_KEY_SERVICE: "scope",
            MSG_KEY_COMMAND_ID: self.COMMAND_ENUM_INFO,
            MSG_KEY_FORMAT: MSG_VALUE_FORMAT_JSON,
            MSG_KEY_TAG: tag,
            MSG_KEY_PAYLOAD: '["%s", [], 1]' % service 
            })

    def handle_enums(self, msg, service):
        if not msg[MSG_KEY_STATUS] and service in self._services_parsed:
            enum_list = None
            try:
                enum_list = eval(msg[MSG_KEY_PAYLOAD].replace("null", "None"))
            except:
                print "evaling message failed in handle_enums in MessageMap"
            if not enum_list == None:
                self._services_parsed[service]['raw_enums'] = enum_list and enum_list[0] or []
                self._services_parsed[service]['parsed_enums'] = True
                if self.check_enum_map_complete():
                    self.get_message_map()
        else:
            pretty_print(
                "handling of message failed in handle_messages in MessageMap:", 
                msg, 1, 0)
        

    def get_message_map(self):
        for service in self._services_parsed:
            tag = tag_manager.set_callback(self.handle_commands, {"service": service})
            self.get_commands(service, tag)

    def finalize(self):
        if self._print_map:
            self.pretty_print_map()
        self._connection.clear_msg_handler()
        self._callback()
        self._services = None
        self._services_parsed = None
        self._map = None
        self._connection = None
        self._callback = None


    # message parsing

    def get_msg(self, list, id):
        MSG_ID = 0
        for msg in list:
            if msg[MSG_ID] == id:
                return msg
        return None

    def get_enum(self, list, id):
        enums = self.get_msg(list, id)
        name = enums[1]
        ret = []
        dict = {}
        if enums:
            for enum in enums[2]:
                dict[enum[1]] = enum[0]
        for i in range(max(dict.keys()) + 1):
            ret.append(dict.get(i, ''))
        return name, ret
            
    def parse_msg(self, msg, msg_list, parsed_list, raw_enums):
        NAME = 1
        FIELD_LIST = 2
        FIELD_NAME = 0
        FIELD_TYPE = 1
        FIELD_NUMBER = 2
        FIELD_Q = 3
        FIELD_ID = 4
        ENUM_ID = 5
        MSG_IS_UNION = 4
        Q_MAP = {
            0: "required",
            1: "optional",
            2: "repeated"
            }
        ret = []
        if msg:
            for field in msg[FIELD_LIST]:
                name = field[FIELD_NAME]
                field_obj = {'name': name, 'is_union': 0}
                field_obj['q'] = "required"
                field_obj['type'] = field[FIELD_TYPE]
                if (len(field) - 1) >= FIELD_Q and field[FIELD_Q]:
                    field_obj['q'] = Q_MAP[field[FIELD_Q]]
                if (len(field) - 1) >= FIELD_ID and field[FIELD_ID]:
                    if name in parsed_list:
                        field_obj['message'] = {'recursive': parsed_list[name]}
                    else:
                        parsed_list[name] = field_obj
                        msg = self.get_msg(msg_list, field[FIELD_ID])
                        
                        if msg and (len(msg) - 1) >= MSG_IS_UNION and msg[MSG_IS_UNION]:
                            field_obj['is_union'] = 1  
                        field_obj['message_name'] = msg and msg[1] or 'default'
                        field_obj['message'] = self.parse_msg(msg, msg_list, parsed_list, raw_enums)
                if (len(field) - 1) >= ENUM_ID and field[ENUM_ID]:
                    field_obj['enum_name'], field_obj['enum'] = self.get_enum(raw_enums, field[ENUM_ID])
                ret.append(field_obj)
        return ret

    def parse_raw_lists(self, service):
        MSG_TYPE_COMMAND = 1
        MSG_TYPE_RESPONSE = 2
        MSG_TYPE_EVENT = 3
        # Command Info
        COMMAND_LIST = 0
        EVENT_LIST = 1
        NAME = 0
        NUMBER = 1 
        MESSAGE_ID = 2
        RESPONSE_ID = 3
        # Command MessageInfo
        MSG_LIST = 0
        MSG_ID = 0
        raw_msgs = self._services_parsed[service]['raw_messages'][MSG_LIST]
        map = self._map[service] = {}
        command_list = self._services_parsed[service]['raw_commands'][COMMAND_LIST]
        raw_enums = self._services_parsed[service].get('raw_enums', [])
        for command in command_list:
            command_obj = map[command[NUMBER]] = {}
            command_obj['name'] = command[NAME]
            msg = self.get_msg(raw_msgs, command[MESSAGE_ID])
            command_obj[MSG_TYPE_COMMAND] = self.parse_msg(msg, raw_msgs, {}, raw_enums)
            msg = self.get_msg(raw_msgs, command[RESPONSE_ID])
            command_obj[MSG_TYPE_RESPONSE] = self.parse_msg(msg, raw_msgs, {}, raw_enums)

        if len(self._services_parsed[service]['raw_commands']) - 1 >= EVENT_LIST:
            command_list = self._services_parsed[service]['raw_commands'][EVENT_LIST]
            for command in command_list:
                command_obj = map[command[NUMBER]] = {}
                command_obj['name'] = command[NAME]
                msg = self.get_msg(raw_msgs, command[MESSAGE_ID])
                command_obj[MSG_TYPE_EVENT] = self.parse_msg(msg, raw_msgs, {}, raw_enums)

    # pretty print message map

    def pretty_print_fields(self, fields, indent):
        ret = []
        for field in fields:
            ret.append('%s{' % (indent * MessageMap.INDENT))
            indent += 1
            ret.append('%s"name": "%s",' % (indent * MessageMap.INDENT, field['name']))
            ret.append('%s"type": %s,' % (indent * MessageMap.INDENT, field['type']))
            ret.append('%s"q": "%s",' % (indent * MessageMap.INDENT, field['q']))
            ret.append('%s"is_union": %s,' % (indent * MessageMap.INDENT, field['is_union']))
            if "enum" in field:
            
                ret.append('%s"enum_name": %s,' % (indent * MessageMap.INDENT, field['enum_name']))
                ret.append('%s"enum": %s,' % (indent * MessageMap.INDENT, field['enum']))
            if "message" in field:
                ret.append('%s"message_name: "%s"' % (indent * MessageMap.INDENT, field['message_name']))
                if 'recursive' in field['message']:
                    ret.append('%s"message": <recursive reference>,' % (
                        indent * MessageMap.INDENT))
                else:
                    ret.append('%s"message": [' % (indent * MessageMap.INDENT))
                    ret.extend(self.pretty_print_fields(field['message'], indent + 1))
                    ret.append('%s],' % (indent * MessageMap.INDENT))
            indent -= 1
            ret.append('%s},' % (indent * MessageMap.INDENT))
        return ret

    def pretty_print_message(self, message, indent):
        ret = []
        ret.append('%s"name": "%s",' % (indent * MessageMap.INDENT , message['name']))
        for key in [1, 2, 3]:
            if key in message:
                ret.append('%s%s: [' % (indent * MessageMap.INDENT , key))
                ret.extend(self.pretty_print_fields(message[key], indent + 1))
                ret.append('%s],' % (indent * MessageMap.INDENT))
        return ret

    def pretty_print_commands(self, commands, indent):
        ret = []
        keys = commands.keys()
        keys.sort()
        for key in keys:
            ret.append('%s%s: {' % (indent * MessageMap.INDENT , key))
            ret.extend(self.pretty_print_message(commands[key], indent + 1))
            ret.append('%s},' % (indent * MessageMap.INDENT))
        return ret
            
            
    def pretty_print_map(self):
        indent = 1
        map = self._map
        ret = ['message map:']
        ret.append('{')
        for service in map:
            if not self._print_map_services or service in self._print_map_services:
                ret.append('%s"%s": {' % (indent * MessageMap.INDENT , service))
                ret.extend(self.pretty_print_commands(map[service], indent + 1))
                ret.append('%s},' % (indent * MessageMap.INDENT))
        for chunk in ret:
            print chunk

def pretty_print_XML(prelude, in_string, format):
    """To pretty print STP 0 messages"""
    INDENT = "  "
    LF = "\n"
    TEXT = 0
    TAG = 1
    CLOSING_TAG = 2
    OPENING_CLOSING_TAG = 3
    OPENING_TAG = 4
    print prelude
    if format:
        if in_string.startswith("<"):
            in_string = re.sub(r"<\?[^>]*>", "", in_string)
            ret = []
            indent_count = 0
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
        in_string = "".join(ret).lstrip(LF)
    print in_string


def pretty_print_payload_item(indent, name, definition, item):
    INDENT = "  "
    if 'message' in definition and 'recursive' in definition['message']:
        definition['message'] = definition['message']['recursive']['message']
    if definition['is_union']:
        definition = definition['message'][item[0] - 1]
        if 'message' in definition:
            item = item[1:]
        else:
            item = item[1]
    return "%s%s: %s" % (
          indent * INDENT,
          name,
          "message" in definition and \
            "\n" + pretty_print_payload(item,
                            definition["message"], indent=indent+1) or \
            "enum" in definition and \
                "%s (%s)" % (definition['enum'][item], item) or
            (item == None and "null" or isinstance(item, str) and
             "\"%s\"" % item or item))


def pretty_print_payload(payload, definitions, indent=2):
    INDENT = "  "
    ret = []
    type_str = type("")
    # TODO message: <recursive reference>
    if definitions:
        try:
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
        except Exception, msg:
            print "failed to pretty print the paylod. wrong message structure?"
            print "%spayload:" % INDENT, payload
            print "%sdefinition:" % INDENT, definitions
    else:
        return ""

def check_message(service, command, message_type):
    if MessageMap.filter and service in MessageMap.filter and \
        message_type in MessageMap.filter[service]:
            for check in MessageMap.filter[service][message_type]:
                if check(command):
                    return True
    return False
    
# TODO handle 'recursive'
def pretty_print(prelude, msg, format, format_payload):
    service = msg[MSG_KEY_SERVICE]
    command_def = message_map.get(service, {}).get(msg[MSG_KEY_COMMAND_ID], None)
    command_name = command_def and command_def.get("name", None) or \
                                    '<id: %d>' % msg[MSG_KEY_COMMAND_ID]
    message_type = message_type_map[msg[MSG_KEY_TYPE]]
    if not MessageMap.filter or check_message(service, command_name, message_type): 
        print prelude
        if format:
            print "  message type:", message_type
            print "  service:", service
            print "  command:", command_name
            print "  format:", format_type_map[msg[MSG_KEY_FORMAT]]
            if MSG_KEY_STATUS in msg:
                print "  status:", status_map[msg[MSG_KEY_STATUS]]
            if MSG_KEY_CLIENT_ID in msg:
                print "  cid:", msg[MSG_KEY_CLIENT_ID]
            if MSG_KEY_UUID in msg:
                print "  uuid:", msg[MSG_KEY_UUID]
            if MSG_KEY_TAG in msg:
                print "  tag:", msg[MSG_KEY_TAG]
            if format_payload and not msg[MSG_KEY_TYPE] == MSG_TYPE_ERROR:
                payload = None
                try:
                    # a bit a hack
                    payload = eval(msg[8].replace(",null", ",None"))
                except:
                    print "failed evaling the payload in pretty_print"
                print "  payload:"
                if type(payload) == type([]) and command_def:
                    print pretty_print_payload(payload, 
                                    command_def.get(msg[MSG_KEY_TYPE], None)), "\n"
                else:
                    print "    ", msg[MSG_KEY_PAYLOAD], "\n"
            else:
                print "  payload:", msg[MSG_KEY_PAYLOAD], "\n"
        else:
            print msg
