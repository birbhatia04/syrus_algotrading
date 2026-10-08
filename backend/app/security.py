import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional
from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession
from .config import settings
from .database import get_db
from .models import Account, Session, User


def hash_password(password: str, salt: Optional[bytes] = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310_000)
    return f"{salt.hex()}:{digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    salt_hex, expected = stored.split(":", 1)
    actual = hash_password(password, bytes.fromhex(salt_hex)).split(":", 1)[1]
    return hmac.compare_digest(actual, expected)


def create_session(db: DBSession, user_id: int) -> str:
    raw = secrets.token_urlsafe(32)
    db.add(Session(user_id=user_id, token_hash=hashlib.sha256(raw.encode()).hexdigest(), expires_at=datetime.now(timezone.utc) + timedelta(hours=settings.session_hours)))
    db.commit()
    return raw


def current_user(authorization: str = Header(default=""), db: DBSession = Depends(get_db)) -> User:
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Authentication required")
    token_hash = hashlib.sha256(authorization[7:].encode()).hexdigest()
    session = db.scalar(select(Session).where(Session.token_hash == token_hash))
    now = datetime.now(timezone.utc)
    if not session or session.expires_at.replace(tzinfo=timezone.utc) < now:
        raise HTTPException(401, "Session expired")
    user = db.get(User, session.user_id)
    if not user:
        raise HTTPException(401, "Unknown user")
    return user


def user_account(user: User, db: DBSession) -> Account:
    account = db.scalar(select(Account).where(Account.user_id == user.id))
    if not account:
        raise HTTPException(404, "Account not found")
    return account
