"""GET /health, POST /admin/reindex, POST /admin/refresh-data."""
from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import APIRouter

from api.models import HealthResponse, ReindexResponse, RefreshDataResponse

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/health", response_model=HealthResponse, summary="Перевірка стану")
async def health_check() -> HealthResponse:
    """Перевіряє доступність ChromaDB та наявність OpenAI API ключа."""
    chroma_status = _check_chroma()
    openai_status = _check_openai()
    overall = "ok" if chroma_status == "ok" and openai_status == "ok" else "degraded"
    return HealthResponse(status=overall, chroma=chroma_status, openai=openai_status)


@router.post("/admin/reindex", response_model=ReindexResponse, summary="Повне переіндексування")
async def reindex() -> ReindexResponse:
    """Запускає повне переіндексування всіх документів у ChromaDB."""
    import asyncio
    loop = asyncio.get_event_loop()
    try:
        from ingestion.indexer import rebuild_index
        from ingestion.index_state import mark_files_indexed

        data_dir = Path(__file__).parent.parent.parent / "data"
        count = await loop.run_in_executor(None, lambda: rebuild_index(data_dir))

        all_files = list(data_dir.rglob("*.txt"))
        await loop.run_in_executor(None, lambda: mark_files_indexed(all_files))

        return ReindexResponse(status="ok", documents_indexed=count)
    except Exception as exc:
        logger.error("Помилка reindex: %s", exc, exc_info=True)
        return ReindexResponse(status=f"помилка: {exc}", documents_indexed=0)


@router.post(
    "/admin/refresh-data",
    response_model=RefreshDataResponse,
    summary="Оновлення даних + переіндексація",
)
async def refresh_data() -> RefreshDataResponse:
    """Завантажує оновлені правові документи та переіндексує змінені файли.

    Не блокує інші запити — виконується у thread pool.
    Повертає список оновлених файлів та кількість нових чанків.
    """
    import asyncio
    loop = asyncio.get_event_loop()
    try:
        updated_files, reindexed_count = await loop.run_in_executor(
            None, _collect_and_reindex
        )
        return RefreshDataResponse(
            status="ok",
            updated_files=updated_files,
            reindexed=reindexed_count,
        )
    except Exception as exc:
        logger.error("Помилка refresh-data: %s", exc, exc_info=True)
        return RefreshDataResponse(
            status=f"помилка: {exc}",
            updated_files=[],
            reindexed=0,
        )


def _collect_and_reindex() -> tuple[list[str], int]:
    """Синхронно запускає update збору даних та переіндексацію змінених файлів.

    Returns:
        (список імен оновлених файлів, кількість нових чанків)
    """
    from data_collector.sources import DOCUMENTS, SourceKind
    from data_collector.state_manager import StateManager
    from data_collector.downloader import fetch_kmein, fetch_eurlex, fetch_rada
    from data_collector.converter import md_to_text, html_to_text
    from data_collector.validator import validate
    from ingestion.indexer import reindex_files, _get_chroma_client
    from ingestion.index_state import mark_files_indexed

    data_dir = Path(__file__).parent.parent.parent / "data"
    state = StateManager()
    updated_paths: list[Path] = []

    for doc in DOCUMENTS:
        output_file = data_dir / doc.output_path
        output_file.parent.mkdir(parents=True, exist_ok=True)

        if doc.kind == SourceKind.KMEIN:
            raw, status = fetch_kmein(doc.output_path, doc.law_matcher, state, force=False)
            convert_fn = md_to_text
        elif doc.kind == SourceKind.EURLEX:
            raw, status = fetch_eurlex(doc.output_path, doc.eurlex_url, state, force=False)
            convert_fn = html_to_text
        elif doc.kind == SourceKind.RADA:
            raw, status = fetch_rada(doc.output_path, doc.rada_nreg, state, force=False)
            convert_fn = lambda b: b.decode("utf-8", errors="replace") if isinstance(b, bytes) else b
        else:
            continue

        if status != "downloaded" or raw is None:
            continue

        try:
            text = convert_fn(raw)
        except Exception as exc:
            logger.error("Конвертація %s: %s", doc.output_path, exc)
            continue

        result = validate(text, source_kind=doc.kind.value)
        if not result:
            logger.warning("Валідація %s: %s", doc.output_path, result.reason)
            continue

        output_file.write_text(text, encoding="utf-8")
        state.set_doc(doc.output_path, is_valid=True, file_size_bytes=len(text.encode()))
        updated_paths.append(output_file)

    state.save()

    if not updated_paths:
        return [], 0

    client = _get_chroma_client()
    count = reindex_files(updated_paths, data_dir=data_dir, client=client)
    mark_files_indexed(updated_paths)

    return [p.name for p in updated_paths], count


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
            # Використовуємо той самий ASCII-безпечний шлях що й indexer/retriever
            _default = Path.home() / ".chroma_rag_legal"
            chroma_dir = Path(os.getenv("CHROMA_PERSIST_DIR", str(_default)))
            chroma_dir.mkdir(parents=True, exist_ok=True)
            client = chromadb.PersistentClient(
                path=str(chroma_dir),
                settings=Settings(anonymized_telemetry=False),
            )
            client.heartbeat()
            return "ok"
    except Exception as exc:
        return f"error: {exc}"


def _check_openai() -> str:
    key = os.getenv("OPENAI_API_KEY", "")
    if not key:
        return "no api key"
    if key.startswith("sk-"):
        return "ok"
    return "key format unknown"
