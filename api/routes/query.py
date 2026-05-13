"""POST /query — основний ендпоінт правового запиту."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from api.models import QueryRequest, QueryResponse
from generation.generator import generate

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/query", response_model=QueryResponse, summary="Правовий запит")
async def handle_query(body: QueryRequest) -> QueryResponse:
    """Приймає правовий запит і профіль, повертає персоналізовану відповідь.

    - Виконує семантичний пошук у базі правових документів
    - Формує персоналізований промпт з урахуванням статусу та регіону
    - Генерує відповідь через GPT-4o-mini
    - Опціонально додає порівняльний контекст Україна–Німеччина
    """
    try:
        result = generate(
            query=body.question,
            profile=body.profile,
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
