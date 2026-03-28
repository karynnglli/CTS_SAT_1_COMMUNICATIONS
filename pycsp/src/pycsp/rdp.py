"""
CSP RDP (Reliable Datagram Protocol) Implementation

This module implements the RDP state machine for connection-oriented,
reliable CSP communication with:
- 3-way handshake (SYN, SYN+ACK, ACK)
- Sequence numbers and acknowledgments
- Retransmission on timeout
- Window-based flow control
- Out-of-order packet buffering

RDP Header Format (5 bytes):
    ┌──────────┬──────────────┬──────────────┐
    │  Flags   │   Seq Num    │   Ack Num    │
    │  (1 byte)│   (2 bytes)  │   (2 bytes)  │
    └──────────┴──────────────┴──────────────┘
"""

import time
import random
import struct
import threading
import logging
from dataclasses import dataclass
from typing import List, Tuple, Optional, TYPE_CHECKING
from enum import IntEnum

if TYPE_CHECKING:
    from .socket import Connection

from .packet import Packet

# Setup logging
logger = logging.getLogger(__name__)

# RDP Control Flags
RDP_SYN = 0x08  # Connection initiation
RDP_ACK = 0x04  # Acknowledgment
RDP_EAK = 0x02  # Extended/Selective ACK
RDP_RST = 0x01  # Reset connection

# RDP Configuration (global defaults)
RDP_WINDOW_SIZE = 4          # Flow control window
RDP_PACKET_TIMEOUT = 1.0     # Retransmit timeout (seconds)
RDP_CONN_TIMEOUT = 10.0      # Connection timeout (seconds)
RDP_DELAYED_ACKS = True      # Enable delayed ACKs
RDP_ACK_TIMEOUT = 0.25       # Delayed ACK timeout (seconds)
RDP_ACK_DELAY_COUNT = 2      # Number of packets before ACK

# Connection states
class ConnectionState(IntEnum):
    CLOSED = 0
    SYN_SENT = 1      # Client waiting for SYN+ACK
    SYN_RCVD = 2      # Server waiting for ACK
    OPEN = 3          # Established
    CLOSE_WAIT = 4    # Graceful shutdown


@dataclass
class RDPHeader:
    """RDP header (5 bytes)."""
    flags: int      # Control flags
    seq_nr: int     # Sequence number (16-bit)
    ack_nr: int     # Acknowledgment number (16-bit)

    def encode(self) -> bytes:
        """Encode RDP header to bytes."""
        return struct.pack('>BHH', self.flags, self.seq_nr, self.ack_nr)

    @classmethod
    def decode(cls, data: bytes) -> 'RDPHeader':
        """Decode RDP header from bytes."""
        if len(data) < 5:
            raise ValueError(f"RDP header too short: {len(data)} bytes")
        flags, seq_nr, ack_nr = struct.unpack('>BHH', data[:5])
        return cls(flags=flags, seq_nr=seq_nr, ack_nr=ack_nr)

    def __repr__(self):
        flags_str = []
        if self.flags & RDP_SYN:
            flags_str.append('SYN')
        if self.flags & RDP_ACK:
            flags_str.append('ACK')
        if self.flags & RDP_EAK:
            flags_str.append('EAK')
        if self.flags & RDP_RST:
            flags_str.append('RST')
        flags = '|'.join(flags_str) if flags_str else 'NONE'
        return f"RDPHeader(flags={flags}, seq={self.seq_nr}, ack={self.ack_nr})"


class RDPState:
    """
    RDP state machine for a single connection.

    Implements:
    - 3-way handshake
    - Reliable transmission with retransmits
    - Flow control with sliding window
    - Out-of-order packet buffering
    - Delayed ACKs for efficiency
    """

    def __init__(self, conn: 'Connection'):
        """
        Initialize RDP state for a connection.

        Args:
            conn: Parent Connection object
        """
        self.conn = conn

        # State
        self.state = ConnectionState.CLOSED

        # Sequence numbers (16-bit with wrap-around)
        self.seq_out = random.randint(0, 0xFFFF)  # Next seq to send
        self.seq_in = 0                           # Last received in-order seq
        self.snd_una = self.seq_out               # Oldest unacknowledged
        self.rcv_irs = 0                          # Initial receive seq (from SYN)

        # Flow control
        self.window_size = RDP_WINDOW_SIZE

        # Retransmit queue: [(packet, timestamp, seq_nr), ...]
        self.tx_queue: List[Tuple[Packet, float, int]] = []

        # Out-of-order receive buffer: [(seq_nr, packet), ...]
        self.rx_queue: List[Tuple[int, Packet]] = []

        # Timeouts
        self.packet_timeout = RDP_PACKET_TIMEOUT
        self.conn_timeout = RDP_CONN_TIMEOUT

        # Delayed ACK
        self.delayed_acks = RDP_DELAYED_ACKS
        self.ack_timeout = RDP_ACK_TIMEOUT
        self.ack_delay_count = RDP_ACK_DELAY_COUNT
        self.ack_count = 0
        self.last_ack_time = time.time()
        self.last_rx_time = time.time()

        # Thread safety
        self._lock = threading.RLock()

        logger.debug(f"RDP state initialized (initial seq={self.seq_out})")

    def _seq_add(self, seq: int, n: int) -> int:
        """Add to sequence number with 16-bit wrap-around."""
        return (seq + n) & 0xFFFF

    def _seq_sub(self, a: int, b: int) -> int:
        """Subtract sequence numbers with wrap-around handling."""
        diff = (a - b) & 0xFFFF
        # Handle wrap: if diff > 32768, it's actually negative
        if diff > 0x8000:
            diff -= 0x10000
        return diff

    def _seq_between(self, seq: int, start: int, end: int) -> bool:
        """
        Check if seq is in range [start, end) with wrap-around.

        Uses signed comparison trick: (seq - start) < (end - start)
        """
        return self._seq_sub(seq, start) < self._seq_sub(end, start)

    def connect(self, timeout=5.0) -> bool:
        """
        Initiate RDP connection (CLIENT side).

        Performs 3-way handshake:
        1. Send SYN
        2. Wait for SYN+ACK
        3. Send ACK

        Args:
            timeout: Connection timeout in seconds

        Returns:
            bool: True if connected successfully
        """
        with self._lock:
            if self.state != ConnectionState.CLOSED:
                logger.error("RDP: Cannot connect, not in CLOSED state")
                return False

            # Send SYN
            logger.debug(f"RDP: Sending SYN (seq={self.seq_out})")
            self._send_control(RDP_SYN, seq=self.seq_out, ack=0)
            self.state = ConnectionState.SYN_SENT

        # Wait for SYN+ACK
        start_time = time.time()
        while time.time() - start_time < timeout:
            pkt = self.conn.rx_queue.get(timeout=0.1) if hasattr(self.conn, 'rx_queue') else None
            if pkt is None:
                continue

            with self._lock:
                # Parse RDP header
                if len(pkt.payload) < 5:
                    continue

                rdp_hdr = RDPHeader.decode(pkt.payload)
                logger.debug(f"RDP: Received {rdp_hdr}")

                # Check for SYN+ACK
                if (rdp_hdr.flags & RDP_SYN) and (rdp_hdr.flags & RDP_ACK):
                    if rdp_hdr.ack_nr == self._seq_add(self.seq_out, 1):
                        # Valid SYN+ACK
                        self.rcv_irs = rdp_hdr.seq_nr
                        self.seq_in = rdp_hdr.seq_nr
                        self.seq_out = self._seq_add(self.seq_out, 1)

                        # Send ACK
                        logger.debug(f"RDP: Sending ACK (seq={self.seq_out}, ack={self._seq_add(self.seq_in, 1)})")
                        self._send_control(RDP_ACK, seq=self.seq_out, ack=self._seq_add(self.seq_in, 1))

                        self.state = ConnectionState.OPEN
                        logger.info("RDP: Connection established (client)")
                        return True

        # Timeout
        with self._lock:
            self.state = ConnectionState.CLOSED
        logger.error("RDP: Connection timeout")
        return False

    def handle_syn(self, pkt: Packet):
        """
        Handle incoming SYN packet (SERVER side).

        Sends SYN+ACK in response.

        Args:
            pkt: Packet containing SYN
        """
        with self._lock:
            if len(pkt.payload) < 5:
                return

            rdp_hdr = RDPHeader.decode(pkt.payload)

            if not (rdp_hdr.flags & RDP_SYN):
                return

            logger.debug(f"RDP: Received {rdp_hdr}")

            # Store initial receive sequence
            self.rcv_irs = rdp_hdr.seq_nr
            self.seq_in = rdp_hdr.seq_nr

            # Send SYN+ACK
            logger.debug(f"RDP: Sending SYN+ACK (seq={self.seq_out}, ack={self._seq_add(self.seq_in, 1)})")
            self._send_control(RDP_SYN | RDP_ACK,
                             seq=self.seq_out,
                             ack=self._seq_add(self.seq_in, 1))

            self.state = ConnectionState.SYN_RCVD
            logger.info("RDP: SYN received, waiting for ACK (server)")

    def send(self, data: bytes) -> bool:
        """
        Send data with RDP reliability.

        Args:
            data: Application data to send

        Returns:
            bool: True if sent successfully
        """
        with self._lock:
            if self.state != ConnectionState.OPEN:
                logger.error(f"RDP: Cannot send, connection not OPEN (state={self.state})")
                return False

            # Check window
            window_used = self._seq_sub(self.seq_out, self.snd_una)
            if window_used >= self.window_size:
                logger.warning("RDP: Send window full, blocking...")
                # TODO: Implement blocking send with timeout
                return False

            # Build packet with RDP header
            seq = self.seq_out
            ack = self._seq_add(self.seq_in, 1)
            rdp_hdr = RDPHeader(flags=RDP_ACK, seq_nr=seq, ack_nr=ack)

            # Prepend RDP header to payload
            payload = rdp_hdr.encode() + data

            # Create packet
            pkt = Packet(
                src=self.conn.src_addr,
                dst=self.conn.dst_addr,
                sport=self.conn.src_port,
                dport=self.conn.dst_port,
                payload=payload,
                rdp=True,
                hmac_key=getattr(self.conn.socket, 'hmac_key', None) if hasattr(self.conn, 'socket') else None,
                crc=(self.conn.opts & 0x0040) if hasattr(self.conn, 'opts') else False
            )

            # Add to retransmit queue
            self.tx_queue.append((pkt, time.time(), seq))

            # Send packet
            try:
                frame = pkt.encode()
                # Send via IPC (will be implemented in socket.py)
                if hasattr(self.conn, 'socket') and hasattr(self.conn.socket, '_ipc_conn'):
                    from .socket import IPCMessage, MSG_DATA
                    msg = IPCMessage(MSG_DATA, frame)
                    self.conn.socket._ipc_conn.send_message(msg)

                    self.seq_out = self._seq_add(self.seq_out, 1)
                    logger.debug(f"RDP: Sent DATA (seq={seq}, ack={ack}, len={len(data)})")
                    return True
                else:
                    logger.error("RDP: No IPC connection available")
                    return False
            except Exception as e:
                logger.error(f"RDP: Send error: {e}")
                return False

    def recv(self, pkt: Packet) -> Optional[bytes]:
        """
        Process received RDP packet.

        Args:
            pkt: Received packet

        Returns:
            bytes: Application data if in-order, None otherwise
        """
        with self._lock:
            if len(pkt.payload) < 5:
                return None

            rdp_hdr = RDPHeader.decode(pkt.payload)
            data = pkt.payload[5:]  # Strip RDP header

            logger.debug(f"RDP: Received {rdp_hdr} (len={len(data)})")

            self.last_rx_time = time.time()

            # Handle control packets
            if rdp_hdr.flags & RDP_RST:
                logger.info("RDP: Received RST, closing connection")
                self.state = ConnectionState.CLOSED
                return None

            # Handle ACK in SYN_RCVD state (complete handshake)
            if self.state == ConnectionState.SYN_RCVD:
                if (rdp_hdr.flags & RDP_ACK) and rdp_hdr.ack_nr == self._seq_add(self.seq_out, 1):
                    self.state = ConnectionState.OPEN
                    self.seq_out = self._seq_add(self.seq_out, 1)
                    logger.info("RDP: Connection established (server)")
                return None

            # Process ACK (update send window)
            if rdp_hdr.flags & RDP_ACK:
                self._process_ack(rdp_hdr.ack_nr)

            # Process data
            if len(data) > 0 and self.state == ConnectionState.OPEN:
                expected_seq = self._seq_add(self.seq_in, 1)

                if rdp_hdr.seq_nr == expected_seq:
                    # In-order packet
                    self.seq_in = rdp_hdr.seq_nr
                    self.ack_count += 1

                    # Send ACK (delayed or immediate)
                    if not self.delayed_acks or self.ack_count >= self.ack_delay_count:
                        self._send_ack()

                    # Flush out-of-order queue
                    self._flush_rx_queue()

                    return data

                elif self._seq_between(rdp_hdr.seq_nr, expected_seq, self._seq_add(expected_seq, self.window_size)):
                    # Out-of-order packet (future)
                    logger.debug(f"RDP: Out-of-order packet (seq={rdp_hdr.seq_nr}, expected={expected_seq})")
                    self.rx_queue.append((rdp_hdr.seq_nr, data))
                    self.rx_queue.sort(key=lambda x: x[0])

                    # Send ACK for current seq
                    self._send_ack()
                    return None
                else:
                    # Duplicate or very old packet
                    logger.debug(f"RDP: Duplicate packet (seq={rdp_hdr.seq_nr})")
                    return None

            return None

    def _process_ack(self, ack_nr: int):
        """Process ACK, remove acknowledged packets from TX queue."""
        # Remove acknowledged packets
        self.tx_queue = [(pkt, ts, seq) for pkt, ts, seq in self.tx_queue
                        if not self._seq_between(seq, self.snd_una, ack_nr)]

        # Update send unacknowledged
        if self._seq_sub(ack_nr, self.snd_una) > 0:
            self.snd_una = ack_nr
            logger.debug(f"RDP: ACK received (ack={ack_nr}, snd_una={self.snd_una})")

    def _flush_rx_queue(self):
        """Flush in-order packets from out-of-order queue."""
        while self.rx_queue:
            seq, data = self.rx_queue[0]
            expected = self._seq_add(self.seq_in, 1)

            if seq == expected:
                self.rx_queue.pop(0)
                self.seq_in = seq
                # TODO: Deliver data to application
                logger.debug(f"RDP: Flushed packet from RX queue (seq={seq})")
            else:
                break

    def _send_ack(self):
        """Send standalone ACK packet."""
        ack_nr = self._seq_add(self.seq_in, 1)
        self._send_control(RDP_ACK, seq=self.seq_out, ack=ack_nr)
        self.ack_count = 0
        self.last_ack_time = time.time()
        logger.debug(f"RDP: Sent ACK (ack={ack_nr})")

    def _send_control(self, flags: int, seq: int, ack: int):
        """
        Send RDP control packet (SYN, ACK, RST).

        Args:
            flags: RDP flags
            seq: Sequence number
            ack: Acknowledgment number
        """
        rdp_hdr = RDPHeader(flags=flags, seq_nr=seq, ack_nr=ack)
        payload = rdp_hdr.encode()

        pkt = Packet(
            src=self.conn.src_addr,
            dst=self.conn.dst_addr,
            sport=self.conn.src_port,
            dport=self.conn.dst_port,
            payload=payload,
            rdp=True,
            hmac_key=getattr(self.conn.socket, 'hmac_key', None) if hasattr(self.conn, 'socket') else None,
            crc=(self.conn.opts & 0x0040) if hasattr(self.conn, 'opts') else False
        )

        try:
            frame = pkt.encode()
            if hasattr(self.conn, 'socket') and hasattr(self.conn.socket, '_ipc_conn'):
                from .socket import IPCMessage, MSG_DATA
                msg = IPCMessage(MSG_DATA, frame)
                self.conn.socket._ipc_conn.send_message(msg)
        except Exception as e:
            logger.error(f"RDP: Control packet send error: {e}")

    def check_timeouts(self):
        """
        Check for retransmit and connection timeouts.

        Should be called periodically (e.g., every 100ms).
        """
        with self._lock:
            now = time.time()

            # Check connection timeout
            if self.state == ConnectionState.OPEN:
                if now - self.last_rx_time > self.conn_timeout:
                    logger.warning("RDP: Connection timeout, closing")
                    self.state = ConnectionState.CLOSED
                    return

            # Check retransmit timeout
            for pkt, timestamp, seq in self.tx_queue:
                if now - timestamp > self.packet_timeout:
                    logger.debug(f"RDP: Retransmitting packet (seq={seq})")
                    try:
                        frame = pkt.encode()
                        if hasattr(self.conn, 'socket') and hasattr(self.conn.socket, '_ipc_conn'):
                            from .socket import IPCMessage, MSG_DATA
                            msg = IPCMessage(MSG_DATA, frame)
                            self.conn.socket._ipc_conn.send_message(msg)
                        # Update timestamp
                        idx = self.tx_queue.index((pkt, timestamp, seq))
                        self.tx_queue[idx] = (pkt, now, seq)
                    except Exception as e:
                        logger.error(f"RDP: Retransmit error: {e}")

            # Check delayed ACK timeout
            if self.delayed_acks and self.ack_count > 0:
                if now - self.last_ack_time > self.ack_timeout:
                    self._send_ack()

    def close(self):
        """Close RDP connection (send RST)."""
        with self._lock:
            if self.state != ConnectionState.CLOSED:
                logger.debug("RDP: Sending RST")
                self._send_control(RDP_RST, seq=self.seq_out, ack=self._seq_add(self.seq_in, 1))
                self.state = ConnectionState.CLOSED
                self.tx_queue.clear()
                self.rx_queue.clear()


# Global RDP timer management
_rdp_timer_running = False
_rdp_timer_thread = None
_active_rdp_connections = []
_rdp_lock = threading.Lock()


def register_rdp_connection(rdp_state: RDPState):
    """Register RDP connection for timeout checking."""
    global _rdp_timer_running, _rdp_timer_thread, _active_rdp_connections

    with _rdp_lock:
        if rdp_state not in _active_rdp_connections:
            _active_rdp_connections.append(rdp_state)

        # Start timer thread if not running
        if not _rdp_timer_running:
            _rdp_timer_running = True
            _rdp_timer_thread = threading.Thread(target=_rdp_timer_worker, daemon=True)
            _rdp_timer_thread.start()
            logger.debug("RDP: Timer thread started")


def unregister_rdp_connection(rdp_state: RDPState):
    """Unregister RDP connection from timeout checking."""
    with _rdp_lock:
        if rdp_state in _active_rdp_connections:
            _active_rdp_connections.remove(rdp_state)


def _rdp_timer_worker():
    """Background thread for RDP timeout checking."""
    global _rdp_timer_running

    while _rdp_timer_running:
        try:
            with _rdp_lock:
                connections = list(_active_rdp_connections)

            for rdp_state in connections:
                try:
                    rdp_state.check_timeouts()
                except Exception as e:
                    logger.error(f"RDP: Timer error: {e}")

            time.sleep(0.1)  # 100ms tick
        except Exception as e:
            logger.error(f"RDP: Timer thread error: {e}")

    logger.debug("RDP: Timer thread stopped")


def stop_rdp_timer():
    """Stop the RDP timer thread."""
    global _rdp_timer_running
    _rdp_timer_running = False
