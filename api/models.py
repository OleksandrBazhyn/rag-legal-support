"""Pydantic-моделі для FastAPI."""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class LegalStatus(str, Enum):
    temporary_protection = "temporary_protection"
    residence_permit = "residence_permit"
    asylum_seeker = "asylum_seeker"
    unknown = "unknown"


class QueryCategory(str, Enum):
    residence = "residence"
    social_benefits = "social_benefits"
    employment = "employment"
    education = "education"
    healthcare = "healthcare"
    other = "other"


class Language(str, Enum):
    uk = "uk"
    de = "de"
    en = "en"


class UserProfile(BaseModel):
    legal_status: LegalStatus = Field(
        default=LegalStatus.unknown,
        description="Правовий статус у Німеччині",
    )
    region: str = Field(
        default="unknown",
        description="Федеральна земля (Bayern, Berlin, NRW тощо) або 'unknown'",
    )
    query_category: QueryCategory = Field(
        default=QueryCategory.other,
        description="Категорія правового запиту",
    )
    language: Language = Field(
        default=Language.uk,
        description="Мова відповіді: uk, de, en",
    )

    model_config = {"use_enum_values": True}


class ChatMessage(BaseModel):
    # Literal-тип блокує ін'єкцію "system" ролі через chat_history
    role: Literal["user", "assistant"] = Field(..., description="'user' або 'assistant'")
    content: str = Field(..., description="Текст повідомлення")


class QueryRequest(BaseModel):
    question: str = Field(
        ...,
        min_length=3,
        max_length=2000,
        description="Правовий запит користувача (3–2000 символів)",
    )
    profile: UserProfile = Field(default_factory=UserProfile)
    chat_history: list[ChatMessage] = Field(
        default_factory=list,
        max_length=10,
        description="Останні повідомлення діалогу (до 10)",
    )


class QueryResponse(BaseModel):
    answer: str
    sources: list[str]
    has_comparison: bool


class ProfileResponse(BaseModel):
    profile_id: str


class HealthResponse(BaseModel):
    status: str
    chroma: str
    openai: str


class ReindexResponse(BaseModel):
    status: str
    documents_indexed: int


class RefreshDataResponse(BaseModel):
    status: str
    updated_files: list[str]
    reindexed: int
