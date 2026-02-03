from .packet import Packet
from .link import Interface
from enum import IntEnum

AF_UNSPEC = 0
AF_CSP_V1 = 8001
AF_CSP_V2 = 8002

SOCK_RAW = 3
SOCK_DGRAM = 2           # CSP UDP
SOCK_SEQPACKET = 5       # CSP RDP

SOCK_NONBLOCK = 0x800
SOCK_CLOEXEC = 0x80000

CSPPROTO_RAW = 255
CSPPROTO_RDP = 1
CSPPROTO_UDP = 0

IntEnum._convert_(
        'AddressFamily',
        __name__,
        lambda C: C.isupper() and C.startswith('AF_'))

IntEnum._convert_(
        'SocketKind',
        __name__,
        lambda C: C.isupper() and C.startswith('SOCK_'))
    
def create_connection(address, timeout, source_address, 
                        *args, all_errors=False):
    pass

def create_server(address, *args, family=AF_CSP_V1, backlog=None, reuse_port=False):
    pass

class socket:
    def __init__(self, family=-1, type=-1, proto=-1):
        if family == -1:
            family = AF_CSP_V1
        if type == -1:
            type = SOCK_DGRAM
        if proto == -1:
            proto = 0
        self._closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        if not self._closed:
            self.close()


    def accept(self):
        pass

    def bind(self, address):
        pass
    
    def close(self):
        pass

    def shutdown(self, how):
        pass

    def connect(self, address):
        pass

    def connect_ex(self, address):
        pass

    def getblocking(self):
        pass

    def setblocking(self, flag):
        pass

    def gettimeout(self):
        pass

    def settimeout(self, value):
        pass

    def getdefaulttimeout(self):
        pass

    def getsockopt(self, level, optname, buflen):
        pass

    def setsockopt(self, level, optname, value):
        pass

    def getsockname(self):
        pass

    def listen(self, backlog):
        pass

    def recvfrom(self, bufsize):
        pass

    def recvmsg(self, bufsize, ancbufsize, flags):
        pass

    def sendto(self, string:str, flags, address):
        pass

    def sendmsg(self, buffers, ancdata, flags, address):
        pass

