"""Відстеження SHA256 проіндексованих файлів для інкрементного re-index."""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_CHROMA_DIR = Path.home() / ".chroma_rag_legal"
INDEX_STATE_FILE = Path(
    __import__("os").getenv("CHROMA_PERSIST_DIR", str(_DEFAULT_CHROMA_DIR))
) / ".index_state.json"


def compute_sha256(path: Path) -> str:
    """Обчислює SHA256 файлу блоками по 64 KB."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_index_state() -> dict[str, Any]:
    """Читає стан індексу з диску. Повертає порожній стан якщо файл відсутній."""
    if not INDEX_STATE_FILE.exists():
        return {"last_indexed": None, "indexed_files": {}}
    try:
        return json.loads(INDEX_STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Не вдалося прочитати index_state: %s — починаємо з чистого стану.", exc)
        return {"last_indexed": None, "indexed_files": {}}


def save_index_state(state: dict[str, Any]) -> None:
    """Зберігає стан індексу на диск."""
    INDEX_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    INDEX_STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def find_changed_files(data_dir: Path) -> list[Path]:
    """Повертає список файлів з data_dir, SHA256 яких змінився відносно збереженого стану.

    Returns:
        Список Path-об'єктів до файлів що потрібно переіндексувати.
    """
    state = load_index_state()
    stored: dict[str, str] = state.get("indexed_files", {})

    changed: list[Path] = []
    for path in sorted(data_dir.rglob("*.txt")):
        key = path.name
        current_sha = compute_sha256(path)
        if stored.get(key) != current_sha:
            changed.append(path)
            logger.debug("Змінено: %s (stored=%s, current=%s)", key, stored.get(key, "—"), current_sha[:8])

    return changed


def mark_files_indexed(paths: list[Path]) -> None:
    """Записує SHA256 щойно проіндексованих файлів до стану."""
    state = load_index_state()
    indexed: dict[str, str] = state.setdefault("indexed_files", {})
    for path in paths:
        indexed[path.name] = compute_sha256(path)
    state["last_indexed"] = datetime.now().isoformat(timespec="seconds")
    save_index_state(state)
