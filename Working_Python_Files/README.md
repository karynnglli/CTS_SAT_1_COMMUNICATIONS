# CTS SAT 1 - Ground Station Communications

Python ground station software for commanding and receiving telemetry from CTS SAT 1 over an AX.100 radio link.

```
OBC clients (:53001)  <-+->  pycsp_gateway  <->  GNU Radio (:52001)  <->  radio  <->  satellite
CSP clients (:53002)  <-|
```

## Files

| File | Description |
|------|-------------|
| `pycsp.py` | CSP v1 packet, header, HMAC/XTEA/CRC engines |
| `pycsplink.py` | AX.100 link layer - Golay24, CCSDS scrambler, Reed-Solomon, framing |
| `pycsp_gateway.py` | TCP to radio gateway |
| `radio_ax100.grc` / `.py` | GNU Radio flowgraph - USRP B210 |
| `radio_ax100_icom.grc` / `.py` | GNU Radio flowgraph - Icom transceiver |

## CSP node addresses

| Node | Address |
|------|---------|
| OBC  | 1 |
| EPS  | 2 |
| TTC  | 5 |
| CAM  | 6 |
| TNC  | 9 |
| GCS  | 10 |

## Link layer configuration

| Parameter | Uplink | Downlink |
|-----------|--------|----------|
| HMAC | yes (signed with `hmac_key`) | no |
| CRC-32C | no | yes |
| Reed-Solomon | yes | no |
| CCSDS scrambler | yes | no |
| Golay24 length field | yes | no |
| AX.100 syncword | yes | no |
| Preamble / tail | 32 x `0xAA` / 1 x `0xAA` | no |

## Installation

### 1 - RadioConda (GNU Radio + UHD)

RadioConda ships GNU Radio, UHD, gr-satellites, and all SDR tooling pre-packaged.

Download the installer from https://github.com/ryanvolz/radioconda/releases (`radioconda-*-Linux-x86_64.sh`) and run:

```bash
bash radioconda-*-Linux-x86_64.sh
source ~/radioconda/bin/activate
```

#### USRP udev rules

Allows USRP access without `sudo`:

```bash
cd ~/radioconda/lib/uhd/utils
sudo cp uhd-usrp.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger
```

#### Security limits (real-time scheduling)

Required for sustained USB throughput on the B210:

```bash
sudo cp ~/radioconda/lib/uhd/utils/uhd-usrp.conf /etc/security/limits.d/
```

Log out and back in for limits to take effect.

#### UHD firmware images

```bash
uhd_images_downloader
echo 'export UHD_IMAGES_DIR="$HOME/radioconda/share/uhd/images"' >> ~/.bashrc
source ~/.bashrc
```

Verify the radio is detected:

```bash
uhd_find_devices
```

### 2 - Python dependencies

```bash
pip install -r requirements.txt
```

### 3 - HMAC key

Generate a shared key and save it in the working directory:

```bash
python -c "import secrets; open('hmac_key.txt','w').write(secrets.token_hex(32))"
```

The same key must be programmed on the satellite TTC radio.

## Usage

### Start GNU Radio

Open `radio_ax100.grc` in GNU Radio Companion (or run `radio_ax100.py`) to start the SDR flowgraph. It exposes a TCP socket on port `52001`.

### Start the gateway

Run `pycsp_gateway.py`. The gateway connects to GRC on `:52001` and opens two TCP servers, both bound to `127.0.0.1`.

### Wire format

Both ports use the same little-endian framing:

| offset | field | type | notes |
|--------|-------|------|-------|
| 0 | version | `uint32_t` | always `0` |
| 4 | length | `uint32_t` | byte length of contents |
| 8 | contents | `bytes` | see per-port description below |

### Port 53001 - OBC access

Uplink: raw payload bytes are wrapped in a CSP packet (src=GCS, dst=OBC) and transmitted.  
Downlink: OBC->GCS frames only; the CSP header is stripped and only the payload is forwarded.

```python
import socket, struct

def send(payload: bytes):
    frame = struct.pack('<II', 0, len(payload)) + payload
    with socket.create_connection(('127.0.0.1', 53001)) as s:
        s.sendall(frame)

def recv(s: socket.socket) -> bytes:
    _, length = struct.unpack('<II', s.recv(8))
    return s.recv(length)

send(b'CTS1+hello_world()!')
send(b'CTS1+fs_list_directory(/,0,10)!')
```

### Port 53002 - CSP over TCP

Uplink: client sends a complete CSP packet (4-byte header + payload); the gateway forwards it through the radio as-is.  
Downlink: every decoded CSP frame is forwarded with its full CSP header intact.

```python
import socket, struct
import pycsp as csp

GCS_ADDR = 10
OBC_ADDR = 1

def send_csp(pkt: csp.Packet):
    raw = pkt.encode()
    frame = struct.pack('<II', 0, len(raw)) + raw
    with socket.create_connection(('127.0.0.1', 53002)) as s:
        s.sendall(frame)

def recv_csp(s: socket.socket) -> csp.Packet:
    _, length = struct.unpack('<II', s.recv(8))
    pkt = csp.Packet()
    pkt.decode(s.recv(length))
    return pkt

# Send a ping to OBC
ping = csp.Packet(src=GCS_ADDR, dst=OBC_ADDR, dport=1, sport=16, prio='norm')
ping.payload = b''
send_csp(ping)

# Receive and inspect a CSP frame
with socket.create_connection(('127.0.0.1', 53002)) as s:
    pkt = recv_csp(s)
    print(pkt)  # Src, Dst, Dport, Sport, Pri, Flags, Size
    print(pkt.payload.hex())
```
