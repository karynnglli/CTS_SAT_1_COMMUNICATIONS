# PyCSP Implementation

Complete TCP/IP-based implementation of the Cubesat Space Protocol (CSP) in Python.

## Architecture

The implementation follows a **daemon architecture** where:
- A central daemon (`pycsp-muxd`) handles all network I/O and routing
- Client applications use `libpycsp` to create sockets via IPC
- Clients handle RDP state machines, HMAC, CRC32, and XTEA
- Routing uses manual interface registration (no config files)

```
┌────────────────────────────────────────────────────────────┐
│  Application (libpycsp)                                    │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐                 │
│  │ Socket   │  │   RDP    │  │  Packet  │                 │
│  │ API      │──│  Layer   │──│  Encode  │                 │
│  └──────────┘  └──────────┘  └──────────┘                 │
│       │                            │                       │
│       └────────────IPC (TCP)───────┘                       │
├────────────────────────────────────────────────────────────┤
│  Daemon (pycsp-muxd)                                       │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │
│  │ IPC Server   │  │  App Table   │  │ Route Table  │     │
│  │ (TCP:9701)   │──│  Dispatcher  │──│   Lookup     │     │
│  └──────────────┘  └──────────────┘  └──────────────┘     │
│                           │                                │
│                    ┌──────┴──────┐                         │
│                    │  Transport  │                         │
│                    │   TcpTun    │                         │
│                    └─────────────┘                         │
└────────────────────────────────────────────────────────────┘
```

## Implemented Modules

### Core Modules

1. **[transport.py](src/pycsp/transport.py)** - TCP transport layer
   - `TcpTun` class for reliable TCP-based CSP packet transport
   - Length-prefixed framing (4-byte big-endian)
   - Background RX thread with packet queuing
   - Automatic reconnection with exponential backoff
   - Thread-safe send/recv operations

2. **[route.py](src/pycsp/route.py)** - Routing table
   - Static routing with manual interface registration
   - Route table: `csp_addr → (interface, via_addr)`
   - Default route support
   - No config files or dynamic discovery

3. **[rdp.py](src/pycsp/rdp.py)** - RDP state machine
   - Reliable Datagram Protocol implementation
   - 3-way handshake (SYN, SYN+ACK, ACK)
   - Sequence numbers and acknowledgments
   - Retransmission on timeout
   - Window-based flow control
   - Out-of-order packet buffering
   - Delayed ACKs for efficiency

4. **[socket.py](src/pycsp/socket.py)** - Socket API
   - Standard Python socket-like API
   - IPC protocol for daemon communication
   - Connection management
   - Port registration and binding
   - RDP integration for reliable connections
   - Context manager support

5. **[daemon.py](src/pycsp/daemon.py)** - Central multiplexer
   - Event loop using `select()` for multiplexing
   - App registration table: `(addr, port) → client socket`
   - Inbound/outbound packet routing
   - Loopback support for local communication
   - Statistics tracking

### Existing Modules (Not Modified)

- **[packet.py](src/pycsp/packet.py)** - Packet encoding/decoding (already implemented)
- **[link.py](src/pycsp/link.py)** - Interface base class and hardware interfaces

## Wire Protocols

### TCP Transport Framing

```
[Length (4B, big-endian)] + [CSP Packet (raw bytes)]
```

### IPC Protocol

```
Message Header: [Type (1B)] + [Length (3B, big-endian)]

Message Types:
- 0x01: MSG_REGISTER      - Register port
- 0x02: MSG_REGISTER_ACK  - Registration response
- 0x03: MSG_UNREGISTER    - Unregister port
- 0x04: MSG_DATA          - CSP packet data
- 0xFF: MSG_ERROR         - Error response
```

### RDP Header

```
┌──────────┬──────────────┬──────────────┐
│  Flags   │   Seq Num    │   Ack Num    │
│  (1 byte)│   (2 bytes)  │   (2 bytes)  │
└──────────┴──────────────┴──────────────┘

Flags:
- 0x08: RDP_SYN - Connection initiation
- 0x04: RDP_ACK - Acknowledgment
- 0x02: RDP_EAK - Extended/Selective ACK
- 0x01: RDP_RST - Reset connection
```

## Usage

### Start the Daemon

```bash
# Basic daemon (local address 10, IPC port 9701)
python -m pycsp.daemon --addr 10

# With remote TCP connection
python -m pycsp.daemon --addr 10 --remote 192.168.1.100:9700

# Verbose logging
python -m pycsp.daemon --addr 10 --verbose
```

### Server Application

```python
from pycsp import create_server

# Create listening socket
sock = create_server((10, 15), backlog=10)

# Accept connections
while True:
    conn = sock.accept()
    data = conn.recv()
    print(f"Received: {data}")
    conn.send(b"ACK")
    conn.close()
```

### Client Application

```python
from pycsp import socket, CSP_O_RDP, CSP_O_HMAC

# Create socket
sock = socket()

# Connect with RDP reliability
conn = sock.connect((20, 15), opts=CSP_O_RDP)

# Send data
conn.send(b"Hello, CSP!")

# Receive response
data = conn.recv(timeout=5.0)

# Close
conn.close()
```

### Simple Example

```python
from pycsp import create_connection, CSP_O_RDP

# Connect to remote endpoint
conn = create_connection((10, 15), opts=CSP_O_RDP, timeout=10.0)

# Send message
conn.send(b"Hello")

# Receive response
response = conn.recv(timeout=5.0)

# Close
conn.close()
```

## Examples

- [examples/simple_loopback.py](examples/simple_loopback.py) - Simple server/client example on single host

## Configuration

Environment variables:
- `PYCSP_DAEMON_HOST` - Daemon hostname (default: 127.0.0.1)
- `PYCSP_DAEMON_PORT` - Daemon port (default: 9701)
- `PYCSP_LOG_LEVEL` - Logging level (default: WARNING)

## Testing

Run the simple loopback example:

```bash
# Terminal 1: Start daemon
python -m pycsp.daemon --addr 10 --verbose

# Terminal 2: Run example
python examples/simple_loopback.py
```

## Features

### Implemented
- ✅ TCP transport (TcpTun)
- ✅ Static routing with manual registration
- ✅ RDP reliable connections
- ✅ Socket API (bind, listen, accept, connect, send, recv)
- ✅ IPC protocol (daemon communication)
- ✅ Loopback routing
- ✅ Packet encoding/decoding (HMAC, CRC32, XTEA)
- ✅ Connection pool management
- ✅ Thread-safe operations
- ✅ Automatic reconnection

### Not Implemented (Future Work)
- ❌ UDP transport
- ❌ Unix socket IPC
- ❌ Dynamic route discovery
- ❌ Config file support
- ❌ Service handlers
- ❌ Serial interfaces (KISS)
- ❌ Radio interfaces (AX100)

## Architecture Decisions

1. **TCP-only for now**: Simplifies initial implementation, UDP can be added later
2. **Daemon architecture**: Separates network I/O from application logic
3. **Manual routing**: No config files or discovery - explicit interface registration
4. **IPC via TCP**: Platform-independent, easier debugging than Unix sockets
5. **Client-side RDP**: Daemon doesn't inspect payloads, just routes based on headers

## Reference

- Design specification: [DESIGN_PLAN.md](../DESIGN_PLAN.md)
- libcsp C implementation: [libcsp/](../libcsp/)
- CSP specification: https://github.com/libcsp/libcsp

## License

Same as parent project.
