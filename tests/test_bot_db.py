"""Тести SQLite-сховища профілів та документів (bot/db.py)."""
from __future__ import annotations

import sqlite3
import tempfile
from datetime import date, timedelta
from pathlib import Path

import pytest

# Імпортуємо db БЕЗ patch-контексту, потім вручну перенаправляємо DB_PATH.
# patch() як контекстний менеджер відновлює значення після виходу — нам це не треба.
import bot.db as db

_TMP_DB_PATH: Path = Path(tempfile.mktemp(suffix=".db"))
db.DB_PATH = _TMP_DB_PATH          # перенаправляємо перед init_db
db.init_db()                        # створюємо таблиці в тимчасовому файлі


def _wipe_test_rows() -> None:
    """Видаляє тестові рядки (user_id >= 10000) з усіх таблиць."""
    # Завжди використовуємо db.DB_PATH — той самий шлях, що й функції модуля
    conn = sqlite3.connect(db.DB_PATH)
    conn.execute("DELETE FROM reminders_sent WHERE user_id >= 10000")
    conn.execute("DELETE FROM user_documents  WHERE user_id >= 10000")
    conn.execute("DELETE FROM user_profiles   WHERE user_id >= 10000")
    conn.commit()
    conn.close()


@pytest.fixture(autouse=True)
def _clean_test_data():
    """Очищає тестові дані до і після кожного тесту."""
    _wipe_test_rows()
    yield
    _wipe_test_rows()


class TestSaveLoadProfile:
    def test_load_nonexistent_returns_none(self):
        """load_profile для невідомого user_id повертає None."""
        result = db.load_profile(99990)
        assert result is None

    def test_save_and_load_roundtrip(self):
        """Збережений профіль завантажується коректно."""
        profile = {
            "legal_status": "temporary_protection",
            "region": "Berlin",
            "language": "uk",
        }
        db.save_profile(10001, profile, first_name="Олена")
        loaded = db.load_profile(10001)

        assert loaded is not None
        assert loaded["legal_status"] == "temporary_protection"
        assert loaded["region"] == "Berlin"
        assert loaded["language"] == "uk"
        assert loaded["query_category"] == "other"  # дефолт

    def test_save_updates_existing(self):
        """Повторне save_profile оновлює запис (UPSERT)."""
        db.save_profile(10002, {"legal_status": "unknown", "region": "unknown", "language": "uk"})
        db.save_profile(10002, {"legal_status": "residence_permit", "region": "NRW", "language": "de"})

        loaded = db.load_profile(10002)
        assert loaded["legal_status"] == "residence_permit"
        assert loaded["region"] == "NRW"
        assert loaded["language"] == "de"

    def test_save_all_statuses(self):
        """Всі можливі legal_status зберігаються і завантажуються."""
        statuses = ["temporary_protection", "residence_permit", "asylum_seeker", "unknown"]
        for uid, status in enumerate(statuses, start=10010):
            db.save_profile(uid, {"legal_status": status, "region": "Bayern", "language": "uk"})
            loaded = db.load_profile(uid)
            assert loaded["legal_status"] == status

    def test_save_without_first_name(self):
        """save_profile працює без параметра first_name."""
        db.save_profile(10020, {"legal_status": "unknown", "region": "unknown", "language": "uk"})
        loaded = db.load_profile(10020)
        assert loaded is not None

class TestDocuments:
    def _profile(self, uid: int) -> None:
        db.save_profile(uid, {"legal_status": "temporary_protection", "region": "Berlin", "language": "uk"})

    def test_get_documents_empty(self):
        """get_documents для нового user_id повертає порожній список."""
        self._profile(10030)
        docs = db.get_documents(10030)
        assert docs == []

    def test_save_and_get_document(self):
        """Збережений документ з'являється в get_documents."""
        self._profile(10031)
        db.save_document(
            10031,
            doc_type="Bürgergeld Bescheid",
            valid_until="2026-12-31",
            monthly_amount=563.0,
            case_number="BG-001",
            issuing_office="Jobcenter Berlin Mitte",
        )
        docs = db.get_documents(10031)

        assert len(docs) == 1
        doc = docs[0]
        assert doc["doc_type"] == "Bürgergeld Bescheid"
        assert doc["valid_until"] == "2026-12-31"
        assert doc["monthly_amount"] == 563.0
        assert doc["case_number"] == "BG-001"
        assert doc["issuing_office"] == "Jobcenter Berlin Mitte"

    def test_save_document_upsert(self):
        """Повторне save_document для того ж doc_type оновлює запис."""
        self._profile(10032)
        db.save_document(10032, "Aufenthaltstitel", valid_until="2026-06-01", monthly_amount=None)
        db.save_document(10032, "Aufenthaltstitel", valid_until="2027-06-01", monthly_amount=500.0)

        docs = db.get_documents(10032)
        assert len(docs) == 1
        assert docs[0]["valid_until"] == "2027-06-01"
        assert docs[0]["monthly_amount"] == 500.0

    def test_multiple_document_types(self):
        """Різні doc_type зберігаються як окремі записи."""
        self._profile(10033)
        db.save_document(10033, "Bürgergeld Bescheid", valid_until="2026-12-31")
        db.save_document(10033, "Aufenthaltstitel §24", valid_until="2026-09-30")

        docs = db.get_documents(10033)
        assert len(docs) == 2
        types = {d["doc_type"] for d in docs}
        assert "Bürgergeld Bescheid" in types
        assert "Aufenthaltstitel §24" in types

class TestReminders:
    def _setup_user(self, uid: int, days_until_expiry: int) -> str:
        """Створює профіль та документ з терміном через N днів."""
        db.save_profile(uid, {"legal_status": "temporary_protection", "region": "Berlin", "language": "uk"})
        expiry = (date.today() + timedelta(days=days_until_expiry)).isoformat()
        db.save_document(uid, "TestDoc", valid_until=expiry)
        return expiry

    def test_needs_reminder_within_threshold(self):
        """Документ що спливає через 25 днів потрапляє у поріг 30 днів."""
        self._setup_user(10040, 25)
        docs = db.get_docs_needing_reminders(30)
        found = [d for d in docs if d["user_id"] == 10040]
        assert len(found) == 1
        assert found[0]["days_left"] <= 30

    def test_no_reminder_outside_threshold(self):
        """Документ що спливає через 45 днів не потрапляє у поріг 30 днів."""
        self._setup_user(10041, 45)
        docs = db.get_docs_needing_reminders(30)
        found = [d for d in docs if d["user_id"] == 10041]
        assert len(found) == 0

    def test_mark_sent_prevents_duplicate(self):
        """Після mark_reminder_sent документ більше не повертається."""
        self._setup_user(10042, 10)
        before = [d for d in db.get_docs_needing_reminders(30) if d["user_id"] == 10042]
        assert len(before) == 1

        db.mark_reminder_sent(10042, "TestDoc", 30)

        after = [d for d in db.get_docs_needing_reminders(30) if d["user_id"] == 10042]
        assert len(after) == 0

    def test_different_thresholds_independent(self):
        """Порог 60 і поріг 7 — незалежні записи в reminders_sent."""
        self._setup_user(10043, 5)

        db.mark_reminder_sent(10043, "TestDoc", 7)

        # Поріг 7 тепер зайнятий, але 60 — вільний
        docs_7  = [d for d in db.get_docs_needing_reminders(7)  if d["user_id"] == 10043]
        docs_60 = [d for d in db.get_docs_needing_reminders(60) if d["user_id"] == 10043]

        assert len(docs_7) == 0   # вже відправлено
        assert len(docs_60) == 1  # ще не відправлено

    def test_expired_doc_not_returned(self):
        """Прострочений документ не повертається в get_docs_needing_reminders."""
        uid = 10044
        db.save_profile(uid, {"legal_status": "unknown", "region": "unknown", "language": "uk"})
        db.save_document(uid, "ExpiredDoc", valid_until="2020-01-01")

        docs = db.get_docs_needing_reminders(30)
        found = [d for d in docs if d["user_id"] == uid]
        assert len(found) == 0

    def test_no_profile_no_reminder(self):
        """Документ без відповідного профілю не повертається (JOIN)."""
        conn = sqlite3.connect(db.DB_PATH)
        expiry = (date.today() + timedelta(days=10)).isoformat()
        conn.execute(
            "INSERT INTO user_documents (user_id, doc_type, valid_until) VALUES (?, ?, ?)",
            (10045, "OrphanDoc", expiry),
        )
        conn.commit()
        conn.close()

        docs = db.get_docs_needing_reminders(30)
        found = [d for d in docs if d["user_id"] == 10045]
        assert len(found) == 0  # немає JOIN з профілем

    def test_get_expiring_soon_basic(self):
        """get_expiring_soon повертає документи в межах N днів."""
        self._setup_user(10050, 15)
        docs = db.get_expiring_soon(days=30)
        found = [d for d in docs if d["user_id"] == 10050]
        assert len(found) == 1

    def test_get_expiring_soon_excludes_far(self):
        """get_expiring_soon не повертає документи за межами N днів."""
        self._setup_user(10051, 90)
        docs = db.get_expiring_soon(days=30)
        found = [d for d in docs if d["user_id"] == 10051]
        assert len(found) == 0
