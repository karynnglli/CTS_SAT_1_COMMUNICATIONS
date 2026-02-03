# LibCSP Cheatsheet

## Packet Structure

```
csp_packet_t
┌────────────────────────────────────────────────────────┐
│  metadata (id, length, frame_begin, frame_length...)   │
├──────────────┬─────────────────────────────────────────┤
│  header[8]   │              data[256]                  │
│  (prepend)   │           (payload + trailers)          │
└──────────────┴─────────────────────────────────────────┘
       ▲                          ▲
  frame_begin                  data ptr
```

## Wire Format

```
┌────────────┬──────────────┬─────────┬────────┬───────┐
│ CSP Header │   App Data   │   RDP   │  HMAC  │ CRC32 │
│  4-6 bytes │   variable   │ 5 bytes │ 4 bytes│4 bytes│
└────────────┴──────────────┴─────────┴────────┴───────┘
  prepended      original     optional  optional optional
```

## CSP Header Formats

**CSP 1.x (4 bytes)** - 32 nodes max

```
│ PRI │  SRC  │  DST  │ DPORT │ SPORT │  FLAGS  │
│ 2b  │  5b   │  5b   │  6b   │  6b   │   8b    │
```

**CSP 2.x (6 bytes)** - 16384 nodes max

```
│ PRI │   DST   │   SRC   │ DPORT │ SPORT │ FLAGS │
│ 2b  │  14b    │  14b    │  6b   │  6b   │  6b   │
```

## Flags

| Flag | Value | Effect |
|------|-------|--------|
| `CSP_FCRC32` | 0x01 | +4 bytes CRC32 |
| `CSP_FRDP` | 0x02 | +5 bytes RDP header |
| `CSP_FHMAC` | 0x08 | +4 bytes HMAC |
| `CSP_FFRAG` | 0x10 | Enable fragmentation |

## TX Encoding Order

```
App Data
   │
   ▼
┌──────────────────────────┐
│ 1. RDP header (5B)       │  ← if CSP_FRDP
├──────────────────────────┤
│ 2. HMAC append (4B)      │  ← if CSP_FHMAC
├──────────────────────────┤
│ 3. CRC32 append (4B)     │  ← if CSP_FCRC32
├──────────────────────────┤
│ 4. CSP header prepend    │  ← always (4-6B)
├──────────────────────────┤
│ 5. Encrypt (optional)    │  ← custom hook
└──────────────────────────┘
   │
   ▼
 Wire
```

## RX Decoding Order

```
 Wire
   │
   ▼
┌──────────────────────────┐
│ 1. Decrypt (optional)    │  ← custom hook
├──────────────────────────┤
│ 2. CSP header strip      │  ← always
├──────────────────────────┤
│ 3. CRC32 verify (4B)     │  ← if CSP_FCRC32
├──────────────────────────┤
│ 4. HMAC verify (4B)      │  ← if CSP_FHMAC
├──────────────────────────┤
│ 5. RDP process (5B)      │  ← if CSP_FRDP
└──────────────────────────┘
   │
   ▼
App Data
```

## Security Summary

| Feature | Algorithm | Size | Key Size |
|---------|-----------|------|----------|
| HMAC | SHA1 (truncated) | 4B | 16B |
| CRC32 | Standard poly | 4B | - |
| XTEA | 32 rounds | 8B blocks | 16B |

## RDP Header (5 bytes)

```
┌─────────┬──────────┬──────────┐
│  flags  │  seq_nr  │  ack_nr  │
│  1 byte │ 2B (BE)  │ 2B (BE)  │
└─────────┴──────────┴──────────┘

Flags: SYN=0x08  ACK=0x04  EAK=0x02  RST=0x01
```

## Key Functions

| Operation | Function | File |
|-----------|----------|------|
| Send packet | `csp_send()` | csp_io.c |
| Read packet | `csp_read()` | csp_io.c |
| Prepend header | `csp_id_prepend()` | csp_id.c |
| Strip header | `csp_id_strip()` | csp_id.c |
| Append HMAC | `csp_hmac_append()` | csp_hmac.c |
| Verify HMAC | `csp_hmac_verify()` | csp_hmac.c |
| Append CRC | `csp_crc32_append()` | csp_crc32.c |
| Verify CRC | `csp_crc32_verify()` | csp_crc32.c |
| XTEA encrypt | `csp_xtea_encrypt()` | csp_xtea.c |
| XTEA decrypt | `csp_xtea_decrypt()` | csp_xtea.c |

## Layer Stack

```
┌─────────────────────────────┐
│  APPLICATION (csp_io.c)     │  send/read/connect/accept
├─────────────────────────────┤
│  TRANSPORT (csp_rdp.c)      │  RDP reliable / UDP-like
├─────────────────────────────┤
│  NETWORK (csp_route.c)      │  QFIFO → Router → Routing Table
├─────────────────────────────┤
│  INTERFACE (csp_if_*.c)     │  Loopback/CAN/KISS/UDP/ZMQ
├─────────────────────────────┤
│  PHYSICAL                   │  CAN Bus/Serial/Ethernet/ZeroMQ
└─────────────────────────────┘
```

## Quick Reference

```c
// Connect and send
csp_conn_t *conn = csp_connect(CSP_PRIO_NORM, dest, port, timeout, CSP_O_RDP);
csp_packet_t *packet = csp_buffer_get(size);
memcpy(packet->data, payload, size);
packet->length = size;
csp_send(conn, packet);
csp_close(conn);

// Server receive
csp_socket_t sock;
csp_bind(&sock, port);
csp_listen(&sock, backlog);
csp_conn_t *conn = csp_accept(&sock, timeout);
csp_packet_t *packet = csp_read(conn, timeout);
// use packet->data, packet->length
csp_buffer_free(packet);
csp_close(conn);
```

## Connection Options

| Option | Value | Description |
|--------|-------|-------------|
| `CSP_O_NONE` | 0x00 | No options |
| `CSP_O_RDP` | 0x01 | Reliable delivery |
| `CSP_O_HMAC` | 0x04 | HMAC authentication |
| `CSP_O_CRC32` | 0x08 | CRC32 checksum |

---

## Architecture

### Layered Architecture

```
┌───────────────────────────────────────────────────────────────┐
│                      APPLICATION LAYER                        │
│  ┌──────────┐  ┌──────────┐  ┌────────────────────────────┐   │
│  │  Server  │  │  Client  │  │  Services (Ports 0-7)      │   │
│  │ accept() │  │ connect()│  │  CMP, PING, PS, UPTIME...  │   │
│  └────┬─────┘  └────┬─────┘  └─────────────┬──────────────┘   │
│       └─────────────┴──────────────────────┘                  │
│                          │                                    │
│  ┌───────────────────────┴────────────────────────────────┐   │
│  │              SOCKET LAYER (csp_io.c)                   │   │
│  │  csp_socket_t ←→ csp_conn_t (connection pool)          │   │
│  └────────────────────────────────────────────────────────┘   │
└───────────────────────────────────────────────────────────────┘
                               │
┌───────────────────────────────────────────────────────────────┐
│                      TRANSPORT LAYER                          │
│  ┌─────────────────────────┐  ┌─────────────────────────┐     │
│  │  RDP (Reliable)         │  │  Connectionless         │     │
│  │  • Sequencing           │  │  • No guarantees        │     │
│  │  • Retransmit           │  │  • Lower overhead       │     │
│  └─────────────────────────┘  └─────────────────────────┘     │
│  Security: HMAC | CRC32 (per-connection flags)                │
└───────────────────────────────────────────────────────────────┘
                               │
┌───────────────────────────────────────────────────────────────┐
│                       NETWORK LAYER                           │
│  ┌────────────────────────────────────────────────────────┐   │
│  │           ROUTING QUEUE (QFIFO) - all packets here     │   │
│  └────────────────────────┬───────────────────────────────┘   │
│                           ▼                                   │
│  ┌────────────────────────────────────────────────────────┐   │
│  │  ROUTER: dst==me? ──► Port Binding ──► Deliver         │   │
│  │              │                                         │   │
│  │              └──► Routing Table (CIDR) ──► Forward     │   │
│  └────────────────────────────────────────────────────────┘   │
└───────────────────────────────────────────────────────────────┘
                               │
┌───────────────────────────────────────────────────────────────┐
│                      INTERFACE LAYER                          │
│      ┌─────────┬─────────┬─────────┬─────────┬─────────┐      │
│      │Loopback │   CAN   │  KISS   │   UDP   │   ZMQ   │      │
│      │         │socketcan│  USART  │         │   Hub   │      │
│      └─────────┴─────────┴─────────┴─────────┴─────────┘      │
└───────────────────────────────────────────────────────────────┘
                               │
┌───────────────────────────────────────────────────────────────┐
│                       PHYSICAL LAYER                          │
│        CAN Bus  │  Serial/UART  │  Ethernet  │  ZeroMQ        │
└───────────────────────────────────────────────────────────────┘
```

### TX Path (Detailed)

```
Application
    │
    │ csp_send(conn, packet)
    ▼
┌─────────────────────────────────────┐
│  [IF RDP] csp_rdp_send()            │
│    • Wait for TX window             │
│    • Append RDP header (5B)         │
│    • Clone to retransmit queue      │
│    • Increment seq_nr               │
└──────────────────┬──────────────────┘
                   ▼
┌─────────────────────────────────────┐
│  csp_send_direct()                  │
│    • [IF HMAC] csp_hmac_append()    │
│    • [IF CRC32] csp_crc32_append()  │
└──────────────────┬──────────────────┘
                   ▼
┌─────────────────────────────────────┐
│  csp_id_prepend()                   │
│    • Pack CSP header (4-6B)         │
│    • Set frame_begin, frame_length  │
└──────────────────┬──────────────────┘
                   ▼
┌─────────────────────────────────────┐
│  iface->nexthop()                   │
│    • Driver-specific TX             │
│    • [Optional] Encrypt             │
└──────────────────┬──────────────────┘
                   ▼
            Physical Medium
```

### RX Path (Detailed)

```
            Physical Medium
                   │
                   ▼
┌─────────────────────────────────────┐
│  Driver RX                          │
│    • [Optional] Decrypt             │
│    • Receive frame                  │
└──────────────────┬──────────────────┘
                   ▼
┌─────────────────────────────────────┐
│  csp_qfifo_write(packet, iface)     │
│    • Queue to central FIFO          │
└──────────────────┬──────────────────┘
                   ▼
┌─────────────────────────────────────┐
│  Router Task: csp_route_work()      │
│    • csp_id_strip() - unpack header │
│    • [IF CRC32] csp_crc32_verify()  │
│    • [IF HMAC] csp_hmac_verify()    │
└──────────────────┬──────────────────┘
                   ▼
┌─────────────────────────────────────┐
│  Routing Decision                   │
│    • dst == me? ──► Deliver local   │
│    • dst != me? ──► Forward         │
└──────────────────┬──────────────────┘
                   ▼ (if local)
┌─────────────────────────────────────┐
│  [IF RDP] csp_rdp_new_packet()      │
│    • Validate seq/ack               │
│    • Handle state machine           │
│    • Strip RDP header               │
└──────────────────┬──────────────────┘
                   ▼
┌─────────────────────────────────────┐
│  csp_conn_enqueue_packet()          │
│    • Queue to connection RX         │
└──────────────────┬──────────────────┘
                   ▼
            csp_read(conn) → App
```

### RDP State Machine

```
                        ┌────────────┐
                        │   CLOSED   │
                        └─────┬──────┘
                              │
            ┌─────────────────┼─────────────────┐
            │                 │                 │
     Client │                 │                 │ Server
     connect()                │                 accept()
            │                 │                 │
            ▼                 │                 ▼
      ┌───────────┐           │           ┌───────────┐
      │ SYN_SENT  │           │           │  (listen) │
      └─────┬─────┘           │           └─────┬─────┘
            │                 │                 │
            │ ────── SYN ─────┼────────────────►│
            │                 │                 │
            │                 │           ┌─────┴─────┐
            │                 │           │ SYN_RCVD  │
            │                 │           └─────┬─────┘
            │                 │                 │
            │◄──── SYN+ACK ───┼─────────────────│
            │                 │                 │
            │ ────── ACK ─────┼────────────────►│
            │                 │                 │
            ▼                 │                 ▼
      ┌───────────┐           │           ┌───────────┐
      │   OPEN    │◄──────────┼──────────►│   OPEN    │
      └─────┬─────┘           │           └─────┬─────┘
            │                 │                 │
            │◄──── DATA+ACK ──┼────────────────►│
            │                 │                 │
            │ ────── RST ─────┼────────────────►│
            │                 │                 │
            ▼                 │                 ▼
      ┌───────────┐           │           ┌───────────┐
      │CLOSE_WAIT │           │           │CLOSE_WAIT │
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
| `window_size` | 4 | Max unacked packets |
| `conn_timeout` | 10000 ms | Connection timeout |
| `packet_timeout` | 1000 ms | Retransmit timeout |
| `delayed_acks` | 1 | Enable delayed ACKs |
| `ack_timeout` | 250 ms | Max ACK delay |
| `ack_delay_count` | 2 | Packets before ACK |

### RDP Retransmit Queue

```text
┌─────────────────────────────────────────────────┐
│              TX Queue (global)                  │
│  ┌────────┐  ┌────────┐  ┌────────┐             │
│  │ pkt    │  │ pkt    │  │ pkt    │  ...        │
│  │ seq=10 │  │ seq=11 │  │ seq=12 │             │
│  │ ts=100 │  │ ts=150 │  │ ts=200 │             │
│  └────────┘  └────────┘  └────────┘             │
│                                                 │
│  • Clone before TX, store in queue              │
│  • Retransmit if: now > ts + packet_timeout     │
│  • Free when: seq < snd_una (acknowledged)      │
└─────────────────────────────────────────────────┘
```
