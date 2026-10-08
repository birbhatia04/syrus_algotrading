from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "AlgoRhythm"
    database_url: str = "sqlite:///./aegis.db"
    environment: str = "SIMULATOR"
    session_hours: int = 24
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    alpaca_api_key_id: str = ""
    alpaca_api_secret_key: str = ""
    alpaca_data_feed: str = "iex"
    alpaca_default_symbol: str = "AAPL"

    model_config = SettingsConfigDict(env_file=Path(__file__).resolve().parents[2] / ".env", extra="ignore")

    @property
    def origins(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def is_alpaca_paper(self) -> bool:
        return self.environment.upper() == "ALPACA_PAPER"

    @property
    def currency(self) -> str:
        return "USD" if self.is_alpaca_paper else "INR"

    @property
    def alpaca_configured(self) -> bool:
        return bool(self.alpaca_api_key_id and self.alpaca_api_secret_key)


settings = Settings()
