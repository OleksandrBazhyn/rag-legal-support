"""POST/GET /profile — управління профілями користувачів."""
from __future__ import annotations

import uuid
from collections import OrderedDict

from fastapi import APIRouter, HTTPException

from api.models import UserProfile, ProfileResponse

router = APIRouter()

# In-memory сховище профілів (у продакшні - БД)
_profiles: OrderedDict[str, dict] = OrderedDict()
_MAX_PROFILES = 10_000


@router.post("/profile", response_model=ProfileResponse, summary="Зберегти профіль")
async def create_profile(profile: UserProfile) -> ProfileResponse:
    """Зберігає профіль користувача і повертає унікальний ідентифікатор."""
    if len(_profiles) >= _MAX_PROFILES:
        # Видаляємо найстаріші записи при переповненні
        oldest_key = next(iter(_profiles))
        del _profiles[oldest_key]

    profile_id = str(uuid.uuid4())
    _profiles[profile_id] = profile.model_dump()
    return ProfileResponse(profile_id=profile_id)


@router.get("/profile/{profile_id}", response_model=UserProfile, summary="Отримати профіль")
async def get_profile(profile_id: str) -> UserProfile:
    """Повертає збережений профіль за ідентифікатором."""
    data = _profiles.get(profile_id)
    if data is None:
        raise HTTPException(
            status_code=404,
            detail=f"Профіль '{profile_id}' не знайдено.",
        )
    return UserProfile(**data)
