from pathlib import Path
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
    assumed_charge_rate: Decimal = Field(default=Decimal("0.0005"), ge=0, le=1)

    @property
    def is_021(self) -> bool:
        return self.environment == "021_SANDBOX"

    model_config = SettingsConfigDict(env_file=Path(__file__).resolve().parents[2] / ".env", extra="ignore")

    @property
    def origins(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def currency(self) -> str:
        return "INR"

settings = Settings()
