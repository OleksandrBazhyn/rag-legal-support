"""Утиліти: хешування паролю + JWT."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import bcrypt
from jose import JWTError, jwt

_SECRET = os.getenv("JWT_SECRET", "CHANGE_ME_IN_PRODUCTION_32chars+")
_ALGO   = "HS256"
_EXPIRE_HOURS = 24


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def create_token(user_id: int, email: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=_EXPIRE_HOURS)
    return jwt.encode(
        {"sub": str(user_id), "email": email, "exp": expire},
        _SECRET, algorithm=_ALGO,
    )


def decode_token(token: str) -> dict:
    """Raises JWTError якщо токен недійсний або прострочений."""
    return jwt.decode(token, _SECRET, algorithms=[_ALGO])
