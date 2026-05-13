"""FastAPI додаток — правова підтримка для українців у Німеччині."""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# Завантаження .env
load_dotenv(Path(__file__).parent.parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

from api.routes.query import router as query_router
from api.routes.profile import router as profile_router
from api.routes.health import router as health_router

app = FastAPI(
    title="Система правової підтримки для українців у Німеччині",
    description=(
        "RAG-система, що надає персоналізовані відповіді на правові запити "
        "українських громадян на території ФРН з урахуванням порівняльного "
        "контексту між правовими системами України та Німеччини."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(query_router, tags=["Запити"])
app.include_router(profile_router, tags=["Профіль"])
app.include_router(health_router, tags=["Система"])

# Статичні файли (веб-інтерфейс)
web_dir = Path(__file__).parent.parent / "web"
if web_dir.exists():
    app.mount("/web", StaticFiles(directory=str(web_dir), html=True), name="web")


@app.get("/", include_in_schema=False)
async def root():
    return {
        "service": "Правова підтримка для українців у Німеччині",
        "docs": "/docs",
        "health": "/health",
        "web": "/web/",
    }


# ─── Startup: розумний re-index ──────────────────────────────────────────────

@app.on_event("startup")
async def startup_event() -> None:
    """При старті API: перевіряє зміни у data/ та переіндексує лише змінені файли."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _smart_reindex)
    # Запуск фонового щоденного оновлення
    asyncio.create_task(_daily_update_loop())


def _smart_reindex() -> None:
    """Синхронна логіка розумного re-index (виконується в thread pool)."""
    from ingestion.index_state import find_changed_files, mark_files_indexed, load_index_state
    from ingestion.indexer import rebuild_index, reindex_files, _get_chroma_client, CHROMA_PERSIST_DIR

    data_dir = Path(__file__).parent.parent / "data"
    if not data_dir.exists():
        logger.warning("Директорія data/ не знайдена — індексацію пропущено.")
        return

    try:
        client = _get_chroma_client()
        # Перевіряємо чи колекції порожні (перший запуск)
        is_empty = _collections_empty(client)

        if is_empty:
            logger.info("ChromaDB порожній — виконуємо повне індексування...")
            count = rebuild_index(data_dir)
            # Запам'ятовуємо SHA256 всіх файлів
            all_files = list(data_dir.rglob("*.txt"))
            mark_files_indexed(all_files)
            logger.info("Індексація завершена: %d чанків.", count)
            return

        # Інкрементна перевірка
        changed = find_changed_files(data_dir)
        if not changed:
            logger.info("Індексація: всі файли без змін.")
            return

        logger.info("Індексація: %d змінених файлів → переіндексування...", len(changed))
        count = reindex_files(changed, data_dir=data_dir, client=client)
        mark_files_indexed(changed)
        names = [p.name for p in changed]
        logger.info(
            "Індексація: %d нових/оновлених документів (%s), %d чанків додано.",
            len(changed), ", ".join(names), count,
        )

    except Exception as exc:
        logger.error("Помилка при startup re-index: %s", exc, exc_info=True)


def _collections_empty(client) -> bool:
    """Повертає True якщо обидві колекції відсутні або порожні."""
    from ingestion.indexer import COLLECTION_GERMAN, COLLECTION_UKRAINIAN
    try:
        for name in [COLLECTION_GERMAN, COLLECTION_UKRAINIAN]:
            col = client.get_collection(name)
            if col.count() > 0:
                return False
        return True
    except Exception:
        return True


# ─── Daily background update ─────────────────────────────────────────────────

_DAILY_UPDATE_INTERVAL = int(os.getenv("DATA_UPDATE_INTERVAL_HOURS", "24")) * 3600


async def _daily_update_loop() -> None:
    """Фонова задача: раз на добу оновлює дані та переіндексує змінені файли."""
    while True:
        await asyncio.sleep(_DAILY_UPDATE_INTERVAL)
        logger.info("Фонове оновлення даних: запуск (інтервал %dh)...",
                    _DAILY_UPDATE_INTERVAL // 3600)
        loop = asyncio.get_event_loop()
        try:
            updated = await loop.run_in_executor(None, _run_data_update)
            if updated:
                logger.info("Фонове оновлення: дані оновлено, переіндексування...")
                await loop.run_in_executor(None, _smart_reindex)
            else:
                logger.info("Фонове оновлення: нових даних немає.")
        except Exception as exc:
            logger.error("Помилка фонового оновлення: %s", exc, exc_info=True)


def _run_data_update() -> bool:
    """Запускає data_collector --mode update. Повертає True якщо є оновлення."""
    try:
        from data_collector.sources import DOCUMENTS, SourceKind
        from data_collector.state_manager import StateManager
        from data_collector.downloader import fetch_kmein, fetch_eurlex, fetch_rada
        from data_collector.converter import md_to_text, html_to_text
        from data_collector.validator import validate
        from pathlib import Path as _Path

        data_dir = _Path(__file__).parent.parent / "data"
        state = StateManager()
        any_updated = False

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
            state.save()
            any_updated = True
            logger.info("Оновлено: %s", doc.output_path)

        return any_updated

    except Exception as exc:
        logger.error("_run_data_update помилка: %s", exc, exc_info=True)
        return False
