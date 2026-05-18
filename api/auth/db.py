"""SQLite-сховище веб-користувачів."""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import date
from pathlib import Path

DB_PATH = Path(__file__).parent.parent.parent / "data" / "web_users.db"

DAILY_QUERY_LIMIT: int = int(os.getenv("DAILY_QUERY_LIMIT", "50"))


def _init() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS web_users (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                email          TEXT    UNIQUE NOT NULL,
                username       TEXT    NOT NULL,
                password_hash  TEXT    NOT NULL,
                created_at     TEXT    DEFAULT (datetime('now')),
                legal_status   TEXT    DEFAULT 'unknown',
                region         TEXT    DEFAULT 'unknown',
                query_category TEXT    DEFAULT 'other',
                language       TEXT    DEFAULT 'uk',
                daily_query_count INTEGER DEFAULT 0,
                daily_query_date  TEXT    DEFAULT ''
            )
        """)
        # Міграція: додаємо колонки, якщо таблиця вже існує без них
        for col_def in [
            "daily_query_count INTEGER DEFAULT 0",
            "daily_query_date  TEXT    DEFAULT ''",
            "bonus_queries     INTEGER DEFAULT 0",
        ]:
            try:
                conn.execute(f"ALTER TABLE web_users ADD COLUMN {col_def}")
            except sqlite3.OperationalError:
                pass  # вже існує
        conn.commit()


_init()


@contextmanager
def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def get_by_email(email: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM web_users WHERE email = ?", (email.lower(),)
        ).fetchone()
        return dict(row) if row else None


def get_by_id(user_id: int) -> dict | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM web_users WHERE id = ?", (user_id,)
        ).fetchone()
        return dict(row) if row else None


def create_user(email: str, username: str, password_hash: str) -> dict:
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO web_users (email, username, password_hash) VALUES (?, ?, ?)",
            (email.lower(), username.strip(), password_hash),
        )
        user_id = cur.lastrowid
    # З'єднання закрите і дані закомічені — тепер можна читати
    return get_by_id(user_id)  # type: ignore[arg-type]


def update_profile(
    user_id: int,
    legal_status: str,
    region: str,
    query_category: str,
    language: str,
) -> None:
    with _conn() as conn:
        conn.execute(
            """UPDATE web_users
               SET legal_status=?, region=?, query_category=?, language=?
               WHERE id=?""",
            (legal_status, region, query_category, language, user_id),
        )


def get_usage(user_id: int) -> dict:
    """Повертає поточний стан добового ліміту для користувача."""
    today = date.today().isoformat()
    with _conn() as conn:
        row = conn.execute(
            "SELECT daily_query_count, daily_query_date, bonus_queries FROM web_users WHERE id = ?",
            (user_id,),
        ).fetchone()
    if not row:
        return {"count": 0, "limit": DAILY_QUERY_LIMIT, "bonus": 0, "remaining": DAILY_QUERY_LIMIT}
    count = row["daily_query_count"] or 0
    bonus = row["bonus_queries"] or 0
    if (row["daily_query_date"] or "") != today:
        count = 0
    effective_limit = DAILY_QUERY_LIMIT + bonus
    remaining = max(0, effective_limit - count)
    return {"count": count, "limit": DAILY_QUERY_LIMIT, "bonus": bonus, "remaining": remaining}


def check_and_increment(user_id: int) -> tuple[bool, int, int]:
    """Перевіряє ліміт і збільшує лічильник.

    Ефективний ліміт = DAILY_QUERY_LIMIT + bonus_queries.
    Returns:
        (allowed, new_count, effective_limit)
        allowed=False якщо ліміт вичерпано.
    """
    today = date.today().isoformat()
    with _conn() as conn:
        row = conn.execute(
            "SELECT daily_query_count, daily_query_date, bonus_queries FROM web_users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if not row:
            # Рядок не знайдено — нестандартна ситуація, дозволяємо запит
            return True, 1, DAILY_QUERY_LIMIT

        count = row["daily_query_count"] or 0
        bonus = row["bonus_queries"] or 0
        if (row["daily_query_date"] or "") != today:
            count = 0  # скидаємо на новий день

        effective_limit = DAILY_QUERY_LIMIT + bonus
        if count >= effective_limit:
            return False, count, effective_limit

        new_count = count + 1
        conn.execute(
            "UPDATE web_users SET daily_query_count = ?, daily_query_date = ? WHERE id = ?",
            (new_count, today, user_id),
        )
    return True, new_count, effective_limit


def add_bonus_queries(user_id: int, amount: int) -> dict:
    """Додає бонусні запити (демо-покупка). Повертає оновлений usage."""
    with _conn() as conn:
        conn.execute(
            "UPDATE web_users SET bonus_queries = COALESCE(bonus_queries, 0) + ? WHERE id = ?",
            (amount, user_id),
        )
    return get_usage(user_id)
