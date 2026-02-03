# PyCSP Multi-Process Architecture Plan

## Overview

This document outlines the architecture for `pycsp-muxd` (daemon) and `libpycsp` (client library) following Candidate C.

---

## 1. System Components

```
┌──────────────────────────────────────────────────────────────────────────┐
│                              HOST                                        │
│                                                                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                    │
│  │    App 1     │  │    App 2     │  │    App 3     │                    │
│  │  libpycsp    │  │  libpycsp    │  │  libpycsp    │                    │
│  │              │  │              │  │              │                    │
│  │ ┌──────────┐ │  │ ┌──────────┐ │  │ ┌──────────┐ │                    │
│  │ │ RDP      │ │  │ │ RDP      │ │  │ │ RDP      │ │  ← Transport       │
│  │ │ HMAC/CRC │ │  │ │ HMAC/CRC │ │  │ │ HMAC/CRC │ │  ← Security        │
│  │ └──────────┘ │  │ └──────────┘ │  │ └──────────┘ │                    │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘                    │
│         │                 │                 │                            │
│         └─────────────────┼─────────────────┘                            │
│                           │                                              │
│                    IPC (TCP/Unix)                                        │
│                           │                                              │
│                   ┌───────┴───────┐                                      │
│                   │  pycsp-muxd   │                                      │
│                   │               │                                      │
│                   │ ┌───────────┐ │                                      │
│                   │ │ App Table │ │  ← (addr,port) → app_conn            │
│                   │ ├───────────┤ │                                      │
│                   │ │Route Table│ │  ← csp_addr → (ip,port)              │
│                   │ ├───────────┤ │                                      │
│                   │ │CSP Header │ │  ← Parse only, don't touch payload   │
│                   │ └───────────┘ │                                      │
│                   └───────┬───────┘                                      │
│                           │                                              │
│                      UDP Socket                                          │
│                           │                                              │
└───────────────────────────┼──────────────────────────────────────────────┘
                            │
                     ┌──────┴──────┐
                     │   Network   │
                     │ (Ethernet)  │
                     └─────────────┘
```

---

## 2. Daemon Architecture (pycsp-muxd)

### 2.1 Data Structures

```
┌─────────────────────────────────────────────────────────────┐
│                    DAEMON STATE                             │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  App Registration Table                                     │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  Key: (csp_addr, csp_port)                          │    │
│  │  Value: app_connection (IPC socket/fd)              │    │
│  │                                                     │    │
│  │  Example:                                           │    │
│  │  (10, 15) → App1_conn                               │    │
│  │  (10, 16) → App2_conn                               │    │
│  │  (10, CSP_ANY) → App3_conn  ← catch-all             │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                             │
│  Route Table (for outbound)                                 │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  Key: csp_addr (or CIDR prefix)                     │    │
│  │  Value: (remote_ip, remote_port)                    │    │
│  │                                                     │    │
│  │  Example:                                           │    │
│  │  csp_addr=5  → 192.168.1.100:9700                   │    │
│  │  csp_addr=20 → 192.168.1.200:9700                   │    │
│  │  default     → 192.168.1.1:9700 (gateway)           │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                             │
│  Local CSP Address                                          │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  my_csp_addr = 10  (configured at startup)          │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 Hardware Interface Setup

```
┌─────────────────────────────────────────────────────────────┐
│                 INTERFACE INITIALIZATION                    │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  1. Parse config file or CLI args:                          │
│     - my_csp_addr = 10                                      │
│     - udp_bind_port = 9700                                  │
│     - ipc_path = /var/run/pycsp.sock (or TCP port)          │
│                                                             │
│  2. Create UDP socket for CSP network:                      │
│     - bind(0.0.0.0:9700)                                    │
│     - This receives CSP frames from remote nodes            │
│                                                             │
│  3. Create IPC listener for local apps:                     │
│     - Unix: bind(/var/run/pycsp.sock)                       │
│     - TCP:  bind(127.0.0.1:9701)                            │
│                                                             │
│  4. Load route table from config:                           │
│     - Static routes: csp_addr → (ip, port)                  │
│     - Default route for unknown destinations                │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 2.3 Packet Flow: Network → App

```
┌─────────────────────────────────────────────────────────────┐
│              INBOUND PACKET ROUTING                         │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  UDP Socket receives frame                                  │
│         │                                                   │
│         ▼                                                   │
│  ┌─────────────────────────────────────┐                    │
│  │  Parse CSP Header (first 4-6 bytes) │                    │
│  │  Extract: dst_addr, dst_port        │                    │
│  │  (Don't touch payload!)             │                    │
│  └──────────────────┬──────────────────┘                    │
│                     │                                       │
│                     ▼                                       │
│  ┌─────────────────────────────────────┐                    │
│  │  Is dst_addr == my_csp_addr?        │                    │
│  └──────────────────┬──────────────────┘                    │
│                     │                                       │
│         ┌───────────┴───────────┐                           │
│         │ YES                   │ NO                        │
│         ▼                       ▼                           │
│  ┌─────────────────┐    ┌─────────────────┐                 │
│  │ Lookup App Table│    │ Lookup Route    │                 │
│  │ (dst_addr,port) │    │ Forward to next │                 │
│  └────────┬────────┘    │ hop via UDP     │                 │
│           │             └─────────────────┘                 │
│           ▼                                                 │
│  ┌─────────────────────────────────────┐                    │
│  │  Lookup order:                      │                    │
│  │  1. Exact: (addr, port)             │                    │
│  │  2. Wildcard: (addr, CSP_ANY)       │                    │
│  │  3. Not found → drop/log            │                    │
│  └──────────────────┬──────────────────┘                    │
│                     │                                       │
│                     ▼                                       │
│  ┌─────────────────────────────────────┐                    │
│  │  Forward raw frame to app via IPC   │                    │
│  │  (CSP header + payload, untouched)  │                    │
│  └─────────────────────────────────────┘                    │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 2.4 Packet Flow: App → Network

```
┌─────────────────────────────────────────────────────────────┐
│              OUTBOUND PACKET ROUTING                        │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  App sends frame via IPC                                    │
│         │                                                   │
│         ▼                                                   │
│  ┌─────────────────────────────────────┐                    │
│  │  Parse CSP Header                   │                    │
│  │  Extract: dst_addr                  │                    │
│  └──────────────────┬──────────────────┘                    │
│                     │                                       │
│                     ▼                                       │
│  ┌─────────────────────────────────────┐                    │
│  │  Is dst_addr == my_csp_addr?        │                    │
│  │  (Local loopback case)              │                    │
│  └──────────────────┬──────────────────┘                    │
│                     │                                       │
│         ┌───────────┴───────────┐                           │
│         │ YES                   │ NO                        │
│         ▼                       ▼                           │
│  ┌─────────────────┐    ┌─────────────────┐                 │
│  │ Route to local  │    │ Lookup Route    │                 │
│  │ app (same as    │    │ Table for       │                 │
│  │ inbound logic)  │    │ dst_addr        │                 │
│  └─────────────────┘    └────────┬────────┘                 │
│                                  │                          │
│                                  ▼                          │
│                    ┌──────────────────────────────┐         │
│                    │  Get (remote_ip, remote_port)│         │
│                    │  from route table            │         │
│                    └──────────────┬───────────────┘         │
│                                   │                         │
│                                   ▼                         │
│                    ┌──────────────────────────────┐         │
│                    │  sendto(udp_socket,          │         │
│                    │         frame,               │         │
│                    │         remote_ip:port)      │         │
│                    └──────────────────────────────┘         │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. Client Library Architecture (libpycsp)

### 3.1 IPC Protocol Messages

```
┌─────────────────────────────────────────────────────────────┐
│                     IPC MESSAGE TYPES                       │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  MSG_REGISTER                                               │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  App → Daemon: "I want to receive on (addr, port)"  │    │
│  │                                                     │    │
│  │  Fields:                                            │    │
│  │  - type: REGISTER                                   │    │
│  │  - csp_addr: uint16 (or 0 = use daemon's addr)      │    │
│  │  - csp_port: uint8 (or CSP_ANY = 255)               │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                             │
│  MSG_REGISTER_ACK                                           │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  Daemon → App: "Registration result"                │    │
│  │                                                     │    │
│  │  Fields:                                            │    │
│  │  - type: REGISTER_ACK                               │    │
│  │  - status: OK / PORT_IN_USE / INVALID               │    │
│  │  - assigned_addr: uint16 (daemon's csp_addr)        │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                             │
│  MSG_UNREGISTER                                             │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  App → Daemon: "I'm done with (addr, port)"         │    │
│  │                                                     │    │
│  │  Fields:                                            │    │
│  │  - type: UNREGISTER                                 │    │
│  │  - csp_addr: uint16                                 │    │
│  │  - csp_port: uint8                                  │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                             │
│  MSG_DATA                                                   │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  Bidirectional: Raw CSP frame                       │    │
│  │                                                     │    │
│  │  Fields:                                            │    │
│  │  - type: DATA                                       │    │
│  │  - length: uint16                                   │    │
│  │  - frame: [CSP Header + Payload] (raw bytes)        │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 3.2 Bind/Listen Implementation

```
┌─────────────────────────────────────────────────────────────┐
│                       BIND / LISTEN                         │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  pycsp_bind(socket, port)                                   │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  1. Connect to daemon IPC (if not connected)        │    │
│  │                                                     │    │
│  │  2. Send MSG_REGISTER:                              │    │
│  │     - csp_addr = 0 (use daemon's address)           │    │
│  │     - csp_port = port                               │    │
│  │                                                     │    │
│  │  3. Wait for MSG_REGISTER_ACK:                      │    │
│  │     - If OK: store assigned_addr in socket struct   │    │
│  │     - If PORT_IN_USE: return error                  │    │
│  │                                                     │    │
│  │  4. Socket is now "bound" - daemon will route       │    │
│  │     packets for (assigned_addr, port) to us         │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                             │
│  pycsp_listen(socket, backlog)                              │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  1. Mark socket as "listening"                      │    │
│  │                                                     │    │
│  │  2. Initialize pending connection queue             │    │
│  │     (for RDP SYN packets)                           │    │
│  │                                                     │    │
│  │  Note: This is local state only - no IPC needed     │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                             │
│  pycsp_accept(socket, timeout)                              │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  1. Wait for incoming packet on bound port          │    │
│  │                                                     │    │
│  │  2. If RDP SYN received:                            │    │
│  │     - Create new connection object                  │    │
│  │     - Initialize RDP state (SYN_RCVD)               │    │
│  │     - Send SYN+ACK                                  │    │
│  │     - Return connection to app                      │    │
│  │                                                     │    │
│  │  3. If connectionless packet:                       │    │
│  │     - Queue for later recv() call                   │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 3.3 Connect Implementation

```
┌─────────────────────────────────────────────────────────────┐
│                          CONNECT                            │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  pycsp_connect(dest_addr, dest_port, opts)                  │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  1. Connect to daemon IPC (if not connected)        │    │
│  │                                                     │    │
│  │  2. Allocate ephemeral source port (32-63)          │    │
│  │                                                     │    │
│  │  3. Send MSG_REGISTER for ephemeral port:           │    │
│  │     - csp_addr = 0                                  │    │
│  │     - csp_port = ephemeral_port                     │    │
│  │                                                     │    │
│  │  4. Create connection object:                       │    │
│  │     - src_addr = assigned_addr (from daemon)        │    │
│  │     - src_port = ephemeral_port                     │    │
│  │     - dst_addr = dest_addr                          │    │
│  │     - dst_port = dest_port                          │    │
│  │                                                     │    │
│  │  5. If RDP enabled (CSP_O_RDP in opts):             │    │
│  │     - Initialize RDP state (SYN_SENT)               │    │
│  │     - Build & send SYN packet                       │    │
│  │     - Wait for SYN+ACK                              │    │
│  │     - Send ACK, transition to OPEN                  │    │
│  │                                                     │    │
│  │  6. Return connection object                        │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 3.4 Send/Recv Implementation

```
┌─────────────────────────────────────────────────────────────┐
│                        SEND / RECV                          │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  pycsp_send(conn, data)                                     │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  1. Build packet:                                   │    │
│  │     - Copy data to packet buffer                    │    │
│  │                                                     │    │
│  │  2. If RDP enabled:                                 │    │
│  │     - Append RDP header (5 bytes)                   │    │
│  │     - Clone to retransmit queue                     │    │
│  │                                                     │    │
│  │  3. If HMAC enabled:                                │    │
│  │     - Append HMAC (4 bytes)                         │    │
│  │                                                     │    │
│  │  4. If CRC32 enabled:                               │    │
│  │     - Append CRC32 (4 bytes)                        │    │
│  │                                                     │    │
│  │  5. Prepend CSP header (4-6 bytes)                  │    │
│  │                                                     │    │
│  │  6. Send MSG_DATA to daemon with raw frame          │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                             │
│  pycsp_recv(conn, timeout)                                  │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  1. Wait for MSG_DATA from daemon                   │    │
│  │                                                     │    │
│  │  2. Strip CSP header, extract flags                 │    │
│  │                                                     │    │
│  │  3. If CRC32 flag set:                              │    │
│  │     - Verify & strip CRC32                          │    │
│  │                                                     │    │
│  │  4. If HMAC flag set:                               │    │
│  │     - Verify & strip HMAC                           │    │
│  │                                                     │    │
│  │  5. If RDP flag set:                                │    │
│  │     - Process RDP header                            │    │
│  │     - Handle seq/ack, maybe send ACK                │    │
│  │     - Handle out-of-order (RX queue)                │    │
│  │                                                     │    │
│  │  6. Return application data                         │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## 4. Route Table Configuration

### 4.1 Static Routes (Config File)

```
┌─────────────────────────────────────────────────────────────┐
│                    ROUTE CONFIGURATION                      │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  # /etc/pycsp/routes.conf                                   │
│                                                             │
│  # Format: csp_addr  remote_ip  remote_port                 │
│                                                             │
│  # Direct routes to known CSP nodes                         │
│  5     192.168.1.100   9700                                 │
│  6     192.168.1.101   9700                                 │
│  20    192.168.1.200   9700                                 │
│                                                             │
│  # CIDR-style subnet routes (optional)                      │
│  # 16/4  192.168.2.1   9700   # CSP addrs 16-31 via gateway │
│                                                             │
│  # Default route (for unknown destinations)                 │
│  default  192.168.1.1  9700                                 │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 4.2 Dynamic Route Discovery (Optional)

```
┌─────────────────────────────────────────────────────────────┐
│                   DYNAMIC ROUTE OPTIONS                     │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  Option A: Multicast Announcement                           │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  - Daemon periodically broadcasts "I am CSP addr X" │    │
│  │  - Other daemons learn remote_ip → csp_addr mapping │    │
│  │  - Simple, works on LAN                             │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                             │
│  Option B: Central Registry                                 │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  - Daemons register with central server             │    │
│  │  - Query registry for csp_addr → ip:port            │    │
│  │  - More complex, works across networks              │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                             │
│  Option C: Static Only (Recommended for v1)                 │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  - Config file only                                 │    │
│  │  - Simplest, predictable                            │    │
│  │  - Good for embedded/spacecraft use case            │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## 5. Connection Lifecycle

```
┌─────────────────────────────────────────────────────────────┐
│                    FULL CONNECTION FLOW                     │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  SERVER SIDE                      CLIENT SIDE               │
│  ──────────────                   ───────────               │
│                                                             │
│  socket = pycsp_socket()          socket = pycsp_socket()   │
│        │                                  │                 │
│        ▼                                  │                 │
│  pycsp_bind(socket, 15)                   │                 │
│        │                                  │                 │
│        │ ──► MSG_REGISTER(port=15) ──►    │                 │
│        │ ◄── MSG_REGISTER_ACK ◄───────    │                 │
│        │                                  │                 │
│        ▼                                  │                 │
│  pycsp_listen(socket)                     │                 │
│        │                                  │                 │
│        │                                  ▼                 │
│        │                          conn = pycsp_connect(     │
│        │                                   dst=10, port=15) │
│        │                                  │                 │
│        │                                  │ ──► MSG_REGISTER│
│        │                                  │ ◄── ACK         │
│        │                                  │                 │
│        │    ◄──── [RDP SYN] ◄─────────────│                 │
│        │                                  │                 │
│        ▼                                  │                 │
│  conn = pycsp_accept(socket)              │                 │
│        │                                  │                 │
│        │ ────► [RDP SYN+ACK] ────────────►│                 │
│        │                                  │                 │
│        │    ◄──── [RDP ACK] ◄─────────────│                 │
│        │                                  │                 │
│        ▼                                  ▼                 │
│   CONNECTED                          CONNECTED              │
│        │                                  │                 │
│        │    ◄──── [DATA+ACK] ◄────────────│ pycsp_send()    │
│        │                                  │                 │
│  data = pycsp_recv(conn)                  │                 │
│        │                                  │                 │
│        │ ────► [DATA+ACK] ───────────────►│                 │
│        │                                  │                 │
│        │                          data = pycsp_recv(conn)   │
│        │                                  │                 │
│  pycsp_close(conn)                pycsp_close(conn)         │
│        │                                  │                 │
│        │    ◄──── [RDP RST] ◄─────────────│                 │
│        │                                  │                 │
│        │ ──► MSG_UNREGISTER ──►           │ ──► MSG_UNREG   │
│        │                                  │                 │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## 6. Summary: What Goes Where

| Component | Daemon (pycsp-muxd) | Library (libpycsp) |
|-----------|---------------------|-------------------|
| **CSP Header Parse** | ✓ (for routing) | ✓ (for payload access) |
| **Route Table** | ✓ | - |
| **App Registration** | ✓ | - |
| **Packet Demux** | ✓ | - |
| **UDP Socket** | ✓ | - |
| **IPC Socket** | ✓ (server) | ✓ (client) |
| **RDP State Machine** | - | ✓ |
| **RDP Retransmit Queue** | - | ✓ |
| **HMAC/CRC32** | - | ✓ |
| **XTEA Encryption** | - | ✓ |
| **Connection Pool** | - | ✓ |
| **bind/listen/accept** | registration | full logic |
| **connect** | registration | full logic + RDP |
| **send/recv** | forward only | full encode/decode |

---

## 7. Next Steps

1. **Define IPC wire format** - Binary protocol for MSG_REGISTER, MSG_DATA, etc.
2. **Implement daemon core** - Event loop, UDP + IPC handling
3. **Implement client library** - Socket API, RDP state machine
4. **Test single host** - App1 → daemon → App2 (loopback)
5. **Test multi-host** - Host1/App → daemon → UDP → daemon → Host2/App
