"""
PyCSP Daemon (pycsp-muxd)

Central multiplexer daemon that handles packet routing between IPC clients
and network transport interfaces.

Architecture:
    - IPC Server: TCP socket for client connections
    - Transport: TcpTun interface for CSP network
    - App Table: Maps (csp_addr, csp_port) → IPC client socket
    - Route Table: Maps csp_addr → transport interface
    - Event Loop: select() for multiplexing IPC and transport
"""

import socket as socket_stdlib
import select
import struct
import logging
import argparse
import signal
import sys
from typing import Dict, Tuple, Optional, List

from .packet import Packet, HeaderV1
from .transport import TcpTun
from .route import Route

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# IPC Message types (matching socket.py)
MSG_REGISTER = 0x01
MSG_REGISTER_ACK = 0x02
MSG_UNREGISTER = 0x03
MSG_DATA = 0x04
MSG_ERROR = 0xFF

# Constants
CSP_ANY = 255


class Daemon:
    """
    PyCSP Daemon - Central packet router and multiplexer.

    Handles:
    - IPC client registration and message routing
    - Network packet routing via transport interfaces
    - App table management (port registration)
    - Loopback routing for local communication
    """

    def __init__(self, local_addr=10, ipc_host='127.0.0.1', ipc_port=9701):
        """
        Initialize daemon.

        Args:
            local_addr: Local CSP address
            ipc_host: IPC server bind address
            ipc_port: IPC server bind port
        """
        self.local_addr = local_addr
        self.ipc_host = ipc_host
        self.ipc_port = ipc_port

        # App registration table: (csp_addr, csp_port) → client socket
        self.app_table: Dict[Tuple[int, int], socket_stdlib.socket] = {}

        # IPC client registry: client socket → (remote_ip, remote_port)
        self.ipc_clients: Dict[socket_stdlib.socket, Tuple[str, int]] = {}

        # Route table
        self.route = Route(local_addr=local_addr)

        # Transport interfaces
        self.interfaces: List[TcpTun] = []

        # IPC server socket
        self.ipc_server = None

        # Running flag
        self.running = False

        # Statistics
        self.stats = {
            'rx_packets': 0,
            'tx_packets': 0,
            'rx_errors': 0,
            'tx_errors': 0,
            'ipc_connects': 0,
            'ipc_disconnects': 0
        }

        logger.info(f"Daemon initialized (local_addr={local_addr}, IPC={ipc_host}:{ipc_port})")

    def add_interface(self, iface: TcpTun):
        """
        Register transport interface.

        Args:
            iface: Transport interface
        """
        self.interfaces.append(iface)
        self.route.add_interface(iface, iface.name)
        logger.info(f"Added interface: {iface.name}")

    def run(self):
        """Main event loop using select()."""
        # Create IPC server socket
        self.ipc_server = socket_stdlib.socket(socket_stdlib.AF_INET, socket_stdlib.SOCK_STREAM)
        self.ipc_server.setsockopt(socket_stdlib.SOL_SOCKET, socket_stdlib.SO_REUSEADDR, 1)
        self.ipc_server.bind((self.ipc_host, self.ipc_port))
        self.ipc_server.listen(10)
        self.ipc_server.setblocking(False)

        logger.info(f"IPC server listening on {self.ipc_host}:{self.ipc_port}")

        self.running = True

        # Main event loop
        while self.running:
            try:
                # Build select sets
                read_sockets = [self.ipc_server] + list(self.ipc_clients.keys())

                # Select with timeout
                readable, _, exceptional = select.select(read_sockets, [], read_sockets, 0.1)

                # Handle readable sockets
                for sock in readable:
                    if sock is self.ipc_server:
                        # New IPC client connection
                        self._handle_ipc_accept()
                    else:
                        # IPC client message
                        self._handle_ipc_message(sock)

                # Handle exceptional sockets (errors)
                for sock in exceptional:
                    if sock in self.ipc_clients:
                        logger.warning(f"IPC client error: {self.ipc_clients[sock]}")
                        self._cleanup_client(sock)

                # Check transport interfaces for incoming packets
                for iface in self.interfaces:
                    pkt = iface.recv(timeout=0)  # Non-blocking
                    if pkt:
                        self._handle_transport_rx(iface, pkt)

            except KeyboardInterrupt:
                logger.info("Received interrupt signal")
                break
            except Exception as e:
                logger.error(f"Event loop error: {e}", exc_info=True)

        # Cleanup
        self.shutdown()

    def _handle_ipc_accept(self):
        """Accept new IPC client connection."""
        try:
            client_sock, client_addr = self.ipc_server.accept()
            client_sock.setblocking(False)
            self.ipc_clients[client_sock] = client_addr
            self.stats['ipc_connects'] += 1
            logger.info(f"IPC client connected: {client_addr}")
        except Exception as e:
            logger.error(f"Accept error: {e}")

    def _handle_ipc_message(self, client_sock: socket_stdlib.socket):
        """
        Read and process IPC message from client.

        Args:
            client_sock: Client socket
        """
        try:
            # Read 4-byte header
            header_data = self._recv_exact(client_sock, 4)
            if not header_data:
                # Client disconnected
                self._cleanup_client(client_sock)
                return

            # Parse header
            header_int = struct.unpack('>I', header_data)[0]
            msg_type = (header_int >> 24) & 0xFF
            length = header_int & 0xFFFFFF

            # Read payload
            payload = b''
            if length > 0:
                payload = self._recv_exact(client_sock, length)
                if not payload:
                    self._cleanup_client(client_sock)
                    return

            # Dispatch message
            if msg_type == MSG_REGISTER:
                self._handle_register(client_sock, payload)
            elif msg_type == MSG_UNREGISTER:
                self._handle_unregister(client_sock, payload)
            elif msg_type == MSG_DATA:
                self._route_outbound(client_sock, payload)
            else:
                logger.warning(f"Unknown message type: {msg_type}")

        except BlockingIOError:
            # No data available (non-blocking socket)
            pass
        except Exception as e:
            logger.error(f"IPC message error: {e}")
            self._cleanup_client(client_sock)

    def _recv_exact(self, sock: socket_stdlib.socket, n: int) -> Optional[bytes]:
        """
        Receive exactly n bytes from socket.

        Args:
            sock: Socket to receive from
            n: Number of bytes

        Returns:
            bytes: Received data, or None if connection closed
        """
        data = bytearray()
        while len(data) < n:
            try:
                chunk = sock.recv(n - len(data))
                if not chunk:
                    return None
                data.extend(chunk)
            except BlockingIOError:
                # Non-blocking socket, wait a bit
                continue
        return bytes(data)

    def _handle_register(self, client_sock: socket_stdlib.socket, payload: bytes):
        """
        Handle MSG_REGISTER from client.

        Format: [csp_addr (2B)] + [csp_port (1B)] + [reserved (1B)]

        Args:
            client_sock: Client socket
            payload: Message payload
        """
        if len(payload) < 4:
            logger.error("Invalid REGISTER payload")
            return

        csp_addr, csp_port = struct.unpack('>HBx', payload)

        # If csp_addr is 0, assign daemon's local address
        if csp_addr == 0:
            csp_addr = self.local_addr

        # Check if port already in use
        key = (csp_addr, csp_port)
        if key in self.app_table:
            # Port in use
            status = 1  # PORT_IN_USE
            response = struct.pack('>BHx', status, csp_addr)
            self._send_ipc_message(client_sock, MSG_REGISTER_ACK, response)
            logger.warning(f"Port in use: {csp_addr}:{csp_port}")
            return

        # Register port
        self.app_table[key] = client_sock
        logger.info(f"Registered: {csp_addr}:{csp_port} → {self.ipc_clients.get(client_sock, 'unknown')}")

        # Send ACK
        status = 0  # OK
        response = struct.pack('>BHx', status, csp_addr)
        self._send_ipc_message(client_sock, MSG_REGISTER_ACK, response)

    def _handle_unregister(self, client_sock: socket_stdlib.socket, payload: bytes):
        """
        Handle MSG_UNREGISTER from client.

        Format: [csp_addr (2B)] + [csp_port (1B)] + [reserved (1B)]

        Args:
            client_sock: Client socket
            payload: Message payload
        """
        if len(payload) < 4:
            logger.error("Invalid UNREGISTER payload")
            return

        csp_addr, csp_port = struct.unpack('>HBx', payload)

        # Remove from app table
        key = (csp_addr, csp_port)
        if key in self.app_table and self.app_table[key] == client_sock:
            del self.app_table[key]
            logger.info(f"Unregistered: {csp_addr}:{csp_port}")

    def _route_outbound(self, client_sock: socket_stdlib.socket, frame: bytes):
        """
        Route outbound packet from IPC client to network.

        Args:
            client_sock: Source client socket
            frame: Raw CSP packet frame
        """
        try:
            # Parse CSP header
            if len(frame) < 4:
                logger.error("Packet too short")
                self.stats['tx_errors'] += 1
                return

            # Decode packet
            pkt = Packet()
            pkt.decode(frame)

            dst_addr = pkt.header.dst

            # Check for loopback (local destination)
            if dst_addr == self.local_addr:
                self._route_loopback(pkt)
                self.stats['tx_packets'] += 1
                return

            # Lookup route for remote destination
            route_result = self.route.lookup(dst_addr)
            if route_result is None:
                logger.warning(f"No route to destination: {dst_addr}")
                self.stats['tx_errors'] += 1
                return

            iface, _via = route_result

            # Send via interface
            if iface.send(pkt):
                self.stats['tx_packets'] += 1
                logger.debug(f"Routed outbound: → {dst_addr}:{pkt.header.dport} via {iface.name}")
            else:
                self.stats['tx_errors'] += 1
                logger.error(f"Failed to send packet via {iface.name}")

        except Exception as e:
            logger.error(f"Outbound routing error: {e}")
            self.stats['tx_errors'] += 1

    def _route_loopback(self, pkt: Packet):
        """
        Route packet to local app (loopback).

        Args:
            pkt: Packet to route
        """
        dst_addr = pkt.header.dst
        dst_port = pkt.header.dport

        # Lookup in app table
        # 1. Try exact match
        client_sock = self.app_table.get((dst_addr, dst_port))

        # 2. Try wildcard
        if client_sock is None:
            client_sock = self.app_table.get((dst_addr, CSP_ANY))

        # 3. Not found
        if client_sock is None:
            logger.debug(f"No app for loopback: {dst_addr}:{dst_port}")
            return

        # Send to app
        self._send_to_app(client_sock, pkt)
        logger.debug(f"Routed loopback: → {dst_addr}:{dst_port}")

    def _handle_transport_rx(self, iface: TcpTun, pkt: Packet):
        """
        Handle packet received from transport interface.

        Args:
            iface: Source interface
            pkt: Received packet
        """
        try:
            dst_addr = pkt.header.dst
            dst_port = pkt.header.dport

            # Check if destination is local
            if dst_addr != self.local_addr:
                logger.debug(f"Packet not for us (dst={dst_addr}, local={self.local_addr})")
                # Could implement forwarding here
                return

            # Lookup app
            # 1. Try exact match
            client_sock = self.app_table.get((dst_addr, dst_port))

            # 2. Try wildcard
            if client_sock is None:
                client_sock = self.app_table.get((dst_addr, CSP_ANY))

            # 3. Not found
            if client_sock is None:
                logger.debug(f"No app registered for: {dst_addr}:{dst_port}")
                self.stats['rx_errors'] += 1
                return

            # Send to app
            self._send_to_app(client_sock, pkt)
            self.stats['rx_packets'] += 1
            logger.debug(f"Routed inbound: {pkt.header.src}:{pkt.header.sport} → {dst_addr}:{dst_port}")

        except Exception as e:
            logger.error(f"Inbound routing error: {e}")
            self.stats['rx_errors'] += 1

    def _send_to_app(self, client_sock: socket_stdlib.socket, pkt: Packet):
        """
        Send packet to IPC client.

        Args:
            client_sock: Client socket
            pkt: Packet to send
        """
        try:
            # Encode packet
            frame = pkt.encode()

            # Build MSG_DATA
            length = len(frame)
            header = struct.pack('>I', (MSG_DATA << 24) | length)
            message = header + frame

            # Send to client
            client_sock.sendall(message)
            logger.debug(f"Sent to app: {len(frame)} bytes")

        except Exception as e:
            logger.error(f"Send to app error: {e}")
            self._cleanup_client(client_sock)

    def _send_ipc_message(self, client_sock: socket_stdlib.socket, msg_type: int, payload: bytes):
        """
        Send IPC message to client.

        Args:
            client_sock: Client socket
            msg_type: Message type
            payload: Message payload
        """
        try:
            length = len(payload)
            header = struct.pack('>I', (msg_type << 24) | length)
            message = header + payload
            client_sock.sendall(message)
        except Exception as e:
            logger.error(f"Send IPC message error: {e}")
            self._cleanup_client(client_sock)

    def _cleanup_client(self, client_sock: socket_stdlib.socket):
        """
        Cleanup disconnected client.

        Args:
            client_sock: Client socket
        """
        # Remove from IPC clients
        if client_sock in self.ipc_clients:
            addr = self.ipc_clients[client_sock]
            del self.ipc_clients[client_sock]
            self.stats['ipc_disconnects'] += 1
            logger.info(f"IPC client disconnected: {addr}")

        # Remove all app table entries for this client
        keys_to_remove = [key for key, sock in self.app_table.items() if sock == client_sock]
        for key in keys_to_remove:
            del self.app_table[key]
            logger.info(f"Unregistered (disconnect): {key[0]}:{key[1]}")

        # Close socket
        try:
            client_sock.close()
        except:
            pass

    def shutdown(self):
        """Shutdown daemon and cleanup resources."""
        logger.info("Shutting down daemon...")

        self.running = False

        # Close all IPC clients
        for client_sock in list(self.ipc_clients.keys()):
            try:
                client_sock.close()
            except:
                pass

        # Close IPC server
        if self.ipc_server:
            try:
                self.ipc_server.close()
            except:
                pass

        # Close transport interfaces
        for iface in self.interfaces:
            try:
                iface.close()
            except:
                pass

        # Print statistics
        logger.info(f"Statistics: {self.stats}")
        logger.info("Daemon shut down")

    def print_status(self):
        """Print daemon status."""
        print("\n=== PyCSP Daemon Status ===")
        print(f"Local CSP Address: {self.local_addr}")
        print(f"IPC Server: {self.ipc_host}:{self.ipc_port}")
        print(f"\nConnected Clients: {len(self.ipc_clients)}")
        print(f"Registered Ports: {len(self.app_table)}")
        for (addr, port), sock in self.app_table.items():
            client_addr = self.ipc_clients.get(sock, ('unknown', 0))
            print(f"  {addr}:{port} → {client_addr}")
        print(f"\nTransport Interfaces: {len(self.interfaces)}")
        for iface in self.interfaces:
            print(f"  {iface}")
        print(f"\nStatistics:")
        for key, value in self.stats.items():
            print(f"  {key}: {value}")
        print("=" * 30 + "\n")


def main():
    """Main entry point for daemon."""
    parser = argparse.ArgumentParser(
        description='PyCSP Daemon - CSP packet router and multiplexer',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Start daemon with default settings (local addr=10, IPC port=9701)
  python -m pycsp.daemon

  # Start with custom local address
  python -m pycsp.daemon --addr 20

  # Connect to remote CSP node via TCP
  python -m pycsp.daemon --addr 10 --remote 192.168.1.100:9700

  # Custom IPC settings
  python -m pycsp.daemon --ipc-host 0.0.0.0 --ipc-port 9702
"""
    )

    parser.add_argument('--addr', type=int, default=10,
                       help='Local CSP address (default: 10)')
    parser.add_argument('--ipc-host', default='127.0.0.1',
                       help='IPC bind host (default: 127.0.0.1)')
    parser.add_argument('--ipc-port', type=int, default=9701,
                       help='IPC bind port (default: 9701)')
    parser.add_argument('--remote', metavar='IP:PORT',
                       help='Remote CSP endpoint (TCP tunnel)')
    parser.add_argument('--remote-server', action='store_true',
                       help='Start as TCP server (listen for remote connection)')
    parser.add_argument('--verbose', '-v', action='store_true',
                       help='Enable verbose (DEBUG) logging')
    parser.add_argument('--status-interval', type=int, default=0,
                       help='Print status every N seconds (0 = disabled)')

    args = parser.parse_args()

    # Setup logging level
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
        logger.setLevel(logging.DEBUG)

    # Create daemon
    daemon = Daemon(
        local_addr=args.addr,
        ipc_host=args.ipc_host,
        ipc_port=args.ipc_port
    )

    # Add transport interface if specified
    if args.remote:
        try:
            if ':' in args.remote:
                ip, port_str = args.remote.split(':', 1)
                port = int(port_str)
            else:
                ip = args.remote
                port = 9700

            tcptun = TcpTun(
                name='tcptun0',
                addr=ip,
                port=port,
                server=args.remote_server,
                timeout=2.0,
                auto_reconnect=not args.remote_server
            )

            daemon.add_interface(tcptun)

            # Set as default route
            daemon.route.set_default_route(tcptun)
            logger.info(f"Set default route via {tcptun.name}")

        except Exception as e:
            logger.error(f"Failed to setup remote connection: {e}")
            sys.exit(1)

    # Setup signal handlers
    def signal_handler(sig, frame):
        logger.info("Received signal, shutting down...")
        daemon.running = False

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Print initial status
    daemon.print_status()

    # Run daemon
    try:
        logger.info("Starting daemon...")
        daemon.run()
    except Exception as e:
        logger.error(f"Daemon error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
