from pathlib import Path
from datetime import time
from decimal import Decimal
from typing import Literal
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    app_name: str = "AlgoRhythm"
    database_url: str = "sqlite:///./aegis.db"
    session_hours: int = 24
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    environment: Literal["SIMULATOR", "021_SANDBOX"] = "SIMULATOR"
    broker_021_access_token: str = ""
    broker_021_username: str = ""
    broker_021_password: str = ""
    broker_021_account_id: int = 1
    broker_021_default_symbol: str = "RELIANCE"
    market_stale_seconds: int = 15
    # Tradeable session (IST). The 021 sandbox feed stays open outside NSE hours,
    # so deployments widen this window instead of hardcoding real market hours.
    market_open_ist: str = "09:15"
    market_close_ist: str = "15:15"
    # Seconds after market open during which the clock-based entry may fire.
    time_entry_window_seconds: int = 60
    # The 021 sandbox feed can emit a frozen/implausible tick timestamp while
    # prices still update; beyond this skew the worker uses receipt time instead.
    tick_time_tolerance_seconds: int = 60
    # Itemised NSE cash INTRADAY charge schedule (see app/charges.py). Rates are
    # configurable so the exact published kickoff table can be pinned without a
    # code change; defaults follow the public 021 Intraday fee schedule plus the
    # standard NSE statutory rates.
    charge_segment: str = "EQUITY_INTRADAY"
    brokerage_rate: Decimal = Field(default=Decimal("0.0003"), ge=0, le=1)
    brokerage_cap: Decimal = Field(default=Decimal("20"), ge=0)
    stt_rate: Decimal = Field(default=Decimal("0.00025"), ge=0, le=1)
    exchange_txn_rate: Decimal = Field(default=Decimal("0.0000297"), ge=0, le=1)
    sebi_rate: Decimal = Field(default=Decimal("0.000001"), ge=0, le=1)
    ipft_rate: Decimal = Field(default=Decimal("0.000001"), ge=0, le=1)
    stamp_duty_rate: Decimal = Field(default=Decimal("0.00003"), ge=0, le=1)
    gst_rate: Decimal = Field(default=Decimal("0.18"), ge=0, le=1)
    # 021's published note: "GST will be charged at 18% on brokerage, stamp duty,
    # exchange transaction charges and Investor Protection Fund Trust Charges."
    gst_includes_stamp_duty: bool = True

    @property
    def is_021(self) -> bool:
        return self.environment == "021_SANDBOX"

    @property
    def market_open(self) -> time:
        return time.fromisoformat(self.market_open_ist)

    @property
    def market_close(self) -> time:
        return time.fromisoformat(self.market_close_ist)

    @property
    def time_entry_deadline(self) -> time:
        minutes = self.market_open.hour * 60 + self.market_open.minute + self.time_entry_window_seconds // 60
        if minutes >= 24 * 60:
            return time(23, 59, 59)
        return time(minutes // 60, minutes % 60)

    model_config = SettingsConfigDict(env_file=Path(__file__).resolve().parents[2] / ".env", extra="ignore")

    @property
    def origins(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def currency(self) -> str:
        return "INR"

settings = Settings()
