# LibCSP Architecture Summary

## Table of Contents
1. [Overview](#overview)
2. [Layered Architecture](#layered-architecture)
3. [CSP Packet Structure](#csp-packet-structure)
4. [CSP Header Encoding](#csp-header-encoding)
5. [Security Features & Packet Encoding](#security-features--packet-encoding)
6. [RDP (Reliable Data Protocol)](#rdp-reliable-data-protocol)
7. [Data Flow](#data-flow)
8. [Key Components](#key-components)
9. [Multi-Process Architecture Evaluation](#multi-process-architecture-evaluation)

---

## Overview

LibCSP (Cubesat Space Protocol) is a lightweight, embeddable network stack designed for embedded systems and spacecraft. It follows a **TCP/IP-inspired layered architecture** optimized for resource-constrained environments.

### Key Characteristics
- **Single-process, multi-task** operation model
- **Zero-copy** packet handling for efficiency
- **Pluggable driver abstraction** for multiple transports
- **Central routing queue** (QFIFO) for all packet processing
- **Connection pool** with fixed pre-allocation
- **QoS-aware routing** with 4 priority levels

---

## Layered Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              APPLICATION LAYER                              │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐                          │
│  │   Server    │  │   Client    │  │  Services   │  (CMP, PING, PS, etc.)   │
│  │ csp_accept()│  │csp_connect()│  │  Ports 0-7  │                          │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘                          │
│         └────────────────┼────────────────┘                                 │
│                          ▼                                                  │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │                      SOCKET LAYER  (csp_io.c)                         │  │
│  │   csp_socket_t ←→ csp_conn_t (connection pool)                        │  │
│  │   bind() / listen() / accept() / connect() / send() / read()          │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              TRANSPORT LAYER                                │
│  ┌─────────────────────────────────┐  ┌─────────────────────────────────┐   │
│  │  Connection-oriented (RDP)      │  │  Connection-less (UDP-like)     │   │
│  │  - Reliable delivery            │  │  - No ordering guarantees       │   │
│  │  - Sequencing, retransmit       │  │  - Lower overhead               │   │
│  └─────────────────────────────────┘  └─────────────────────────────────┘   │
│                                                                             │
│  Security Options: CRC32 | HMAC (per-connection)                            │
└─────────────────────────────────────────────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              NETWORK LAYER                                  │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │              ROUTING QUEUE (QFIFO)  - All packets flow through here   │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                       │                                     │
│                                       ▼                                     │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │                            ROUTER                                     │  │
│  │   ┌─────────────┐    ┌──────────────┐    ┌─────────────────┐          │  │
│  │   │ For me?     │───▶│ Port Binding │───▶│ Deliver to      │         │  │
│  │   │ dst==myaddr │    │              │    │ socket/callback │          │  │
│  │   └──────┬──────┘    └──────────────┘    └─────────────────┘          │  │
│  │          │ No                                                         │  │
│  │          ▼                                                            │  │
│  │   ┌─────────────────────────────────────────────────────────┐         │  │
│  │   │ Routing Table (CIDR)                                    │         │  │
│  │   │ Lookup: dst_addr → (interface, via_addr)                │         │  │
│  │   └─────────────────────────────────────────────────────────┘         │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           INTERFACE LAYER                                   │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │  Interface Abstraction: struct csp_iface_s { nexthop_t; driver_data } │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                       │                                     │
│       ┌───────────────┬───────────────┼───────────────┬───────────────┐     │
│       ▼               ▼               ▼               ▼               ▼     │
│  ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐    │
│  │Loopback │    │   CAN   │    │  USART  │    │   UDP   │    │ ZMQ Hub │    │
│  │         │    │socketcan│    │  KISS   │    │         │    │  (IPC)  │    │
│  └─────────┘    └─────────┘    └─────────┘    └─────────┘    └─────────┘    │
└─────────────────────────────────────────────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              PHYSICAL LAYER                                 │
│         CAN Bus  │  Serial/UART  │  Ethernet/IP  │  ZeroMQ Sockets          │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## CSP Packet Structure

### Memory Layout (Zero-Copy Design)

```
┌─────────────────────────────────────────────────────────────────┐
│                       csp_packet_t                              │
├─────────────────────────────────────────────────────────────────┤
│  Metadata:                                                      │
│  - timestamp_tx    (4 bytes)  TX timestamp                      │
│  - conn            (ptr)      Associated RDP connection         │
│  - rx_count        (2 bytes)  Received bytes                    │
│  - remain          (2 bytes)  Remaining fragments               │
│  - cfpid           (4 bytes)  CAN fragmentation ID              │
│  - frame_begin     (ptr)      Start of frame                    │
│  - frame_length    (2 bytes)  Total frame length                │
│  - length          (2 bytes)  Data length                       │
│  - id              (8 bytes)  Unpacked CSP header               │
├─────────────────────────────────────────────────────────────────┤
│  header[8]         Padding for L2 headers (prepend area)        │
├─────────────────────────────────────────────────────────────────┤
│  data[256]         Payload data (union: uint8/16/32 access)     │
└─────────────────────────────────────────────────────────────────┘

Frame Layout in Memory:
  ◄──── header[8] ────►◄────────── data[256] ──────────►
  ┌────────────────────┬────────────────────────────────┐
  │  [CSP Hdr prepend] │  [Application Data] [RDP Hdr] │
  └────────────────────┴────────────────────────────────┘
       ▲                     ▲
       │                     │
  frame_begin            data pointer
```

### CSP ID Structure (Unpacked)

```c
typedef struct {
    uint8_t  pri;    // Priority (0-3)
    uint8_t  flags;  // Protocol flags
    uint16_t src;    // Source address
    uint16_t dst;    // Destination address
    uint8_t  dport;  // Destination port (0-63)
    uint8_t  sport;  // Source port (0-63)
} csp_id_t;
```

### Header Flags

| Flag | Value | Description |
|------|-------|-------------|
| `CSP_FCRC32` | 0x01 | Use CRC32 checksum |
| `CSP_FRDP` | 0x02 | Use RDP protocol |
| `CSP_FHMAC` | 0x08 | Use HMAC verification |
| `CSP_FFRAG` | 0x10 | Use fragmentation |

---

## CSP Header Encoding

### CSP 1.x Header (4 bytes, 32-bit packed)

```
Bit:  31 30 | 29 28 27 26 25 | 24 23 22 21 20 | 19 18 17 16 15 14 | 13 12 11 10 9 8 | 7 6 5 4 3 2 1 0
      ├─────┼────────────────┼────────────────┼──────────────────┼─────────────────┼─────────────────┤
      │ PRI │      SRC       │      DST       │      DPORT       │      SPORT      │      FLAGS      │
      │ 2b  │      5b        │      5b        │       6b         │       6b        │       8b        │
      └─────┴────────────────┴────────────────┴──────────────────┴─────────────────┴─────────────────┘

Max nodes: 32 (5 bits)
Max ports: 64 (6 bits)
```

### CSP 2.x Header (6 bytes, 48-bit packed)

```
Bit:  47 46 | 45 44 ... 32 | 31 30 ... 18 | 17 16 15 14 13 12 | 11 10 9 8 7 6 | 5 4 3 2 1 0
      ├─────┼──────────────┼──────────────┼───────────────────┼───────────────┼─────────────┤
      │ PRI │     DST      │     SRC      │      DPORT        │     SPORT     │    FLAGS    │
      │ 2b  │     14b      │     14b      │       6b          │      6b       │      6b     │
      └─────┴──────────────┴──────────────┴───────────────────┴───────────────┴─────────────┘

Max nodes: 16384 (14 bits)
Max ports: 64 (6 bits)
```

### Encoding Functions

```c
// Prepend CSP header to packet (before TX)
void csp_id_prepend(csp_packet_t * packet);

// Strip CSP header from packet (after RX)
int csp_id_strip(csp_packet_t * packet);
```

---

## Security Features & Packet Encoding

LibCSP supports optional security features that are applied during packet transmission. These are controlled by flags in the CSP header.

### Security Flags

| Flag | Value | Description |
|------|-------|-------------|
| `CSP_FCRC32` | 0x01 | Append CRC32 checksum (4 bytes) |
| `CSP_FHMAC` | 0x08 | Append HMAC authentication (4 bytes) |

### Encoding Order (TX Path)

Security features are applied in `csp_send_direct_iface()` (`src/csp_io.c:232-290`):

```
Application Data
       │
       ▼
[IF RDP enabled] ──► Append RDP header (5 bytes)
       │
       ▼
[IF CSP_FHMAC] ────► Append HMAC (4 bytes)
       │
       ▼
[IF CSP_FCRC32] ───► Append CRC32 (4 bytes)
       │
       ▼
Driver nexthop() ──► Prepend CSP Header (4-6 bytes)
       │
       ▼
[IF encryption] ───► Encrypt frame (optional hook)
       │
       ▼
    Wire TX
```

**Important:** The `from_me` flag ensures only originating nodes add HMAC/CRC. Forwarded packets skip this step to avoid double-wrapping.

### HMAC (4 bytes)

**Source:** `src/crypto/csp_hmac.c`

HMAC provides message authentication to verify packet integrity and authenticity.

```c
// Triggered when CSP_FHMAC flag is set
if (idout->flags & CSP_FHMAC) {
    csp_hmac_append(packet, false);
}
```

**Implementation Details:**

| Property | Value |
|----------|-------|
| Algorithm | HMAC-SHA1 |
| Full digest | 20 bytes |
| Appended bytes | 4 bytes (truncated) |
| Key length | 16 bytes |
| Location | End of `packet->data` |

**Key Functions:**

- `csp_hmac_append()` (Lines 127-152): Appends 4-byte HMAC to packet
- `csp_hmac_verify()` (Lines 154-190): Verifies and strips HMAC on RX
- `csp_hmac_set_key()`: Configure the shared HMAC key

**Operation:**

```
┌─────────────────────────────────────────────────────┐
│  HMAC-SHA1(key, packet->data[0..length])            │
│                     │                               │
│                     ▼                               │
│  20-byte digest ──► Truncate to 4 bytes             │
│                     │                               │
│                     ▼                               │
│  Append to packet->data[length], length += 4        │
└─────────────────────────────────────────────────────┘
```

### CRC32 (4 bytes)

**Source:** `src/csp_crc32.c`

CRC32 provides error detection for packet corruption.

```c
// Triggered when CSP_FCRC32 flag is set
if (idout->flags & CSP_FCRC32) {
    csp_crc32_append(packet);
}
```

**Implementation Details:**

| Property | Value |
|----------|-------|
| Algorithm | CRC32 (standard polynomial) |
| Appended bytes | 4 bytes |
| Byte order | Big-endian (network order) |
| Location | End of `packet->data` (after HMAC if present) |

**Key Functions:**

- `csp_crc32_append()` (Lines 88-112): Calculates and appends CRC32
- `csp_crc32_verify()` (Lines 114-143): Verifies and strips CRC32 on RX

**CSP Version Differences:**

- **CSP 2.1+**: CRC32 includes the CSP header in calculation
- **Legacy mode**: CRC32 on data only (backward compatible)

**Operation:**

```
┌─────────────────────────────────────────────────────┐
│  CSP 2.1+:                                          │
│  csp_id_prepend() ──► CRC32(frame_begin..length)    │
│                                                     │
│  Legacy:                                            │
│  CRC32(packet->data[0..length])                     │
│                     │                               │
│                     ▼                               │
│  Convert to big-endian (htobe32)                    │
│                     │                               │
│                     ▼                               │
│  Append to packet->data[length], length += 4        │
└─────────────────────────────────────────────────────┘
```

### Encryption (XTEA / Custom)

**Source:** `src/interfaces/csp_if_tun.c`

LibCSP provides **weak function hooks** for custom encryption. XTEA or any other cipher can be implemented by overriding these hooks.

```c
// Weak hooks - override with your implementation
__attribute__((weak))
int csp_crypto_encrypt(const uint8_t * msg_begin, uint8_t msg_len,
                       uint8_t * ciphertext_out);

__attribute__((weak))
int csp_crypto_decrypt(const uint8_t * ciphertext_in, uint8_t ciphertext_len,
                       uint8_t * msg_out);
```

**Encryption Points:**

- **TX**: Called after `csp_id_prepend()`, encrypts entire frame
- **RX**: Called before `csp_id_strip()`, decrypts entire frame

**XTEA Implementation Example:**

To use XTEA encryption, implement the hooks:

```c
#include <csp/crypto/csp_xtea.h>

int csp_crypto_encrypt(const uint8_t * msg_begin, uint8_t msg_len,
                       uint8_t * ciphertext_out) {
    memcpy(ciphertext_out, msg_begin, msg_len);
    // XTEA encrypts in 8-byte blocks
    if (csp_xtea_encrypt(ciphertext_out, msg_len) != 0) {
        return -1;
    }
    return msg_len;  // Return ciphertext length
}

int csp_crypto_decrypt(const uint8_t * ciphertext_in, uint8_t ciphertext_len,
                       uint8_t * msg_out) {
    memcpy(msg_out, ciphertext_in, ciphertext_len);
    if (csp_xtea_decrypt(msg_out, ciphertext_len) != 0) {
        return -1;
    }
    return ciphertext_len;  // Return plaintext length
}
```

**XTEA Details** (`src/crypto/csp_xtea.c`):

| Property | Value |
|----------|-------|
| Algorithm | XTEA (eXtended TEA) |
| Block size | 8 bytes |
| Key size | 16 bytes (4 × 32-bit words) |
| Rounds | 32 |

### Complete Wire Format

After all encoding steps, the packet on the wire looks like:

```
┌──────────────┬─────────────────┬───────────┬──────────┬─────────┐
│  CSP Header  │  App Data       │ RDP Hdr   │  HMAC    │  CRC32  │
│  (4-6 bytes) │  (variable)     │ (5 bytes) │ (4 bytes)│(4 bytes)│
└──────────────┴─────────────────┴───────────┴──────────┴─────────┘
   ▲ prepended    ▲ original       ▲ optional  ▲ optional ▲ optional
   (by driver)      data          (if FRDP)   (if FHMAC) (if FCRC32)
```

**Maximum Packet Sizes:**

- CSP 1.x: 4 + 251 + 5 + 4 + 4 = 268 bytes
- CSP 2.x: 6 + 249 + 5 + 4 + 4 = 268 bytes

### Decoding Order (RX Path)

On receive, security features are verified in reverse order:

```
    Wire RX
       │
       ▼
[IF encryption] ───► Decrypt frame
       │
       ▼
Driver ────────────► Strip CSP Header (csp_id_strip)
       │
       ▼
[IF CSP_FCRC32] ───► Verify & strip CRC32
       │
       ▼
[IF CSP_FHMAC] ────► Verify & strip HMAC
       │
       ▼
[IF RDP enabled] ──► Process RDP header
       │
       ▼
Application Data
```

### Security Implementation Files

| Component | File | Key Functions |
|-----------|------|---------------|
| HMAC | `src/crypto/csp_hmac.c` | `csp_hmac_append()`, `csp_hmac_verify()` |
| CRC32 | `src/csp_crc32.c` | `csp_crc32_append()`, `csp_crc32_verify()` |
| XTEA | `src/crypto/csp_xtea.c` | `csp_xtea_encrypt()`, `csp_xtea_decrypt()` |
| Crypto hooks | `src/interfaces/csp_if_tun.c` | `csp_crypto_encrypt()`, `csp_crypto_decrypt()` |
| Header pack | `src/csp_id.c` | `csp_id_prepend()`, `csp_id_strip()` |

---

## RDP (Reliable Data Protocol)

Based on **RFC 908/1151** with delayed acknowledgment extensions.

### RDP Header (5 bytes, appended to data end)

```
┌─────────────────┬───────────────────────┬───────────────────────────┐
│     flags       │       seq_nr          │        ack_nr             │
│     1 byte      │    2 bytes (BE)       │     2 bytes (BE)          │
└─────────────────┴───────────────────────┴───────────────────────────┘

Flags (lower 4 bits):
  0x08 = RDP_SYN  - Synchronize (connection init)
  0x04 = RDP_ACK  - Acknowledgment
  0x02 = RDP_EAK  - Extended ACK (out-of-order)
  0x01 = RDP_RST  - Reset (close connection)

Upper 4 bits: ephemeral counter (anti-deduplication)
```

### RDP State Machine

```
                    ┌──────────────┐
                    │    CLOSED    │
                    └──────┬───────┘
                           │
         ┌─────────────────┼─────────────────┐
         │ Client          │                 │ Server
         │ connect()       │                 │ accept()
         ▼                 │                 ▼
   ┌───────────┐           │           ┌───────────┐
   │ SYN_SENT  │──── SYN ──┼──────────►│  CLOSED   │
   └─────┬─────┘           │           └─────┬─────┘
         │                 │                 │
         │                 │           ┌─────┴─────┐
         │                 │           │ SYN_RCVD  │
         │◄─── SYN+ACK ────┼───────────┴─────┬─────┘
         │                 │                 │
         │──── ACK ────────┼────────────────►│
         ▼                 │                 ▼
   ┌───────────┐           │           ┌───────────┐
   │   OPEN    │◄──────────┼──────────►│   OPEN    │
   └─────┬─────┘           │           └─────┬─────┘
         │    (data + ACK) │                 │
         │                 │                 │
   ┌─────┴─────┐           │           ┌─────┴─────┐
   │CLOSE_WAIT │◄─── RST ──┼──────────►│CLOSE_WAIT │
   └─────┬─────┘           │           └─────┬─────┘
         │ timeout         │           timeout │
         ▼                 │                 ▼
   ┌───────────┐           │           ┌───────────┐
   │  CLOSED   │           │           │  CLOSED   │
   └───────────┘           │           └───────────┘
```

### RDP Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `window_size` | 4 | Max unacknowledged packets in flight |
| `conn_timeout` | 10000 ms | Connection timeout |
| `packet_timeout` | 1000 ms | Retransmit timeout |
| `delayed_acks` | 1 (enabled) | Enable delayed ACKs |
| `ack_timeout` | 250 ms | Max time before forced ACK |
| `ack_delay_count` | 2 | Packets received before forced ACK |

### SYN Packet Payload

SYN packets carry RDP parameters (24 bytes):

```c
packet->data32[0] = htobe32(window_size);
packet->data32[1] = htobe32(conn_timeout);
packet->data32[2] = htobe32(packet_timeout);
packet->data32[3] = htobe32(delayed_acks);
packet->data32[4] = htobe32(ack_timeout);
packet->data32[5] = htobe32(ack_delay_count);
```

### RDP Retransmission Queue

```
┌─────────────────────────────────────────────────────────┐
│                   TX Queue (global)                     │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐                 │
│  │ packet  │  │ packet  │  │ packet  │  ...            │
│  │ seq=100 │  │ seq=101 │  │ seq=102 │                 │
│  │ conn=X  │  │ conn=X  │  │ conn=Y  │                 │
│  │ ts=1000 │  │ ts=1050 │  │ ts=1100 │                 │
│  └─────────┘  └─────────┘  └─────────┘                 │
│                                                         │
│  Operations:                                            │
│  - Clone packet before TX, store in queue               │
│  - Check timeouts in csp_rdp_check_timeouts()           │
│  - Retransmit if: ts + packet_timeout < now             │
│  - Free when: seq < snd_una (acknowledged)              │
└─────────────────────────────────────────────────────────┘
```

---

## Data Flow

### TX Path

```
Application
    │
    ▼
csp_send(conn, packet)
    │
    ├─► [If RDP] csp_rdp_send()
    │       - Wait for TX window
    │       - Append RDP header (5 bytes at data end)
    │       - Clone to retransmit queue
    │       - Increment seq_nr
    │
    ▼
csp_send_direct()
    │
    ▼
csp_id_prepend()
    │   - Pack CSP header (4 or 6 bytes)
    │   - Set frame_begin, frame_length
    │
    ▼
iface->nexthop()
    │
    ▼
Driver TX (sendto, CAN write, etc.)
    │
    ▼
Physical Medium
```

### RX Path

```
Physical Medium
    │
    ▼
Driver RX (recvfrom, CAN read, etc.)
    │
    ▼
csp_qfifo_write(packet, iface)
    │
    ▼
┌─────────────────────────────────────┐
│         Router Task Loop            │
│  csp_route_work() / csp_router()    │
└─────────────────────────────────────┘
    │
    ▼
csp_id_strip()
    │   - Unpack CSP header
    │   - Populate packet->id
    │
    ▼
Routing Decision
    │
    ├─► [Not for me] → Forward via routing table
    │
    └─► [For me]
            │
            ├─► [Has RDP flag] → csp_rdp_new_packet()
            │       - Validate seq/ack numbers
            │       - Handle state machine
            │       - Remove RDP header
            │       - Handle out-of-order (RX queue)
            │
            ▼
        csp_conn_enqueue_packet()
            │
            ▼
        Connection RX Queue
            │
            ▼
        csp_read(conn) → Application
```

---

## Key Components

### Source Files

| Component | File | Description |
|-----------|------|-------------|
| **Initialization** | `src/csp_init.c` | Buffer, connection, queue init |
| **I/O Operations** | `src/csp_io.c` | send/recv/sendto/recvfrom |
| **Routing** | `src/csp_route.c` | Packet routing decisions |
| **Connections** | `src/csp_conn.c` | Connection pool management |
| **Ports** | `src/csp_port.c` | Port binding (0-63) |
| **Buffers** | `src/csp_buffer.c` | Packet buffer pool |
| **RDP** | `src/csp_rdp.c` | Reliable Data Protocol |
| **RDP Queue** | `src/csp_rdp_queue.c` | TX/RX retransmit queues |
| **Routing Queue** | `src/csp_qfifo.c` | Central FIFO queue |
| **ID Pack/Unpack** | `src/csp_id.c` | CSP header encoding |
| **Routing Table** | `src/csp_rtable_cidr.c` | CIDR-based routing |

### Interface Drivers

| Driver | File | Transport |
|--------|------|-----------|
| Loopback | `src/interfaces/csp_if_lo.c` | Virtual |
| UDP | `src/interfaces/csp_if_udp.c` | IP/UDP |
| ZMQ Hub | `src/interfaces/csp_if_zmqhub.c` | ZeroMQ (IPC) |
| CAN | `src/drivers/can/can_socketcan.c` | SocketCAN |
| USART/KISS | `src/drivers/usart/` | Serial |
| Ethernet | `src/drivers/eth/eth_linux.c` | Raw Ethernet |

### Global State (Process-Local)

```c
// All state is per-process, NOT shared between processes
static csp_conn_t arr_conn[CSP_CONN_MAX];     // Connection pool
static csp_port_t ports[CSP_PORT_MAX_BIND+2]; // Port bindings
static csp_queue_handle_t qfifo;               // Routing queue
static csp_iface_t *interfaces;                // Interface list (linked)
```

### OS Abstraction

| OS | Location | Queue Implementation |
|----|----------|---------------------|
| POSIX/Linux | `src/arch/posix/` | pthread mutex + condvar |
| FreeRTOS | `src/arch/freertos/` | Native xQueueHandle |
| Zephyr | `src/arch/zephyr/` | k_msgq |

---

## Multi-Process Architecture Evaluation

### Constraints
- **No L2 transport** (raw sockets not desired)
- **TCP/IP as IPC transport** (UDP/TCP preferred)

### Candidates Summary

| Candidate | Description | Verdict |
|-----------|-------------|---------|
| **A** | Central daemon + client library | Strong |
| **B** | UDP/TCP overlay per-app | Viable |
| **C** | Local mux sidecar + UDP on wire | **BEST** |
| **D** | L2 EtherType + kernel mux | **Eliminated** (L2) |
| **E** | TAP/VETH per app + bridge | Weak (overkill) |
| **F** | Service-bus broker | Weak (over-engineered) |

### Recommended: Candidate C

**Local Mux Sidecar + UDP on Wire**

```
┌─────────────────────────────────────────────────────────┐
│                        Host                             │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐                  │
│  │  App 1  │  │  App 2  │  │  App 3  │                  │
│  │libpycsp │  │libpycsp │  │libpycsp │                  │
│  └────┬────┘  └────┬────┘  └────┬────┘                  │
│       │            │            │                       │
│       └────────────┼────────────┘                       │
│                    │ TCP/Unix Socket IPC                │
│              ┌─────┴─────┐                              │
│              │ pycsp-muxd│  ← Thin mux daemon           │
│              │  (per-host)│                             │
│              └─────┬─────┘                              │
│                    │ UDP Port (e.g., 9700)              │
└────────────────────┼────────────────────────────────────┘
                     │
              ┌──────┴──────┐
              │   Network   │
              └─────────────┘
```

### Why Candidate C?

1. **Matches libcsp's design patterns**
   - `pycsp-muxd` maps to libcsp's central QFIFO concept
   - Apps use IPC interface (like ZMQ hub works)

2. **Uses TCP/IP as requested**
   - UDP on the wire to other hosts
   - Unix domain sockets (or TCP localhost) for local IPC

3. **Minimal complexity**
   - Pure multiplexing, not a full router
   - Can be stateless for the mux function

4. **Implementation path**
   - Reuse `csp_if_udp.c` for network side
   - Create `csp_if_ipc.c` for app-to-mux communication
   - Mux daemon is thin layer connecting the two

---

## Wire Format Summary

### Complete Packet on Wire

```
┌────────────────────────────────────────────────────────────────────┐
│                    COMPLETE WIRE FORMAT                            │
├────────────────────────────────────────────────────────────────────┤
│                                                                    │
│  ┌────────────────┬─────────────────────────┬───────────────────┐  │
│  │   CSP Header   │    Application Data     │    RDP Header     │  │
│  │   (4-6 bytes)  │      (0-251 bytes)      │    (5 bytes)      │  │
│  └────────────────┴─────────────────────────┴───────────────────┘  │
│                                                                    │
│  Total max: 4 + 251 + 5 = 260 bytes (CSP 1.x)                      │
│             6 + 249 + 5 = 260 bytes (CSP 2.x)                      │
│                                                                    │
│  Note: RDP header only present if CSP_FRDP flag set                │
│        RDP header is APPENDED to data, not prepended               │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘
```

### Byte Order
- **CSP Header**: Big-endian (network order)
- **RDP Header**: Big-endian (network order)
- **Application Data**: Application-defined

---

## References

- RFC 908 - Reliable Data Protocol
- RFC 1151 - RDP Version 2
- LibCSP GitHub: https://github.com/libcsp/libcsp
