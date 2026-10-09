"""Big-endian decoders from API Guide pp.16–20 (October 2026).

Full packets are sampled snapshots, not a replayable exchange tick tape.
Unknown/truncated packets fail the frame; callers reconnect and reconcile.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import struct

EPOCH_OFFSET = 315513000
FULL_LENGTH = {1: 220, 2: 234, 3: 36, 4: 262, 5: 262, 6: 262}


@dataclass(frozen=True)
class MarketTick:
    exchange: int
    token: int
    price_paise: int
    open_paise: int | None
    volume: int | None
    timestamp: datetime


def u16(data, offset):
    return struct.unpack_from(">H", data, offset)[0]


def u32(data, offset):
    return struct.unpack_from(">I", data, offset)[0]


def u64(data, offset):
    return struct.unpack_from(">Q", data, offset)[0]


def decode_market(frame: bytes, received_at: datetime | None = None) -> list[MarketTick]:
    now = received_at or datetime.now(timezone.utc)
    result = []
    offset = 0
    while offset < len(frame):
        data = frame[offset:]
        if len(data) < 2:
            raise ValueError("Truncated TC")
        tc = u16(data, 0)
        if tc == 10:
            offset += 2
            continue
        if len(data) < 4:
            raise ValueError("Truncated exchange")
        exchange = u16(data, 2)
        if tc == 1:
            length = 12
            if len(data) < length:
                raise ValueError("Truncated LTP packet")
            open_price = None
            if len(data) > length and data[length] in (ord("o"), ord("c")):
                if len(data) < 17:
                    raise ValueError("Truncated LTP snapshot")
                if data[length] == ord("o"):
                    open_price = u32(data, 13)
                length = 17
            result.append(MarketTick(exchange, u32(data, 4), u32(data, 8), open_price, None, now))
        elif tc == 3:
            length = FULL_LENGTH.get(exchange, 0)
            if not length or len(data) < length:
                raise ValueError("Truncated or unsupported full packet")
            if exchange == 2:
                token_at, price_at, open_at, volume_at, time_at = 6, 10, 50, 22, 62
            elif exchange == 3:
                token_at, price_at, open_at, volume_at, time_at = 4, 8, 16, None, 28
            elif exchange in (4, 5, 6):
                token_at, price_at, open_at, volume_at, time_at = 4, 8, 52, 24, 64
            else:
                token_at, price_at, open_at, volume_at, time_at = 4, 8, 48, 20, 60
            timestamp = datetime.fromtimestamp(u32(data, time_at) + EPOCH_OFFSET, timezone.utc)
            result.append(MarketTick(exchange, u32(data, token_at), u32(data, price_at),
                                     u32(data, open_at), u64(data, volume_at) if volume_at else None, timestamp))
        else:
            raise ValueError(f"Unexpected market transaction code {tc}")
        offset += length
    return result


def decode_order_event(frame: bytes) -> dict | None:
    if len(frame) < 2:
        raise ValueError("Truncated order frame")
    tc = u16(frame, 0)
    if tc == 10:
        return None
    if tc == 4 and len(frame) >= 46:
        order_at, qty_at, price_at, text_at = 34, 38, 42, 46
    elif tc == 8 and len(frame) >= 32:
        order_at, qty_at, price_at, text_at = 20, 24, 28, 32
    else:
        raise ValueError("Unsupported or truncated order event")
    return {
        "status": u16(frame, 2), "order_id": str(u32(frame, order_at)),
        "quantity": struct.unpack_from(">i", frame, qty_at)[0],
        "price_paise": u32(frame, price_at),
        "reason": frame[text_at:].decode("ascii", errors="replace").rstrip("\x00 "),
    }
