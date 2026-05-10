import asyncio
import struct

import pycsp as csp
import pycsplink as csplink

# --- Node addresses -----------------------------------------------------------

OBC_ADDR = 1
EPS_ADDR = 2
TTC_ADDR = 5
CAM_ADDR = 6
TNC_ADDR = 9
GCS_ADDR = 10

# --- Destination ports --------------------------------------------------------

DPORT_CMP      = 0
DPORT_PING     = 1
DPORT_PS       = 2
DPORT_MEMFREE  = 3
DPORT_REBOOT   = 4
DPORT_BUF_FREE = 5
DPORT_UPTIME   = 6

# --- Gateway config -----------------------------------------------------------

HOST         = '127.0.0.1'
PORT_OBC     = 53001   # raw OBC payload wire format
PORT_CSP_TCP = 53002   # CSP-over-TCP (full CSP packets with headers)

# --- Radio link setup ---------------------------------------------------------

with open('hmac_key.txt', 'r') as f:
    hmac_key = bytes.fromhex(f.read().strip())

uplink   = csplink.AX100(hmac_key=hmac_key, crc=False, reed_solomon=True,
                         randomize=True, len_field=True, syncword=True,
                         prefill=32, tailfill=1)
downlink = csplink.AX100(hmac_key=None, crc=True, reed_solomon=False,
                         randomize=False, len_field=False, syncword=False,
                         exception=False, verbose=True)

# --- Wire format helpers ------------------------------------------------------

def _frame(data: bytes) -> bytes:
    return struct.pack('<II', 0, len(data)) + data

async def _read_frame(reader: asyncio.StreamReader) -> bytes:
    hdr = await reader.readexactly(8)
    _, length = struct.unpack('<II', hdr)
    return await reader.readexactly(length)

# --- Connected client sets ----------------------------------------------------

_obc_clients: set[asyncio.StreamWriter] = set()
_csp_clients: set[asyncio.StreamWriter] = set()

async def _broadcast_obc(payload: bytes):
    frame = _frame(payload)
    for w in list(_obc_clients):
        try:
            w.write(frame)
            await w.drain()
        except Exception:
            _obc_clients.discard(w)

async def _broadcast_csp(csp_pkt: bytes):
    frame = _frame(csp_pkt)
    for w in list(_csp_clients):
        try:
            w.write(frame)
            await w.drain()
        except Exception:
            _csp_clients.discard(w)

# --- Uplink helpers -----------------------------------------------------------

async def obc_send(payload: bytes, dst: int = OBC_ADDR):
    """Wrap raw payload in a CSP packet addressed to dst and transmit."""
    packet = csp.Packet(GCS_ADDR, dst, 7, 16, prio='norm', hmac_key=None, crc=False)
    packet.payload = payload
    await ttc.send(uplink.encode(packet))  # type: ignore[name-defined]

async def csp_send(csp_pkt: bytes):
    """Transmit a pre-formed CSP packet (header + payload) through the radio."""
    await ttc.send(uplink.encode(csp_pkt))  # type: ignore[name-defined]

# --- Client handlers ----------------------------------------------------------

async def _handle_obc_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    _obc_clients.add(writer)
    try:
        while True:
            payload = await _read_frame(reader)
            await obc_send(payload)
    except (asyncio.IncompleteReadError, ConnectionResetError):
        pass
    finally:
        _obc_clients.discard(writer)
        writer.close()

async def _handle_csp_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    _csp_clients.add(writer)
    try:
        while True:
            csp_pkt = await _read_frame(reader)
            await csp_send(csp_pkt)
    except (asyncio.IncompleteReadError, ConnectionResetError):
        pass
    finally:
        _csp_clients.discard(writer)
        writer.close()

# --- RX worker ----------------------------------------------------------------

async def _rx_worker(link: csplink.GrcLink):
    while True:
        try:
            rx = await asyncio.wait_for(link.recv(), timeout=1.0)

            if csp.HeaderV1.from_bytes(rx[0:4]).src == GCS_ADDR:  # echo - discard
                continue

            resp = downlink.decode(rx)
            if not resp:
                continue

            # Port 53001: OBC->GCS frames, payload only
            if resp.header.src == OBC_ADDR and resp.header.dst == GCS_ADDR:
                await _broadcast_obc(resp.payload)

            # Port 53002: all decoded frames with full CSP header
            raw_csp = resp.header.to_bytes() + resp.payload
            await _broadcast_csp(raw_csp)

        except asyncio.TimeoutError:
            pass
        except (KeyboardInterrupt, asyncio.CancelledError):
            break
        except ValueError as e:
            print(e)

# --- Main ---------------------------------------------------------------------

async def main():
    global ttc
    ttc = await csplink.GrcLink.connect()
    _ = asyncio.create_task(_rx_worker(ttc))

    obc_server = await asyncio.start_server(_handle_obc_client, HOST, PORT_OBC)
    csp_server = await asyncio.start_server(_handle_csp_client, HOST, PORT_CSP_TCP)

    print('Gateway started')
    print(f'  OBC TCP     {HOST}:{PORT_OBC}   (raw OBC payload, no CSP header)')
    print(f'  CSP TCP     {HOST}:{PORT_CSP_TCP}   (full CSP packets with headers)')

    async with obc_server, csp_server:
        await asyncio.gather(
            obc_server.serve_forever(),
            csp_server.serve_forever(),
        )

if __name__ == '__main__':
    asyncio.run(main())
