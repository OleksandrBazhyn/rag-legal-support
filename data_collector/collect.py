"""CLI-точка входу для збору правових документів.

Використання:
  python data_collector/collect.py --mode check
  python data_collector/collect.py --mode update
  python data_collector/collect.py --mode full
  python data_collector/collect.py --source kmein
  python data_collector/collect.py --source eurlex
  python data_collector/collect.py --source rada
"""
from __future__ import annotations

import argparse
import io
import logging
import os
import sys
from pathlib import Path

# Примусово UTF-8 для stdout/stderr на Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Додаємо корінь проєкту до sys.path для локального запуску
_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv
load_dotenv(_ROOT / ".env")

from data_collector.sources import DOCUMENTS, SourceKind, LegalDocument
from data_collector.state_manager import StateManager
from data_collector.downloader import fetch_kmein, fetch_eurlex, fetch_rada
from data_collector.converter import md_to_text, html_to_text
from data_collector.validator import validate

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

DATA_DIR = _ROOT / "data"

# Лічильники результатів
_STATS: dict[str, int] = {
    "downloaded": 0,
    "cached": 0,
    "not_found": 0,
    "error": 0,
    "invalid": 0,
}


# ─── Обробка одного документа ─────────────────────────────────────────────────

def _process_document(doc: LegalDocument, state: StateManager, force: bool) -> str:
    """Завантажує, конвертує, валідує та зберігає один документ.

    Returns:
        статус: 'downloaded' | 'cached' | 'not_found' | 'error' | 'invalid'
    """
    output_file = DATA_DIR / doc.output_path
    output_file.parent.mkdir(parents=True, exist_ok=True)

    # ── Завантаження ─────────────────────────────────────────────────────────
    if doc.kind == SourceKind.KMEIN:
        raw_bytes, status = fetch_kmein(doc.output_path, doc.law_matcher, state, force)
        convert_fn = md_to_text

    elif doc.kind == SourceKind.EURLEX:
        raw_bytes, status = fetch_eurlex(doc.output_path, doc.eurlex_url, state, force)
        convert_fn = html_to_text

    elif doc.kind == SourceKind.RADA:
        raw_bytes, status = fetch_rada(doc.output_path, doc.rada_nreg, state, force)
        # Рада вже повертає plain text (.txt endpoint)
        convert_fn = lambda b: b.decode("utf-8", errors="replace") if isinstance(b, bytes) else b

    else:
        return "error"

    if status == "waf_blocked":
        # EUR-Lex WAF: залишаємо існуючий файл (mock або попереднє завантаження)
        existing = DATA_DIR / doc.output_path
        if existing.exists():
            logger.info("EUR-Lex WAF: залишаємо існуючий файл %s", doc.output_path)
            return "cached"
        return "waf_blocked"

    if status != "downloaded":
        return status

    # ── Конвертація ───────────────────────────────────────────────────────────
    try:
        text = convert_fn(raw_bytes)
    except Exception as exc:
        logger.error("Помилка конвертації %s: %s", doc.output_path, exc)
        return "error"

    # ── Валідація ─────────────────────────────────────────────────────────────
    result = validate(text, source_kind=doc.kind.value)
    if not result:
        logger.warning("Валідація провалена для %s: %s", doc.output_path, result.reason)
        state.set_doc(doc.output_path, is_valid=False, validation_error=result.reason)
        state.save()
        return "invalid"

    # ── Збереження ────────────────────────────────────────────────────────────
    output_file.write_text(text, encoding="utf-8")
    state.set_doc(doc.output_path, is_valid=True, file_size_bytes=len(text.encode()))
    state.save()

    logger.info("💾 Збережено: %s (%.1f KB)", doc.output_path, len(text) / 1024)
    return "downloaded"


# ─── Режим check ──────────────────────────────────────────────────────────────

def _cmd_check(state: StateManager, source_filter: str | None) -> None:
    """Виводить таблицю статусу всіх документів."""
    docs = _filter_docs(source_filter)

    header = f"{'Документ':<45} {'Джерело':<8} {'Розмір':<10} {'Оновлено':<18} {'Статус'}"
    print("\n" + header)
    print("─" * len(header))

    for doc in docs:
        filename = Path(doc.output_path).name
        file = DATA_DIR / doc.output_path
        doc_state = state.get_doc(doc.output_path)

        exists = file.exists()
        is_valid = doc_state.get("is_valid", False)
        size_bytes = doc_state.get("file_size_bytes", 0)
        last_dl = doc_state.get("last_downloaded", "")[:16].replace("T", " ")

        if exists and is_valid:
            size_str = f"{size_bytes / 1024:.1f} KB" if size_bytes else f"{file.stat().st_size / 1024:.1f} KB"
            status = "✓ актуально"
        elif exists and not is_valid:
            size_str = f"{file.stat().st_size / 1024:.1f} KB"
            status = "⚠ невалідний"
        else:
            size_str = "—"
            last_dl = "—"
            status = "✗ відсутній"

        print(
            f"{filename:<45} {doc.kind.value:<8} {size_str:<10} {last_dl:<18} {status}"
        )

    print()


# ─── Режим update / full ──────────────────────────────────────────────────────

def _cmd_collect(
    state: StateManager,
    source_filter: str | None,
    force: bool,
) -> None:
    docs = _filter_docs(source_filter)
    mode_label = "ПОВНЕ ЗАВАНТАЖЕННЯ" if force else "ОНОВЛЕННЯ"
    print(f"\n[{mode_label}] {len(docs)} документів\n")

    for doc in docs:
        label = Path(doc.output_path).name
        print(f"  → {label} ({doc.kind.value})… ", end="", flush=True)
        try:
            status = _process_document(doc, state, force=force)
        except Exception as exc:
            logger.exception("Неочікувана помилка для %s", doc.output_path)
            status = "error"

        _STATS[status] = _STATS.get(status, 0) + 1

        icons = {
            "downloaded": "OK завантажено",
            "cached":     "-- без змін",
            "not_found":  "!! не знайдено",
            "error":      "!! помилка",
            "invalid":    "~~ невалідний",
            "waf_blocked":"~~ WAF (залишено mock)",
        }
        print(icons.get(status, status))

    if force:
        state.set_last_full_collection()
        state.save()

    _print_summary()


def _print_summary() -> None:
    print("\n" + "═" * 50)
    print("ПІДСУМОК:")
    print(f"  ✅ Завантажено:    {_STATS.get('downloaded', 0)}")
    print(f"  ⏩ Без змін:       {_STATS.get('cached', 0)}")
    print(f"  ❌ Не знайдено:   {_STATS.get('not_found', 0)}")
    print(f"  ❌ Помилки:       {_STATS.get('error', 0)}")
    print(f"  ⚠️  Невалідних:    {_STATS.get('invalid', 0)}")
    print("═" * 50 + "\n")


def _filter_docs(source_filter: str | None) -> list[LegalDocument]:
    if not source_filter:
        return DOCUMENTS
    kind_map = {"kmein": SourceKind.KMEIN, "eurlex": SourceKind.EURLEX, "rada": SourceKind.RADA}
    kind = kind_map.get(source_filter.lower())
    if kind is None:
        print(f"Невідоме джерело: {source_filter}. Доступні: kmein, eurlex, rada")
        sys.exit(1)
    return [d for d in DOCUMENTS if d.kind == kind]


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Збір реальних правових документів для RAG-системи"
    )
    parser.add_argument(
        "--mode",
        choices=["check", "update", "full"],
        default="check",
        help="check — показати статус | update — завантажити змінені | full — примусове повне завантаження",
    )
    parser.add_argument(
        "--source",
        choices=["kmein", "eurlex", "rada"],
        default=None,
        help="Фільтр за джерелом (якщо не вказано — всі джерела)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Розширене логування",
    )
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    state = StateManager()

    if args.mode == "check":
        _cmd_check(state, args.source)
    elif args.mode == "update":
        _cmd_collect(state, args.source, force=False)
    elif args.mode == "full":
        _cmd_collect(state, args.source, force=True)


if __name__ == "__main__":
    main()
