"""POST /query і POST /query/stream — основні ендпоінти правового запиту."""
from __future__ import annotations

import json
import logging
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from api.auth import db as auth_db
from api.auth.utils import decode_token
from api.models import QueryRequest, QueryResponse

_QUERY_EXAMPLE = {
    "question": "Яка сума Bürgergeld для одинокої особи у 2024 році?",
    "profile": {
        "legal_status": "temporary_protection",
        "region": "Berlin",
        "query_category": "social_benefits",
        "language": "uk",
    },
    "chat_history": [],
}

_QUERY_RESPONSES: dict = {
    200: {
        "description": "Успішна відповідь",
        "content": {
            "application/json": {
                "example": {
                    "answer": (
                        "У 2024 році розмір <b>Bürgergeld</b> для одинокої особи "
                        "становить <b>563 євро</b> на місяць (<code>§ 20 SGB II</code>)."
                    ),
                    "sources": ["sgb_ii.txt", "aufenthaltsgesetz.txt"],
                    "has_comparison": False,
                }
            }
        },
    },
    400: {"description": "Виявлена спроба ін'єкції в запиті"},
    422: {"description": "Некоректний формат запиту (довжина, тип)"},
    429: {"description": "Денний ліміт запитів вичерпано"},
    503: {"description": "Сервіс тимчасово недоступний (ChromaDB або OpenAI)"},
}
from api.security import InjectionDetected, sanitize_for_log, validate_question
from generation.generator import generate, generate_stream
from jose import JWTError

logger = logging.getLogger(__name__)
router = APIRouter()

_bearer = HTTPBearer(auto_error=False)


def _get_optional_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict | None:
    """Повертає користувача якщо токен присутній і дійсний, інакше None."""
    if not creds:
        return None
    try:
        payload = decode_token(creds.credentials)
        user = auth_db.get_by_id(int(payload["sub"]))
        return user
    except (JWTError, KeyError, ValueError, Exception):
        return None


def _check_limit(user: dict | None) -> None:
    """Перевіряє добовий ліміт. Піднімає 429 якщо вичерпано."""
    if user is None:
        return  # анонімні (Telegram-бот) — без ліміту
    allowed, count, limit = auth_db.check_and_increment(user["id"])
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Денний ліміт {limit} запитів вичерпано. Спробуйте завтра.",
        )


@router.post(
    "/query",
    response_model=QueryResponse,
    summary="Правовий запит",
    responses=_QUERY_RESPONSES,
    openapi_extra={"requestBody": {"content": {"application/json": {"example": _QUERY_EXAMPLE}}}},
)
async def handle_query(
    body: QueryRequest,
    user: dict | None = Depends(_get_optional_user),
) -> QueryResponse:
    """Приймає правовий запит і профіль, повертає персоналізовану відповідь.

    - Виконує семантичний пошук у базі правових документів (ChromaDB, hybrid BM25+vector)
    - Формує персоналізований промпт з урахуванням правового статусу та регіону
    - Генерує відповідь через **GPT-4o-mini** з посиланнями на конкретні §§
    - Опціонально додає порівняльний контекст Україна–Німеччина

    **Авторизація:** необов'язкова. Для авторизованих користувачів діє денний ліміт запитів.
    Анонімний доступ (Telegram-бот) — без ліміту.
    """
    _check_limit(user)
    try:
        question = validate_question(body.question)
    except InjectionDetected as exc:
        logger.warning("Injection attempt blocked: %s", sanitize_for_log(body.question))
        raise HTTPException(status_code=400, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    logger.info("Query: %s", sanitize_for_log(question))

    try:
        history = [{"role": m.role, "content": m.content} for m in body.chat_history]
        result = generate(
            query=question,
            profile=body.profile,
            chat_history=history or None,
        )
        return QueryResponse(
            answer=result.answer,
            sources=result.sources,
            has_comparison=result.has_comparison,
        )
    except EnvironmentError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        logger.error("Помилка генерації: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Внутрішня помилка сервера. Спробуйте пізніше.",
        )


@router.post(
    "/query/stream",
    summary="Streaming правовий запит (SSE)",
    responses={
        200: {"description": "Потік SSE подій", "content": {"text/event-stream": {
            "example": 'data: {"token": "У 2024 році"}\n\ndata: {"done": true, "sources": ["sgb_ii.txt"], "has_comparison": false}\n\n'
        }}},
        400: {"description": "Виявлена спроба ін'єкції"},
        429: {"description": "Денний ліміт вичерпано"},
    },
    openapi_extra={"requestBody": {"content": {"application/json": {"example": _QUERY_EXAMPLE}}}},
)
async def handle_query_stream(
    body: QueryRequest,
    user: dict | None = Depends(_get_optional_user),
) -> StreamingResponse:
    """Повертає відповідь потоково у форматі Server-Sent Events (SSE).

    Ідеально для відображення тексту в реальному часі (ефект "друкування").

    Формат подій:
    - ``data: {"token": "..."}``  — черговий фрагмент тексту
    - ``data: {"done": true, "sources": [...], "has_comparison": bool}``  — кінець
    - ``data: {"error": "..."}``  — помилка
    """
    _check_limit(user)
    try:
        question_stream = validate_question(body.question)
    except InjectionDetected as exc:
        logger.warning("Streaming injection blocked: %s", sanitize_for_log(body.question))
        raise HTTPException(status_code=400, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    history = [{"role": m.role, "content": m.content} for m in body.chat_history]

    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            async for sse_line in generate_stream(
                query=question_stream,
                profile=body.profile,
                chat_history=history or None,
            ):
                yield sse_line
        except EnvironmentError as exc:
            yield "data: " + json.dumps({"error": str(exc)}, ensure_ascii=False) + "\n\n"
        except Exception as exc:
            logger.error("Streaming помилка: %s", exc, exc_info=True)
            yield "data: " + json.dumps(
                {"error": "Внутрішня помилка сервера. Спробуйте пізніше."},
                ensure_ascii=False,
            ) + "\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",      # вимикає nginx-буферизацію
            "Connection": "keep-alive",
        },
    )
