"""Run once per token expiry: python -m app.brokers.login_021.

Never executed by API/worker startup: logging in revokes other live tokens.
"""
from getpass import getpass
from pathlib import Path
import httpx
from dotenv import set_key
from ..config import settings
from .broker_021 import BASE_URL


def main():
    username = settings.broker_021_username or input("021 UCC (HACK...): ").strip()
    password = settings.broker_021_password or getpass("021 password: ")
    try:
        response = httpx.post(f"{BASE_URL}/auth/token", json={"username": username, "password": password}, timeout=15)
        payload = response.json()
        if response.is_error or not payload.get("success"):
            raise ValueError(str(payload.get("error") or "Authentication failed"))
        token = payload["data"]["accessToken"]
    except (httpx.HTTPError, ValueError, KeyError) as error:
        raise SystemExit(f"021 login failed: {type(error).__name__}. Check credentials and connectivity.") from None
    env_file = Path(__file__).resolve().parents[3] / ".env"
    set_key(str(env_file), "BROKER_021_ACCESS_TOKEN", token)
    print("Access token saved to ignored .env. Restart API and worker to load it.")
    print(f"Token expires at {payload['data'].get('expiresAt', 'next 05:00 IST')}.")


if __name__ == "__main__":
    main()
