"""Itemised transaction charges for NSE cash INTRADAY, per the published table.

Every rate is a setting so the deployment can be pinned to the exact schedule
handed out at kickoff without a code change. Statutory defaults follow the
public 021 "Intraday" fee schedule plus the standard NSE statutory rates.
"""
from __future__ import annotations
import json
from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_HALF_UP
from .config import settings

MONEY = Decimal("0.0001")
CHARGE_COMPONENTS = ("turnover", "brokerage", "stt", "exchange_txn", "sebi", "ipft", "stamp_duty", "gst", "total")


def q(value: Decimal) -> Decimal:
    return value.quantize(MONEY, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class ChargeBreakdown:
    turnover: Decimal
    brokerage: Decimal
    stt: Decimal
    exchange_txn: Decimal
    sebi: Decimal
    ipft: Decimal
    stamp_duty: Decimal
    gst: Decimal
    total: Decimal

    def as_dict(self) -> dict:
        return {key: float(value) for key, value in asdict(self).items()}


def compute_charges(side: str, price: Decimal, qty: int) -> ChargeBreakdown:
    """Full charge breakdown for one fill. STT is sell-side, stamp duty buy-side."""
    turnover = Decimal(str(price)) * int(qty)
    is_buy = side.upper() == "BUY"
    brokerage = turnover * settings.brokerage_rate
    if settings.brokerage_cap > 0:
        brokerage = min(brokerage, settings.brokerage_cap)
    stt = Decimal("0") if is_buy else turnover * settings.stt_rate
    exchange_txn = turnover * settings.exchange_txn_rate
    sebi = turnover * settings.sebi_rate
    ipft = turnover * settings.ipft_rate
    stamp_duty = turnover * settings.stamp_duty_rate if is_buy else Decimal("0")
    gst_base = brokerage + exchange_txn + sebi + ipft
    if settings.gst_includes_stamp_duty:
        gst_base += stamp_duty
    gst = gst_base * settings.gst_rate
    lines = [brokerage, stt, exchange_txn, sebi, ipft, stamp_duty, gst]
    rounded = [q(line) for line in lines]
    total = q(sum(rounded))
    return ChargeBreakdown(turnover=q(turnover), brokerage=rounded[0], stt=rounded[1], exchange_txn=rounded[2],
                           sebi=rounded[3], ipft=rounded[4], stamp_duty=rounded[5], gst=rounded[6], total=total)


def charge_for(side: str, price: Decimal, qty: int) -> Decimal:
    return compute_charges(side, price, qty).total


def breakdown_dict(breakdown: ChargeBreakdown) -> dict[str, Decimal]:
    return {key: getattr(breakdown, key) for key in CHARGE_COMPONENTS}


def parse_breakdown(value: str | None) -> dict[str, Decimal] | None:
    """Read a persisted fee snapshot. Invalid legacy values are backfilled later."""
    try:
        payload = json.loads(value or "")
        if not isinstance(payload, dict) or not all(key in payload for key in CHARGE_COMPONENTS):
            return None
        return {key: q(Decimal(str(payload[key]))) for key in CHARGE_COMPONENTS}
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def serialize_breakdown(breakdown: ChargeBreakdown | dict[str, Decimal]) -> str:
    values = breakdown_dict(breakdown) if isinstance(breakdown, ChargeBreakdown) else breakdown
    return json.dumps({key: str(q(values[key])) for key in CHARGE_COMPONENTS}, separators=(",", ":"))


def sum_breakdowns(items) -> dict[str, Decimal]:
    totals = {key: Decimal("0") for key in CHARGE_COMPONENTS}
    for item in items:
        for key in CHARGE_COMPONENTS:
            totals[key] += item[key]
    return {key: q(value) for key, value in totals.items()}


def schedule() -> dict:
    """The active schedule, surfaced to the UI so the assumption is auditable."""
    return {
        "segment": settings.charge_segment,
        "brokerage_rate": float(settings.brokerage_rate),
        "brokerage_cap": float(settings.brokerage_cap),
        "stt_rate": float(settings.stt_rate),
        "stt_side": "SELL",
        "exchange_txn_rate": float(settings.exchange_txn_rate),
        "sebi_rate": float(settings.sebi_rate),
        "ipft_rate": float(settings.ipft_rate),
        "stamp_duty_rate": float(settings.stamp_duty_rate),
        "stamp_duty_side": "BUY",
        "gst_rate": float(settings.gst_rate),
        "gst_includes_stamp_duty": settings.gst_includes_stamp_duty,
    }
