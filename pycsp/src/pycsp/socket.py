"""
CSP Socket API with IPC Protocol

This module implements the CSP socket API that communicates with the pycsp-muxd
daemon via TCP-based IPC. The socket API mirrors the standard Python socket interface
while providing CSP-specific functionality.

IPC Wire Protocol:
    Message Header: [Type (1B)] + [Length (3B, big-endian)]
    Message Types: REGISTER, REGISTER_ACK, UNREGISTER, DATA, ERROR
"""

import socket as socket_stdlib
import struct
import threading
import queue
import time
import random
import logging
import os
from enum import IntEnum
from typing import Optional

from .packet import Packet
from .rdp import RDPState, ConnectionState, register_rdp_connection, unregister_rdp_connection

# Setup logging
logger = logging.getLogger(__name__)

# Address families
AF_UNSPEC = 0
AF_CSP_V1 = 8001
AF_CSP_V2 = 8002

# Socket types
SOCK_RAW = 3
SOCK_DGRAM = 2           # CSP UDP (connectionless)
SOCK_SEQPACKET = 5       # CSP RDP (connection-oriented)

# Socket flags
SOCK_NONBLOCK = 0x800
SOCK_CLOEXEC = 0x80000

# Protocols
CSPPROTO_RAW = 255
CSPPROTO_RDP = 1
CSPPROTO_UDP = 0

# Socket options
CSP_SO_NONE = 0x0000
CSP_SO_RDPREQ = 0x0001        # Require RDP
CSP_SO_RDPPROHIB = 0x0002     # Prohibit RDP
CSP_SO_HMACREQ = 0x0004       # Require HMAC
CSP_SO_HMACPROHIB = 0x0008    # Prohibit HMAC
CSP_SO_CRC32REQ = 0x0040      # Require CRC32
CSP_SO_CRC32PROHIB = 0x0080   # Prohibit CRC32
CSP_SO_CONN_LESS = 0x0100     # Connectionless mode

# Connection options (aliases)
CSP_O_NONE = CSP_SO_NONE
CSP_O_RDP = CSP_SO_RDPREQ
CSP_O_HMAC = CSP_SO_HMACREQ
CSP_O_CRC32 = CSP_SO_CRC32REQ

# Socket option levels
SOL_CSP = 0x29A  # CSP-specific options

# IPC Message types
MSG_REGISTER = 0x01
MSG_REGISTER_ACK = 0x02
MSG_UNREGISTER = 0x03
MSG_DATA = 0x04
MSG_ERROR = 0xFF

# Constants
CSP_ANY = 255  # Wildcard port


class SocketState(IntEnum):
    """Socket state."""
    CLOSED = 0
    BOUND = 1
    LISTENING = 2
    CONNECTED = 3


class ConnectionType(IntEnum):
    """Connection type."""
    CLIENT = 0
    SERVER = 1


class IPCMessage:
    """IPC protocol message."""

    def __init__(self, msg_type: int, payload: bytes = b''):
        """
        Initialize IPC message.

        Args:
            msg_type: Message type
            payload: Message payload
        """
        self.msg_type = msg_type
        self.payload = payload

    def encode(self) -> bytes:
        """
        Encode message to wire format.

        Format: [Type (1B)] + [Length (3B)] + [Payload]
        """
        length = len(self.payload)
        if length > 0xFFFFFF:
            raise ValueError(f"Payload too large: {length}")

        # Pack: type (1B) + length (3B, big-endian) = 4 bytes header
        header = struct.pack('>I', (self.msg_type << 24) | length)
        return header + self.payload

    @classmethod
    def decode(cls, data: bytes) -> 'IPCMessage':
        """
        Decode message from wire format.

        Args:
            data: Raw bytes

        Returns:
            IPCMessage: Decoded message
        """
        if len(data) < 4:
            raise ValueError("Message too short")

        # Unpack header
        header_int = struct.unpack('>I', data[:4])[0]
        msg_type = (header_int >> 24) & 0xFF
        length = header_int & 0xFFFFFF

        # Extract payload
        payload = data[4:4+length] if length > 0 else b''

        return cls(msg_type, payload)

    def __repr__(self):
        type_names = {
            MSG_REGISTER: 'REGISTER',
            MSG_REGISTER_ACK: 'REGISTER_ACK',
            MSG_UNREGISTER: 'UNREGISTER',
            MSG_DATA: 'DATA',
            MSG_ERROR: 'ERROR'
        }
        type_name = type_names.get(self.msg_type, f'UNKNOWN({self.msg_type})')
        return f"IPCMessage({type_name}, len={len(self.payload)})"


class IPCConnection:
    """Manages TCP connection to daemon."""

    def __init__(self, host=None, port=None):
        """
        Initialize IPC connection.

        Args:
            host: Daemon host (default: from env or '127.0.0.1')
            port: Daemon port (default: from env or 9701)
        """
        self.host = host or os.getenv('PYCSP_DAEMON_HOST', '127.0.0.1')
        self.port = port or int(os.getenv('PYCSP_DAEMON_PORT', '9701'))
        self.sock = None
        self.lock = threading.Lock()

    def connect(self):
        """Connect to daemon."""
        self.sock = socket_stdlib.socket(socket_stdlib.AF_INET, socket_stdlib.SOCK_STREAM)
        self.sock.connect((self.host, self.port))
        logger.debug(f"IPC: Connected to daemon at {self.host}:{self.port}")

    def send_message(self, msg: IPCMessage):
        """
        Send IPC message to daemon.

        Args:
            msg: Message to send
        """
        with self.lock:
            data = msg.encode()
            self.sock.sendall(data)
            logger.debug(f"IPC: Sent {msg}")

    def recv_message(self, timeout=None) -> IPCMessage:
        """
        Receive IPC message from daemon.

        Args:
            timeout: Timeout in seconds

        Returns:
            IPCMessage: Received message
        """
        if timeout is not None:
            self.sock.settimeout(timeout)

        # Read 4-byte header
        header = self._recv_exact(4)
        header_int = struct.unpack('>I', header)[0]
        msg_type = (header_int >> 24) & 0xFF
        length = header_int & 0xFFFFFF

        # Read payload
        payload = self._recv_exact(length) if length > 0 else b''

        msg = IPCMessage(msg_type, payload)
        logger.debug(f"IPC: Received {msg}")
        return msg

    def _recv_exact(self, n: int) -> bytes:
        """
        Receive exactly n bytes.

        Args:
            n: Number of bytes to receive

        Returns:
            bytes: Received data
        """
        data = bytearray()
        while len(data) < n:
            chunk = self.sock.recv(n - len(data))
            if not chunk:
                raise ConnectionError("Connection closed by daemon")
            data.extend(chunk)
        return bytes(data)

    def close(self):
        """Close IPC connection."""
        if self.sock:
            try:
                self.sock.close()
            except:
                pass
            self.sock = None
            logger.debug("IPC: Connection closed")


class Connection:
    """
    CSP Connection object (similar to libcsp csp_conn_t).

    Represents a connection between two CSP endpoints.
    """

    def __init__(self, src_addr: int, src_port: int, dst_addr: int, dst_port: int,
                 conn_type: ConnectionType, opts: int = 0, socket_obj=None):
        """
        Initialize connection.

        Args:
            src_addr: Source CSP address
            src_port: Source CSP port
            dst_addr: Destination CSP address
            dst_port: Destination CSP port
            conn_type: CLIENT or SERVER
            opts: Connection options (RDP, HMAC, CRC32)
            socket_obj: Parent socket object
        """
        # Connection identifiers
        self.src_addr = src_addr
        self.src_port = src_port
        self.dst_addr = dst_addr
        self.dst_port = dst_port

        # Connection state
        self.state = ConnectionState.CLOSED
        self.type = conn_type
        self.opts = opts

        # Parent socket reference
        self.socket = socket_obj

        # RX queue for incoming packets
        self.rx_queue = queue.Queue(maxsize=32)

        # RDP state (if enabled)
        self.rdp_state = None
        if opts & CSP_O_RDP:
            self.rdp_state = RDPState(self)
            register_rdp_connection(self.rdp_state)

        # Timestamp
        self.timestamp = time.time()

        # Thread safety
        self._lock = threading.RLock()

        logger.debug(f"Connection created: {self.src_addr}:{self.src_port} → {self.dst_addr}:{self.dst_port}")

    def send(self, data: bytes, timeout=1.0):  # noqa: ARG002
        """
        Send data on this connection.

        Args:
            data: Data to send
            timeout: Timeout in seconds

        Returns:
            int: Number of bytes sent, or -1 on error
        """
        if self.state != ConnectionState.OPEN:
            logger.error(f"Connection not open (state={self.state})")
            return -1

        # If RDP, use RDP layer
        if self.opts & CSP_O_RDP and self.rdp_state:
            success = self.rdp_state.send(data)
            return len(data) if success else -1

        # Connectionless: build and send packet directly
        try:
            pkt = Packet(
                src=self.src_addr,
                dst=self.dst_addr,
                sport=self.src_port,
                dport=self.dst_port,
                payload=data,
                rdp=False,
                hmac_key=getattr(self.socket, 'hmac_key', None) if self.socket else None,
                crc=(self.opts & CSP_O_CRC32) != 0
            )

            frame = pkt.encode()

            # Send via IPC
            if self.socket and hasattr(self.socket, '_ipc_conn'):
                msg = IPCMessage(MSG_DATA, frame)
                self.socket._ipc_conn.send_message(msg)
                return len(data)
            else:
                logger.error("No IPC connection available")
                return -1

        except Exception as e:
            logger.error(f"Send error: {e}")
            return -1

    def recv(self, timeout=1.0) -> Optional[bytes]:
        """
        Receive data from connection.

        Args:
            timeout: Timeout in seconds

        Returns:
            bytes: Received data, or None on timeout/error
        """
        if self.state != ConnectionState.OPEN:
            logger.error(f"Connection not open (state={self.state})")
            return None

        try:
            pkt = self.rx_queue.get(timeout=timeout)

            # If RDP, process through RDP layer
            if self.opts & CSP_O_RDP and self.rdp_state:
                data = self.rdp_state.recv(pkt)
                return data

            # Connectionless: return payload directly
            return pkt.payload

        except queue.Empty:
            return None
        except Exception as e:
            logger.error(f"Recv error: {e}")
            return None

    def close(self):
        """Close the connection."""
        if self.state == ConnectionState.CLOSED:
            return

        # If RDP, send close handshake
        if self.opts & CSP_O_RDP and self.rdp_state:
            self.rdp_state.close()
            unregister_rdp_connection(self.rdp_state)

        # Unregister port from daemon (client mode only)
        if self.type == ConnectionType.CLIENT and self.socket:
            try:
                payload = struct.pack('>HBx', self.src_addr, self.src_port)
                msg = IPCMessage(MSG_UNREGISTER, payload)
                if hasattr(self.socket, '_ipc_conn') and self.socket._ipc_conn:
                    self.socket._ipc_conn.send_message(msg)
            except:
                pass

        # Remove from socket's connection pool
        if self.socket:
            with self.socket._conn_lock:
                key = (self.src_port,)
                self.socket._connections.pop(key, None)

        self.state = ConnectionState.CLOSED
        logger.debug(f"Connection closed: {self.src_addr}:{self.src_port} → {self.dst_addr}:{self.dst_port}")

    def __repr__(self):
        return f"Connection({self.src_addr}:{self.src_port} → {self.dst_addr}:{self.dst_port}, state={self.state.name})"


class socket:
    """
    CSP Socket (similar to standard Python socket).

    Provides socket-like API for CSP communication via daemon IPC.
    """

    def __init__(self, family=-1, type=-1, proto=-1):
        """
        Create a CSP socket.

        Args:
            family: Address family (default: AF_CSP_V1)
            type: Socket type (default: SOCK_DGRAM)
            proto: Protocol (default: 0)
        """
        if family == -1:
            family = AF_CSP_V1
        if type == -1:
            type = SOCK_DGRAM
        if proto == -1:
            proto = 0

        self.family = family
        self.type = type
        self.proto = proto

        # Socket state
        self.state = SocketState.CLOSED
        self._closed = False

        # Binding info
        self.bound_addr = None
        self.bound_port = None

        # Socket options
        self.opts = CSP_SO_NONE
        self.hmac_key = None

        # IPC connection to daemon
        self._ipc_conn = None
        self._ipc_lock = threading.RLock()

        # RX queue for incoming connections (listening sockets)
        self.rx_queue = queue.Queue(maxsize=10)

        # Active connections pool
        self._connections = {}
        self._conn_lock = threading.RLock()

        # Receiver thread
        self._rx_thread = None
        self._rx_running = False

        # Timeout settings
        self._timeout = None
        self._blocking = True

        # Ephemeral port allocation
        self._ephemeral_port = None
        self._ephemeral_range = (32, 63)

        logger.debug(f"Socket created (family={family}, type={type})")

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, *args):
        """Context manager exit."""
        if not self._closed:
            self.close()

    def bind(self, address):
        """
        Bind socket to CSP address and port.

        Args:
            address: (csp_addr, csp_port) tuple or just csp_port
                    If csp_addr is 0 or None, daemon assigns its address
        """
        # Parse address
        if isinstance(address, tuple):
            csp_addr, csp_port = address
        elif isinstance(address, int):
            csp_addr = 0
            csp_port = address
        else:
            raise ValueError("Address must be (addr, port) tuple or port integer")

        # Connect to daemon if not already connected
        if self._ipc_conn is None:
            self._connect_to_daemon()

        # Send MSG_REGISTER
        payload = struct.pack('>HBx', csp_addr, csp_port)
        msg = IPCMessage(MSG_REGISTER, payload)
        self._ipc_conn.send_message(msg)

        # Wait for MSG_REGISTER_ACK
        try:
            ack_msg = self._ipc_conn.recv_message(timeout=5.0)
            if ack_msg.msg_type != MSG_REGISTER_ACK:
                raise RuntimeError(f"Unexpected response from daemon: {ack_msg}")

            status, assigned_addr = struct.unpack('>BHx', ack_msg.payload)
            if status != 0:
                error_msgs = {1: "Port in use", 2: "Invalid request"}
                raise OSError(f"Bind failed: {error_msgs.get(status, f'status={status}')}")

            self.bound_addr = assigned_addr
            self.bound_port = csp_port
            self.state = SocketState.BOUND

            logger.info(f"Socket bound to {assigned_addr}:{csp_port}")

            # Start RX thread
            self._start_rx_thread()

        except socket_stdlib.timeout:
            raise TimeoutError("Daemon did not respond to registration")

    def listen(self, backlog=10):
        """
        Mark socket as listening for connections.

        Args:
            backlog: Maximum number of queued connections
        """
        if self.state != SocketState.BOUND:
            raise OSError("Socket must be bound before listen")

        self.rx_queue = queue.Queue(maxsize=backlog)
        self.state = SocketState.LISTENING
        logger.info(f"Socket listening on {self.bound_addr}:{self.bound_port}")

    def accept(self, timeout=None) -> Optional[Connection]:
        """
        Accept incoming connection.

        Args:
            timeout: Timeout in seconds (None = blocking)

        Returns:
            Connection: New connection, or None on timeout
        """
        if self.state != SocketState.LISTENING:
            raise OSError("Socket must be listening")

        try:
            conn = self.rx_queue.get(timeout=timeout)
            logger.info(f"Accepted connection: {conn}")
            return conn
        except queue.Empty:
            return None

    def connect(self, address, opts=0):
        """
        Connect to remote CSP endpoint.

        Args:
            address: (dst_addr, dst_port) tuple
            opts: Connection options (CSP_O_RDP, CSP_O_HMAC, etc.)

        Returns:
            Connection: New connection object
        """
        dst_addr, dst_port = address

        # Connect to daemon if not already
        if self._ipc_conn is None:
            self._connect_to_daemon()

        # Allocate ephemeral source port
        src_port = self._allocate_ephemeral_port()

        # Register ephemeral port with daemon
        payload = struct.pack('>HBx', 0, src_port)
        msg = IPCMessage(MSG_REGISTER, payload)
        self._ipc_conn.send_message(msg)

        # Wait for ACK
        ack_msg = self._ipc_conn.recv_message(timeout=5.0)
        if ack_msg.msg_type != MSG_REGISTER_ACK:
            raise RuntimeError(f"Unexpected response: {ack_msg}")

        status, assigned_addr = struct.unpack('>BHx', ack_msg.payload)
        if status != 0:
            raise OSError(f"Registration failed: status={status}")

        # Start RX thread if not running
        if not self._rx_running:
            self._start_rx_thread()

        # Create connection object
        conn = Connection(
            assigned_addr, src_port,
            dst_addr, dst_port,
            ConnectionType.CLIENT,
            opts,
            socket_obj=self
        )

        # Store connection in pool
        with self._conn_lock:
            self._connections[(src_port,)] = conn

        # If RDP enabled, perform 3-way handshake
        if opts & CSP_O_RDP:
            if not conn.rdp_state.connect(timeout=5.0):
                conn.close()
                raise ConnectionError("RDP handshake failed")

        conn.state = ConnectionState.OPEN
        logger.info(f"Connected: {conn}")
        return conn

    def close(self):
        """Close socket and cleanup resources."""
        if self._closed:
            return

        self._closed = True

        # Stop RX thread
        self._rx_running = False
        if self._rx_thread and self._rx_thread.is_alive():
            self._rx_thread.join(timeout=1.0)

        # Unregister from daemon
        if self.bound_port is not None and self._ipc_conn:
            try:
                payload = struct.pack('>HBx', self.bound_addr or 0, self.bound_port)
                msg = IPCMessage(MSG_UNREGISTER, payload)
                self._ipc_conn.send_message(msg)
            except:
                pass

        # Close all connections
        with self._conn_lock:
            for conn in list(self._connections.values()):
                try:
                    conn.close()
                except:
                    pass

        # Close IPC connection
        if self._ipc_conn:
            self._ipc_conn.close()
            self._ipc_conn = None

        self.state = SocketState.CLOSED
        logger.info("Socket closed")

    def _connect_to_daemon(self):
        """Connect to daemon with retry logic."""
        max_retries = 3
        retry_delay = 0.5

        for attempt in range(max_retries):
            try:
                self._ipc_conn = IPCConnection()
                self._ipc_conn.connect()
                return
            except Exception as e:
                if attempt < max_retries - 1:
                    logger.warning(f"Daemon connection failed (attempt {attempt+1}/{max_retries}): {e}")
                    time.sleep(retry_delay)
                    retry_delay *= 2
                else:
                    raise ConnectionError(f"Cannot connect to daemon: {e}")

    def _start_rx_thread(self):
        """Start background thread to receive packets from daemon."""
        if self._rx_running:
            return

        self._rx_running = True
        self._rx_thread = threading.Thread(target=self._rx_worker, daemon=True)
        self._rx_thread.start()
        logger.debug("RX thread started")

    def _rx_worker(self):
        """Background thread that receives MSG_DATA from daemon."""
        while self._rx_running:
            try:
                # Receive message from daemon
                msg = self._ipc_conn.recv_message(timeout=0.1)

                if msg.msg_type != MSG_DATA:
                    logger.warning(f"Unexpected message type: {msg}")
                    continue

                # Parse CSP packet
                try:
                    pkt = Packet()
                    pkt.decode(msg.payload)
                except Exception as e:
                    logger.error(f"Packet decode error: {e}")
                    continue

                # Route packet
                if self.state == SocketState.LISTENING:
                    # Server mode: check if this is a new connection
                    if pkt.header.rdp:
                        self._handle_incoming_rdp(pkt)
                    else:
                        self._handle_incoming_dgram(pkt)
                else:
                    # Client mode: route to existing connection
                    self._route_to_connection(pkt)

            except socket_stdlib.timeout:
                continue
            except Exception as e:
                if self._rx_running:
                    logger.error(f"RX thread error: {e}")
                break

        logger.debug("RX thread stopped")

    def _handle_incoming_rdp(self, pkt: Packet):
        """Handle incoming RDP packet for listening socket."""
        from .rdp import RDPHeader, RDP_SYN

        try:
            # Parse RDP header
            if len(pkt.payload) < 5:
                return

            rdp_hdr = RDPHeader.decode(pkt.payload)

            # Check for SYN (new connection)
            if rdp_hdr.flags & RDP_SYN:
                # Create new server connection
                conn = Connection(
                    self.bound_addr, self.bound_port,
                    pkt.header.src, pkt.header.sport,
                    ConnectionType.SERVER,
                    CSP_O_RDP,
                    socket_obj=self
                )
                conn.state = ConnectionState.SYN_RCVD

                # Handle SYN
                conn.rdp_state.handle_syn(pkt)

                # Queue connection for accept()
                try:
                    self.rx_queue.put(conn, block=False)
                except queue.Full:
                    logger.warning("Accept queue full, dropping connection")
                    conn.close()
            else:
                # Route to existing connection
                self._route_to_connection(pkt)

        except Exception as e:
            logger.error(f"RDP handling error: {e}")

    def _handle_incoming_dgram(self, pkt: Packet):
        """Handle incoming datagram for listening socket."""
        # For connectionless, create ephemeral connection
        # TODO: Implement connectionless server mode
        self._route_to_connection(pkt)

    def _route_to_connection(self, pkt: Packet):
        """Route packet to appropriate connection."""
        # Find connection by destination port
        key = (pkt.header.dport,)

        with self._conn_lock:
            conn = self._connections.get(key)

        if conn:
            try:
                conn.rx_queue.put(pkt, block=False)
            except queue.Full:
                logger.warning(f"Connection RX queue full, dropping packet")
        else:
            logger.debug(f"No connection for packet: {pkt.header.src}:{pkt.header.sport} → {pkt.header.dst}:{pkt.header.dport}")

    def _allocate_ephemeral_port(self) -> int:
        """Allocate ephemeral port from range."""
        if self._ephemeral_port is None:
            start, end = self._ephemeral_range
            self._ephemeral_port = random.randint(start, end)
        return self._ephemeral_port

    # Timeout and blocking methods
    def setblocking(self, flag):
        """Set blocking mode."""
        self._blocking = flag
        self._timeout = None if flag else 0

    def getblocking(self):
        """Get blocking mode."""
        return self._blocking

    def settimeout(self, value):
        """Set timeout in seconds (None = blocking, 0 = non-blocking)."""
        self._timeout = value
        if value == 0:
            self._blocking = False
        elif value is None:
            self._blocking = True

    def gettimeout(self):
        """Get timeout."""
        return self._timeout

    def setsockopt(self, level, optname, value):
        """Set socket option."""
        if level == SOL_CSP:
            if optname == CSP_SO_RDPREQ:
                self.opts |= CSP_SO_RDPREQ
            elif optname == CSP_SO_HMACREQ:
                self.opts |= CSP_SO_HMACREQ
                if isinstance(value, bytes):
                    self.hmac_key = value
            elif optname == CSP_SO_CRC32REQ:
                self.opts |= CSP_SO_CRC32REQ

    def getsockopt(self, level, optname, buflen=None):
        """Get socket option."""
        if level == SOL_CSP:
            return bool(self.opts & optname)
        return None

    def getsockname(self):
        """Get socket name."""
        return (self.bound_addr, self.bound_port)

    # Stub methods (not implemented for CSP)
    def shutdown(self, how):
        """Shutdown (not implemented)."""
        pass

    def connect_ex(self, address):
        """Connect (error code return) - not implemented."""
        pass

    def recvfrom(self, bufsize):
        """Receive from (not implemented)."""
        pass

    def sendto(self, data, flags, address):  # noqa: ARG002
        """Send to (not implemented)."""
        pass

    def __repr__(self):
        return f"socket(family={self.family}, type={self.type}, state={self.state.name})"


# Helper functions
def create_connection(address, timeout=None, source_address=None, opts=0):
    """
    High-level helper to connect to CSP address.

    Args:
        address: (dst_addr, dst_port) tuple
        timeout: Connection timeout
        source_address: Source address binding (optional)
        opts: Connection options

    Returns:
        Connection: Connected connection object
    """
    sock = socket(AF_CSP_V1, SOCK_SEQPACKET if (opts & CSP_O_RDP) else SOCK_DGRAM)

    if source_address:
        sock.bind(source_address)

    if timeout:
        sock.settimeout(timeout)

    conn = sock.connect(address, opts=opts)
    return conn


def create_server(address, family=AF_CSP_V1, backlog=10):
    """
    High-level helper to create listening server.

    Args:
        address: (csp_addr, csp_port) or just csp_port
        family: Address family
        backlog: Listen backlog

    Returns:
        socket: Listening socket
    """
    sock = socket(family, SOCK_SEQPACKET)
    sock.bind(address)
    sock.listen(backlog)
    return sock
