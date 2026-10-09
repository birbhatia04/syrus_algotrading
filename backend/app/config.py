from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "AlgoRhythm"
    database_url: str = "sqlite:///./aegis.db"
    environment: str = "SIMULATOR"
    session_hours: int = 24
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    upstox_analytics_token: str = ""
    upstox_sandbox_access_token: str = ""
    upstox_default_symbol: str = "INFY"
    upstox_default_instrument_key: str = "NSE_EQ|INE009A01021"

    model_config = SettingsConfigDict(env_file=Path(__file__).resolve().parents[2] / ".env", extra="ignore")

    @property
    def origins(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def is_upstox_sandbox(self) -> bool:
        return self.environment.upper() == "UPSTOX_SANDBOX"

    @property
    def currency(self) -> str:
        return "INR"

    @property
    def upstox_configured(self) -> bool:
        return bool(self.upstox_analytics_token and self.upstox_sandbox_access_token)


settings = Settings()
