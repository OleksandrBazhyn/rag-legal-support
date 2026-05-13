"""GET /health — перевірка стану сервісу."""
from __future__ import annotations

import os

from fastapi import APIRouter

from api.models import HealthResponse, ReindexResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse, summary="Перевірка стану")
async def health_check() -> HealthResponse:
    """Перевіряє доступність ChromaDB та наявність OpenAI API ключа."""
    chroma_status = _check_chroma()
    openai_status = _check_openai()
    overall = "ok" if chroma_status == "ok" and openai_status == "ok" else "degraded"
    return HealthResponse(status=overall, chroma=chroma_status, openai=openai_status)


@router.post("/admin/reindex", response_model=ReindexResponse, summary="Переіндексувати документи")
async def reindex() -> ReindexResponse:
    """Запускає повне переіндексування всіх документів у ChromaDB."""
    try:
        from ingestion.indexer import rebuild_index
        count = rebuild_index()
        return ReindexResponse(status="ok", documents_indexed=count)
    except Exception as exc:
        return ReindexResponse(status=f"помилка: {exc}", documents_indexed=0)


def _check_chroma() -> str:
    try:
        import chromadb
        from chromadb.config import Settings

        host = os.getenv("CHROMA_HOST", "localhost")
        port = int(os.getenv("CHROMA_PORT", "8001"))
        try:
            client = chromadb.HttpClient(
                host=host, port=port,
                settings=Settings(anonymized_telemetry=False),
            )
            client.heartbeat()
            return "ok"
        except Exception:
            from pathlib import Path
            chroma_dir = Path(__file__).parent.parent.parent / "chroma_db"
            client = chromadb.PersistentClient(
                path=str(chroma_dir),
                settings=Settings(anonymized_telemetry=False),
            )
            client.heartbeat()
            return "ok (local)"
    except Exception as exc:
        return f"error: {exc}"


def _check_openai() -> str:
    key = os.getenv("OPENAI_API_KEY", "")
    if not key:
        return "no api key"
    if key.startswith("sk-"):
        return "ok"
    return "key format unknown"
