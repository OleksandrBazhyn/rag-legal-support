"""SQLite-сховище профілів та документів користувачів.

Таблиці:
  user_profiles  — правовий статус, регіон, мова (персистентно між перезапусками)
  user_documents — витягнуті дані з Bescheid-ів: термін, сума, номер справи
"""
from __future__ import annotations

import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import date
from pathlib import Path

DAILY_QUERY_LIMIT: int = int(os.getenv("DAILY_QUERY_LIMIT", "50"))

logger = logging.getLogger(__name__)

# База зберігається поруч зі скриптом бота, у підпапці data/
DB_PATH = Path(__file__).parent.parent / "data" / "users.db"


def init_db() -> None:
    """Створює таблиці якщо їх ще немає (викликається один раз при старті)."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS user_profiles (
                user_id             INTEGER PRIMARY KEY,
                legal_status        TEXT    NOT NULL DEFAULT 'unknown',
                region              TEXT    NOT NULL DEFAULT 'unknown',
                language            TEXT    NOT NULL DEFAULT 'uk',
                first_name          TEXT,
                updated_at          TEXT    NOT NULL DEFAULT (datetime('now')),
                daily_query_count   INTEGER NOT NULL DEFAULT 0,
                daily_query_date    TEXT    NOT NULL DEFAULT '',
                bonus_queries       INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS user_documents (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id          INTEGER NOT NULL,
                doc_type         TEXT    NOT NULL,
                valid_until      TEXT,
                monthly_amount   REAL,
                case_number      TEXT,
                issuing_office   TEXT,
                raw_description  TEXT,
                updated_at       TEXT    NOT NULL DEFAULT (datetime('now')),
                UNIQUE(user_id, doc_type)
            );

            CREATE TABLE IF NOT EXISTS reminders_sent (
                user_id   INTEGER NOT NULL,
                doc_type  TEXT    NOT NULL,
                threshold INTEGER NOT NULL,
                sent_at   TEXT    NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (user_id, doc_type, threshold)
            );
        """)
    # Міграція: додаємо нові колонки якщо таблиця вже існує
    with _conn() as conn:
        for col_def in [
            "daily_query_count INTEGER NOT NULL DEFAULT 0",
            "daily_query_date  TEXT    NOT NULL DEFAULT ''",
            "bonus_queries     INTEGER NOT NULL DEFAULT 0",
        ]:
            try:
                conn.execute(f"ALTER TABLE user_profiles ADD COLUMN {col_def}")
            except sqlite3.OperationalError:
                pass  # вже існує

    logger.info("БД ініціалізовано: %s", DB_PATH)


@contextmanager
def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# ─── Профіль ─────────────────────────────────────────────────────────────────

def save_profile(
    user_id: int,
    profile: dict,
    first_name: str | None = None,
) -> None:
    """Зберігає або оновлює профіль користувача."""
    with _conn() as conn:
        conn.execute(
            """
            INSERT INTO user_profiles
                (user_id, legal_status, region, language, first_name, updated_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(user_id) DO UPDATE SET
                legal_status = excluded.legal_status,
                region       = excluded.region,
                language     = excluded.language,
                first_name   = COALESCE(excluded.first_name, first_name),
                updated_at   = datetime('now')
            """,
            (
                user_id,
                profile.get("legal_status", "unknown"),
                profile.get("region", "unknown"),
                profile.get("language", "uk"),
                first_name,
            ),
        )


def load_profile(user_id: int) -> dict | None:
    """Завантажує профіль з БД або повертає None якщо запис відсутній."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM user_profiles WHERE user_id = ?", (user_id,)
        ).fetchone()
    if row is None:
        return None
    return {
        "legal_status": row["legal_status"],
        "region": row["region"],
        "query_category": "other",
        "language": row["language"],
    }


# ─── Документи (Bescheid тощо) ────────────────────────────────────────────────

def save_document(
    user_id: int,
    doc_type: str,
    valid_until: str | None = None,
    monthly_amount: float | None = None,
    case_number: str | None = None,
    issuing_office: str | None = None,
    raw_description: str | None = None,
) -> None:
    """Зберігає або оновлює дані документа (UPSERT по user_id + doc_type)."""
    with _conn() as conn:
        conn.execute(
            """
            INSERT INTO user_documents
                (user_id, doc_type, valid_until, monthly_amount,
                 case_number, issuing_office, raw_description, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(user_id, doc_type) DO UPDATE SET
                valid_until     = COALESCE(excluded.valid_until,     valid_until),
                monthly_amount  = COALESCE(excluded.monthly_amount,  monthly_amount),
                case_number     = COALESCE(excluded.case_number,     case_number),
                issuing_office  = COALESCE(excluded.issuing_office,  issuing_office),
                raw_description = COALESCE(excluded.raw_description, raw_description),
                updated_at      = datetime('now')
            """,
            (
                user_id, doc_type, valid_until, monthly_amount,
                case_number, issuing_office, raw_description,
            ),
        )


def get_documents(user_id: int) -> list[dict]:
    """Повертає всі збережені документи для користувача."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM user_documents WHERE user_id = ? ORDER BY updated_at DESC",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_expiring_soon(days: int = 30) -> list[dict]:
    """Повертає документи, що спливають протягом N днів.

    Використовується для проактивних нагадувань.
    """
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT d.*, p.language, p.first_name
            FROM user_documents d
            JOIN user_profiles p ON d.user_id = p.user_id
            WHERE d.valid_until IS NOT NULL
              AND date(d.valid_until) BETWEEN date('now') AND date('now', ? || ' days')
            ORDER BY d.valid_until
            """,
            (days,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_docs_needing_reminders(threshold_days: int) -> list[dict]:
    """Документи, що потребують нагадування для порогу threshold_days.

    Повертає лише ті, для яких це нагадування ще НЕ надсилалось.
    days_left — кількість днів до закінчення (ціле).
    """
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT
                d.*,
                p.language,
                p.first_name,
                CAST(julianday(d.valid_until) - julianday('now') AS INTEGER) AS days_left
            FROM user_documents d
            JOIN user_profiles p ON d.user_id = p.user_id
            WHERE d.valid_until IS NOT NULL
              AND date(d.valid_until) >= date('now')
              AND CAST(julianday(d.valid_until) - julianday('now') AS INTEGER) <= ?
              AND NOT EXISTS (
                  SELECT 1 FROM reminders_sent rs
                  WHERE rs.user_id  = d.user_id
                    AND rs.doc_type = d.doc_type
                    AND rs.threshold = ?
              )
            ORDER BY d.valid_until
            """,
            (threshold_days, threshold_days),
        ).fetchall()
    return [dict(r) for r in rows]


# ─── Денний ліміт запитів ────────────────────────────────────────────────────

def get_usage(user_id: int) -> dict:
    """Повертає поточний стан ліміту: count, limit, bonus, remaining."""
    today = date.today().isoformat()
    with _conn() as conn:
        row = conn.execute(
            "SELECT daily_query_count, daily_query_date, bonus_queries FROM user_profiles WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    if not row:
        return {"count": 0, "limit": DAILY_QUERY_LIMIT, "bonus": 0, "remaining": DAILY_QUERY_LIMIT}
    count = row["daily_query_count"] or 0
    bonus = row["bonus_queries"] or 0
    if (row["daily_query_date"] or "") != today:
        count = 0
    effective = DAILY_QUERY_LIMIT + bonus
    return {"count": count, "limit": DAILY_QUERY_LIMIT, "bonus": bonus, "remaining": max(0, effective - count)}


def check_and_increment(user_id: int) -> tuple[bool, int, int]:
    """Перевіряє ліміт і збільшує лічильник.

    Returns: (allowed, new_count, effective_limit).
    """
    today = date.today().isoformat()
    with _conn() as conn:
        # Гарантуємо існування рядка — нові користувачі не блокуються
        conn.execute(
            "INSERT OR IGNORE INTO user_profiles (user_id) VALUES (?)",
            (user_id,),
        )

        row = conn.execute(
            "SELECT daily_query_count, daily_query_date, bonus_queries FROM user_profiles WHERE user_id = ?",
            (user_id,),
        ).fetchone()

        count = row["daily_query_count"] or 0
        bonus = row["bonus_queries"] or 0
        if (row["daily_query_date"] or "") != today:
            count = 0  # новий день — скидаємо

        effective = DAILY_QUERY_LIMIT + bonus
        if count >= effective:
            return False, count, effective

        new_count = count + 1
        conn.execute(
            "UPDATE user_profiles SET daily_query_count = ?, daily_query_date = ? WHERE user_id = ?",
            (new_count, today, user_id),
        )
    return True, new_count, effective


def mark_reminder_sent(user_id: int, doc_type: str, threshold: int) -> None:
    """Позначає нагадування як відправлене (не надсилатиметься повторно)."""
    with _conn() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO reminders_sent (user_id, doc_type, threshold)
            VALUES (?, ?, ?)
            """,
            (user_id, doc_type, threshold),
        )
