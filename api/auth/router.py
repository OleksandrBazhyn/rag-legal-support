"""Маршрути авторизації: /auth/register, /auth/login, /auth/me, /auth/profile."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from pydantic import BaseModel, EmailStr, Field

from api.auth import db as auth_db
from api.auth.utils import create_token, hash_password, verify_password, decode_token

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["Авторизація"])

_bearer = HTTPBearer(auto_error=False)


# ── Dependency ────────────────────────────────────────────────────────────────

def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict:
    if not creds:
        raise HTTPException(status_code=401, detail="Потрібна авторизація")
    try:
        payload = decode_token(creds.credentials)
        user_id = int(payload["sub"])
    except (JWTError, KeyError, ValueError):
        raise HTTPException(status_code=401, detail="Токен недійсний або прострочений")

    user = auth_db.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=401, detail="Користувача не знайдено")
    return user


# ── Schemas ───────────────────────────────────────────────────────────────────

class RegisterIn(BaseModel):
    email: EmailStr
    username: str = Field(..., min_length=2, max_length=50)
    password: str = Field(..., min_length=8, max_length=128)


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class ProfileIn(BaseModel):
    legal_status: str   = "unknown"
    region: str         = "unknown"
    query_category: str = "other"
    language: str       = "uk"


class PurchaseIn(BaseModel):
    package: int  # кількість запитів: 10 або 50

PURCHASE_PACKAGES = {10, 50}  # дозволені пакети


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/register", response_model=TokenOut, summary="Реєстрація")
async def register(body: RegisterIn) -> TokenOut:
    if auth_db.get_by_email(body.email):
        raise HTTPException(status_code=400, detail="Email вже зареєстровано")

    user = auth_db.create_user(body.email, body.username, hash_password(body.password))
    logger.info("Новий веб-користувач: %s", body.email)
    return TokenOut(access_token=create_token(user["id"], user["email"]))


@router.post("/login", response_model=TokenOut, summary="Вхід")
async def login(body: LoginIn) -> TokenOut:
    user = auth_db.get_by_email(body.email)
    if not user or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Невірний email або пароль")
    return TokenOut(access_token=create_token(user["id"], user["email"]))


@router.get("/me", summary="Поточний користувач")
async def me(user: dict = Depends(get_current_user)) -> dict:
    return {
        "id":       user["id"],
        "email":    user["email"],
        "username": user["username"],
        "profile": {
            "legal_status":   user["legal_status"],
            "region":         user["region"],
            "query_category": user["query_category"],
            "language":       user["language"],
        },
        "usage": auth_db.get_usage(user["id"]),
    }


@router.put("/profile", summary="Оновити профіль")
async def update_profile(
    body: ProfileIn,
    user: dict = Depends(get_current_user),
) -> dict:
    auth_db.update_profile(
        user["id"],
        body.legal_status,
        body.region,
        body.query_category,
        body.language,
    )
    return {"status": "ok"}


@router.post("/purchase", summary="Демо-покупка додаткових запитів")
async def purchase(
    body: PurchaseIn,
    user: dict = Depends(get_current_user),
) -> dict:
    if body.package not in PURCHASE_PACKAGES:
        raise HTTPException(
            status_code=400,
            detail=f"Дозволені пакети: {sorted(PURCHASE_PACKAGES)}",
        )
    usage = auth_db.add_bonus_queries(user["id"], body.package)
    logger.info("Purchase: user=%s added %d bonus queries", user["email"], body.package)
    return {"status": "ok", "added": body.package, "usage": usage}
