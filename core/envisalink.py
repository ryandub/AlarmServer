import time, datetime
from tornado import gen
from tornado.tcpclient import TCPClient
from tornado.iostream import IOStream, StreamClosedError
from envisalinkdefs import evl_ResponseTypes
from envisalinkdefs import evl_Defaults
from envisalinkdefs import evl_ArmModes

#alarmserver logger
import logger

#import config
from config import config
from events import events

def dict_merge(a, b):
    c = a.copy()
    c.update(b)
    return c

def getMessageType(code):
    return evl_ResponseTypes[code]

def to_chars(string):
    chars = []
    for char in string:
        chars.append(ord(char))
    return chars

def get_checksum(code, data):
    return ("%02X" % sum(to_chars(code)+to_chars(data)))[-2:]

class Client(object):
    def __init__(self):
        logger.debug('Staring Envisalink Client')

        # Register events for alarmserver requests -> envisalink
        events.register('alarm_update', self.request_action)

        # Create TCP Client
        self.tcpclient = TCPClient()

        # Connection
        self._connection = None

        # Are we logged in?
        self._loggedin = False

        # Set our terminator to \r\n
        self._terminator = b"\r\n"

        # Reconnect delay
        self._retrydelay = 10

        self.do_connect()

    @gen.coroutine
    def do_connect(self, reconnect = False):
        # Create the socket and connect to the server
        if reconnect == True:
            logger.warning('Connection failed, retrying in '+str(self._retrydelay)+ ' seconds')
            for i in range(0, self._retrydelay):
                time.sleep(1)

        logger.debug('Connecting to {}:{}'.format(config.ENVISALINKHOST, config.ENVISALINKPORT))

        self._connection = yield self.tcpclient.connect(config.ENVISALINKHOST, config.ENVISALINKPORT)

        #set on stream close callback
        self._connection.set_close_callback(self.handle_close)

        #kick off first read line
        line = yield self._connection.read_until(self._terminator)
        logger.debug("Connected to %s:%i" % (config.ENVISALINKHOST, config.ENVISALINKPORT))
        self.handle_line(line)

    def handle_close(self):
        self._loggedin = False
        #self._connection.disconnect()
        logger.info("Disconnected from %s:%i" % (config.ENVISALINKHOST, config.ENVISALINKPORT))
        self.do_connect(True)

    #TODO: not implemented
    def handle_error(self):
        self._loggedin = False
        self.close()
        logger.error("Disconnected from %s:%i" % (config.ENVISALINKHOST, config.ENVISALINKPORT))
        self.do_connect(True)

    @gen.coroutine    
    def send_command(self, code, data, checksum = True):
        if checksum == True:
            to_send = code+data+get_checksum(code,data)+'\r\n'
        else:
            to_send = code+data+'\r\n'

        logger.debug('TX > '+to_send[:-1])
        res = yield self._connection.write(to_send)

    @gen.coroutine
    def handle_line(self, input):
        if input != '':
            code=int(input[:3])
            parameters=input[3:][:-4]
            event = getMessageType(int(code))
            message = self.format_event(event, parameters)
            logger.debug('RX < ' +str(code)+' - '+message)

            try:
                handler = "handle_%s" % evl_ResponseTypes[code]['handler']
            except KeyError:
                #call general event handler
                self.handle_event(code, parameters, event, message)
                line = yield self._connection.read_until(self._terminator)
                self.handle_line(line)
                return

            try:
                func = getattr(self, handler)
            except AttributeError:
                raise CodeError("Handler function doesn't exist")

            func(code, parameters, event, message)
            line = yield self._connection.read_until(self._terminator)
            self.handle_line(line)

    def format_event(self, event, parameters):
        if 'type' in event:
            if event['type'] in ('partition', 'zone'):
                if event['type'] == 'partition':
                    # If parameters includes extra digits then this next line would fail
                    # without looking at just the first digit which is the partition number
                    if int(parameters[0]) in config.PARTITIONNAMES:
                        # After partition number can be either a usercode
                        # or for event 652 a type of arm mode (single digit)
                        # Usercode is always 4 digits padded with zeros
                        if len(str(parameters)) == 5:
                            # We have a usercode
                            try:
                                usercode = int(parameters[1:5])
                            except:
                                usercode = 0
                            if int(usercode) in config.ALARMUSERNAMES:
                                alarmusername = config.ALARMUSERNAMES[int(usercode)]
                            else:
                                # Didn't find a username, use the code instead
                                alarmusername = usercode
                            return event['name'].format(str(config.PARTITIONNAMES[int(parameters[0])]), str(alarmusername))
                        elif len(parameters) == 2:
                            # We have an arm mode instead, get it's friendly name
                            armmode = evl_ArmModes[int(parameters[1])]
                            return event['name'].format(str(config.PARTITIONNAMES[int(parameters[0])]), str(armmode))
                        else:
                            return event['name'].format(str(config.PARTITIONNAMES[int(parameters)]))
                elif event['type'] == 'zone':
                    if int(parameters) in config.ZONENAMES:
                        if config.ZONENAMES[int(parameters)]!=False:
                            return event['name'].format(str(config.ZONENAMES[int(parameters)]))

        return event['name'].format(str(parameters))

    #envisalink event handlers, some events are unhandeled.
    def handle_login(self, code, parameters, event, message):
        if parameters == '3':
            self._loggedin = True
            self.send_command('005', config.ENVISALINKPASS)
        if parameters == '1':
            self.send_command('001', '')
        if parameters == '0':
            logger.warning('Incorrect envisalink password')
            sys.exit(0)

    def handle_event(self, code, parameters, event, message):
        # only handle events with a 'type' defined
        if not 'type' in event:
            return

        parameters = int(parameters)
        
        try:
            defaultStatus = evl_Defaults[event['type']]
        except IndexError:
            defaultStatus = {}
        
        if (event['type'] == 'zone' and parameters in config.ZONENAMES) or (event['type'] == 'partition' and parameters in config.PARTITIONNAMES):
            events.put('alarm', event['type'], code, parameters, event, message, defaultStatus) 
        else:
            logger.debug('Ignoring unnamed %s %s' % (event['type'], parameters))

    def handle_zone(self, code, parameters, event, message):
        self.handle_event(code, parameters[1:], event, message)

    def handle_partition(self, code, parameters, event, message):
        self.handle_event(code, parameters[0], event, message)

    def request_action(self, eventType, type, parameters):
        if type == 'arm':
            self.send_command('030', '1')
        elif type == 'stayarm':
            self.send_command('031', '1')            
        elif type == 'armwithcode':
            self.send_command('033', '1' + str(parameters['alarmcode']))
        elif type == 'disarm':
            if 'alarmcode' in parameters:
                self.send_command('040', '1' + str(parameters['alarmcode']))
            else:
                self.send_command('040', '1' + str(config.ALARMCODE))
        elif type == 'refresh':
            self.send_command('001', '')
        elif type == 'pgm':
            response = {'response' : 'Request to trigger PGM'}

"""class Proxy(asyncore.dispatcher):
    def __init__(self, config, server):

        logger = logging.getLogger('alarmserver.Proxy')
        
        config = config
        if config.ENABLEPROXY == False:
            return

        asyncore.dispatcher.__init__(self)
        self.create_socket(socket.AF_INET, socket.SOCK_STREAM)
        self.set_reuse_addr()
        logger.info('Envisalink Proxy Started')

        self.bind(("", config.ENVISALINKPROXYPORT))
        self.listen(5)

    def handle_accept(self):
        pair = self.accept()
        if pair is None:
            pass
        else:
            sock, addr = pair
            logger.info('Incoming proxy connection from %s' % repr(addr))
            handler = ProxyChannel(server, config.ENVISALINKPROXYPASS, sock, addr)
"""
