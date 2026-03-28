"""
CSP Transport Layer - TCP/IP Implementation

This module implements the TcpTun transport interface for CSP packet transmission
over TCP with length-prefixed framing.

Wire Protocol:
    TCP Frame: [Length (4B, big-endian)] + [CSP Packet (raw bytes)]
"""

import socket as socket_stdlib
import threading
import queue
import time
import struct
import logging
from typing import Optional

from .packet import Packet
from .link import Interface

# Setup logging
logger = logging.getLogger(__name__)


class TcpTun(Interface):
    """
    TCP-based CSP transport interface with length-prefixed framing.

    Supports both client and server modes:
    - Client mode: Connects to remote TCP server
    - Server mode: Listens for one client connection

    Features:
    - Length-prefixed framing (4-byte big-endian)
    - Background RX thread for packet reception
    - Automatic reconnection with exponential backoff (client mode)
    - Thread-safe send/recv operations
    """

    def __init__(self, name='tcptun0', addr='127.0.0.1', port=52001,
                 server=False, mtu=65536, timeout=1.0, auto_reconnect=True):
        """
        Initialize TCP transport interface.

        Args:
            name: Interface name
            addr: IP address (bind address for server, remote address for client)
            port: TCP port number
            server: True for server mode, False for client mode
            mtu: Maximum transmission unit in bytes
            timeout: Socket timeout in seconds
            auto_reconnect: Enable automatic reconnection (client mode only)
        """
        self.name = name
        self.addr = addr
        self.port = port
        self.server = server
        self.mtu = mtu
        self.timeout = timeout
        self.auto_reconnect = auto_reconnect

        # Socket management
        self.sock = None  # Primary socket (listener in server mode)
        self.conn = None  # Active connection socket
        self.connected = False

        # Threading
        self.rx_queue = queue.Queue(maxsize=1024)
        self.rx_thread = None
        self.running = False

        # Thread safety
        self.send_lock = threading.Lock()
        self.recv_lock = threading.Lock()

        # Reconnection control
        self.reconnect_delay = 1.0
        self.max_reconnect_delay = 30.0

        # Statistics
        self.tx_count = 0
        self.rx_count = 0
        self.tx_errors = 0
        self.rx_errors = 0

        # Initialize connection
        self._initialize()

    def _initialize(self):
        """Initialize the transport (connect or listen)."""
        self.running = True

        if self.server:
            self._start_server()
        else:
            self._connect_client()

        # Start RX thread
        self.rx_thread = threading.Thread(target=self._rx_loop, daemon=True)
        self.rx_thread.start()

        logger.info(f"{self.name}: Initialized ({'server' if self.server else 'client'} mode, {self.addr}:{self.port})")

    def _connect_client(self):
        """Connect to remote TCP server (client mode)."""
        try:
            self.sock = socket_stdlib.socket(socket_stdlib.AF_INET, socket_stdlib.SOCK_STREAM)
            self.sock.settimeout(self.timeout)
            self.sock.connect((self.addr, self.port))
            self.conn = self.sock
            self.connected = True
            self.reconnect_delay = 1.0  # Reset backoff
            logger.info(f"{self.name}: Connected to {self.addr}:{self.port}")
            return True
        except Exception as e:
            self.connected = False
            logger.error(f"{self.name}: Connection failed: {e}")
            return False

    def _start_server(self):
        """Start TCP server and wait for client (server mode)."""
        try:
            self.sock = socket_stdlib.socket(socket_stdlib.AF_INET, socket_stdlib.SOCK_STREAM)
            self.sock.setsockopt(socket_stdlib.SOL_SOCKET, socket_stdlib.SO_REUSEADDR, 1)
            self.sock.bind((self.addr, self.port))
            self.sock.listen(1)
            self.sock.settimeout(1.0)  # Non-blocking accept with timeout
            logger.info(f"{self.name}: Listening on {self.addr}:{self.port}")
        except Exception as e:
            logger.error(f"{self.name}: Failed to start server: {e}")
            raise

    def _accept_client(self):
        """Accept one client connection (server mode)."""
        try:
            conn, addr = self.sock.accept()
            conn.settimeout(self.timeout)

            # Close old connection if exists
            if self.conn:
                try:
                    self.conn.close()
                except:
                    pass

            self.conn = conn
            self.connected = True
            logger.info(f"{self.name}: Accepted connection from {addr}")
            return True
        except socket_stdlib.timeout:
            return False
        except Exception as e:
            logger.error(f"{self.name}: Accept failed: {e}")
            return False

    def _rx_loop(self):
        """Background thread for receiving packets."""
        logger.debug(f"{self.name}: RX thread started")

        while self.running:
            try:
                # Server mode: accept client if not connected
                if self.server and not self.connected:
                    self._accept_client()
                    continue

                # Client mode: reconnect if not connected
                if not self.server and not self.connected:
                    if self.auto_reconnect:
                        if self._reconnect_with_backoff():
                            continue
                        else:
                            time.sleep(0.1)
                            continue
                    else:
                        time.sleep(0.1)
                        continue

                # Receive frame
                if self.conn:
                    frame = self._recv_frame(self.conn)

                    if frame is None:
                        # Connection closed or error
                        self._handle_disconnect()
                        continue

                    # Parse packet
                    try:
                        pkt = Packet()
                        pkt.decode(frame)
                        self.rx_queue.put(pkt, block=False)
                        self.rx_count += 1
                    except queue.Full:
                        logger.warning(f"{self.name}: RX queue full, dropping packet")
                        self.rx_errors += 1
                    except Exception as e:
                        logger.error(f"{self.name}: Packet decode error: {e}")
                        self.rx_errors += 1

            except Exception as e:
                if self.running:
                    logger.error(f"{self.name}: RX loop error: {e}")
                    self._handle_disconnect()
                time.sleep(0.1)

        logger.debug(f"{self.name}: RX thread stopped")

    def _recv_frame(self, sock):
        """
        Receive one length-prefixed frame from socket.

        Args:
            sock: Socket to receive from

        Returns:
            bytes: Frame data, or None on error/disconnect
        """
        try:
            # Read 4-byte length prefix
            length_bytes = self._recv_exact(sock, 4)
            if not length_bytes:
                return None

            length = struct.unpack('>I', length_bytes)[0]

            # Validate length
            if length == 0 or length > self.mtu:
                logger.error(f"{self.name}: Invalid frame length: {length}")
                return None

            # Read frame data
            data = self._recv_exact(sock, length)
            if not data or len(data) != length:
                return None

            return data

        except socket_stdlib.timeout:
            return None
        except Exception as e:
            logger.debug(f"{self.name}: Recv frame error: {e}")
            return None

    def _recv_exact(self, sock, n):
        """
        Receive exactly n bytes from socket.

        Args:
            sock: Socket to receive from
            n: Number of bytes to receive

        Returns:
            bytes: Received data, or None if connection closed
        """
        data = bytearray()
        while len(data) < n:
            try:
                chunk = sock.recv(n - len(data))
                if not chunk:
                    return None  # Connection closed
                data.extend(chunk)
            except socket_stdlib.timeout:
                if len(data) > 0:
                    continue  # Partial data, keep trying
                raise
        return bytes(data)

    def _send_frame(self, sock, data):
        """
        Send one length-prefixed frame to socket.

        Args:
            sock: Socket to send to
            data: Frame data to send

        Returns:
            bool: True on success, False on failure
        """
        try:
            length = len(data)
            if length > self.mtu:
                logger.error(f"{self.name}: Packet exceeds MTU: {length} > {self.mtu}")
                return False

            # Prepend length
            frame = struct.pack('>I', length) + data

            # Send complete frame (handle partial sends)
            sent = 0
            while sent < len(frame):
                n = sock.send(frame[sent:])
                if n == 0:
                    raise ConnectionError("Socket connection broken")
                sent += n

            return True

        except Exception as e:
            logger.error(f"{self.name}: Send frame error: {e}")
            return False

    def _handle_disconnect(self):
        """Handle connection disconnect."""
        self.connected = False

        if self.conn:
            try:
                self.conn.close()
            except:
                pass
            self.conn = None

        logger.info(f"{self.name}: Disconnected")

    def _reconnect_with_backoff(self):
        """
        Attempt to reconnect with exponential backoff.

        Returns:
            bool: True if reconnected successfully
        """
        if not self.running:
            return False

        # Try to connect
        if self._connect_client():
            return True

        # Exponential backoff
        time.sleep(self.reconnect_delay)
        self.reconnect_delay = min(self.reconnect_delay * 2, self.max_reconnect_delay)

        return False

    def reconnect(self):
        """Force reconnection (client mode only)."""
        if self.server:
            logger.warning(f"{self.name}: Cannot reconnect in server mode")
            return False

        self._handle_disconnect()
        self.reconnect_delay = 1.0
        return self._connect_client()

    def send(self, pkt):
        """
        Send a CSP packet over the transport.

        Args:
            pkt: Packet object to send

        Returns:
            bool: True on success, False on failure
        """
        if not isinstance(pkt, Packet):
            logger.error(f"{self.name}: Invalid packet type")
            return False

        with self.send_lock:
            # Check if connected
            if not self.connected or not self.conn:
                if self.auto_reconnect and not self.server:
                    logger.debug(f"{self.name}: Not connected, attempting reconnect...")
                    if not self._connect_client():
                        self.tx_errors += 1
                        return False
                else:
                    self.tx_errors += 1
                    return False

            # Encode packet
            try:
                frame = pkt.encode()
            except Exception as e:
                logger.error(f"{self.name}: Packet encode error: {e}")
                self.tx_errors += 1
                return False

            # Send frame
            if self._send_frame(self.conn, frame):
                self.tx_count += 1
                return True
            else:
                self.tx_errors += 1
                self._handle_disconnect()
                return False

    def recv(self, timeout=None):
        """
        Receive a CSP packet from the transport.

        Args:
            timeout: Timeout in seconds (None = blocking, 0 = non-blocking)

        Returns:
            Packet: Received packet, or None on timeout
        """
        try:
            if timeout == 0:
                return self.rx_queue.get(block=False)
            else:
                return self.rx_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self):
        """Close the transport and stop all threads."""
        logger.info(f"{self.name}: Closing")

        self.running = False

        # Wait for RX thread to stop
        if self.rx_thread and self.rx_thread.is_alive():
            self.rx_thread.join(timeout=2.0)

        # Close sockets
        if self.conn:
            try:
                self.conn.close()
            except:
                pass

        if self.sock and self.sock != self.conn:
            try:
                self.sock.close()
            except:
                pass

        self.connected = False
        logger.info(f"{self.name}: Closed")

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()

    def __repr__(self):
        """String representation."""
        mode = "server" if self.server else "client"
        status = "connected" if self.connected else "disconnected"
        return f"TcpTun({self.name}, {mode}, {self.addr}:{self.port}, {status})"
