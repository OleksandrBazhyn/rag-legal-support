"""Тести для модуля data_collector/."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ─── test_state_manager_read_write ────────────────────────────────────────────

def test_state_manager_read_write(tmp_path):
    """StateManager коректно зберігає та читає стан документів."""
    from data_collector.state_manager import StateManager

    state_file = tmp_path / "state.json"
    sm = StateManager(state_file=state_file)

    # Запис стану документа
    sm.set_doc(
        "german_law/aufenthaltsgesetz.txt",
        source="kmein",
        github_sha="abc123",
        file_size_bytes=512000,
        is_valid=True,
    )
    sm.save()

    # Перевірка, що файл створено
    assert state_file.exists()
    raw = json.loads(state_file.read_text(encoding="utf-8"))
    assert "documents" in raw
    assert "german_law/aufenthaltsgesetz.txt" in raw["documents"]

    # Читання через новий екземпляр (simulates restart)
    sm2 = StateManager(state_file=state_file)
    doc = sm2.get_doc("german_law/aufenthaltsgesetz.txt")
    assert doc["source"] == "kmein"
    assert doc["github_sha"] == "abc123"
    assert doc["file_size_bytes"] == 512000
    assert doc["is_valid"] is True


# ─── test_converter_md_to_txt ─────────────────────────────────────────────────

def test_converter_md_to_txt():
    """md_to_text прибирає Markdown-розмітку та зберігає §-параграфи."""
    from data_collector.converter import md_to_text

    # Використовуємо encode щоб передати Unicode через bytes
    md_str = (
        "---\n"
        "Title: AufenthG\n"
        "---\n\n"
        "# Aufenthaltsgesetz\n\n"
        "## § 1 Zweck des Gesetzes\n\n"
        "**Das** Aufenthaltsgesetz regelt die Einreise.\n\n"
        "- Punkt 1\n"
        "- Punkt 2\n\n"
        "[Link Text](https://example.com)\n\n"
        "`code snippet`\n"
    )
    md_bytes = md_str.encode("utf-8")
    result = md_to_text(md_bytes)

    # Markdown-розмітка прибрана
    assert "#" not in result
    assert "**" not in result
    assert "[Link Text]" not in result
    assert "`code snippet`" not in result
    assert "https://example.com" not in result

    # Вміст збережено
    assert "Aufenthaltsgesetz" in result
    assert "Einreise" in result
    assert "§ 1" in result  # §-параграфи збережені
    assert "Punkt 1" in result
    assert "Punkt 2" in result


# ─── test_validator_rejects_empty ─────────────────────────────────────────────

def test_validator_rejects_empty():
    """Порожній рядок не проходить валідацію."""
    from data_collector.validator import validate

    result = validate("", source_kind="kmein")
    assert not result.is_valid
    assert result.reason is not None
    assert len(result.reason) > 0


def test_validator_rejects_too_short():
    """Текст коротший за мінімальний розмір не проходить валідацію."""
    from data_collector.validator import validate

    # 50 байт — менше мінімального порогу 500 байт
    tiny_text = "Kleiner Text." * 3
    result = validate(tiny_text, source_kind="kmein")
    assert not result.is_valid


# ─── test_validator_rejects_html_error ────────────────────────────────────────

def test_validator_rejects_html_error():
    """Текст що містить HTML-розмітку відхиляється як помилкова сторінка."""
    from data_collector.validator import validate

    # Симулюємо HTML-відповідь AWS WAF або 404-сторінку
    html_error = """<html>
<head><title>404 Not Found</title></head>
<body>
<h1>Not Found</h1>
<p>The requested URL was not found on this server.
Please check the URL and try again.
This error has been logged.
Contact the administrator if you believe this is a mistake.
</p>
</body>
</html>""" * 5  # повторюємо щоб подолати мінімальний розмір і кількість слів

    result = validate(html_error, source_kind="eurlex")
    assert not result.is_valid
    assert "HTML" in result.reason or "html" in result.reason.lower()


# ─── test_downloader_skips_unchanged ─────────────────────────────────────────

def test_downloader_skips_unchanged(tmp_path):
    """Якщо SHA файлу не змінився, fetch_kmein повертає статус 'cached'."""
    from data_collector.downloader import fetch_kmein
    from data_collector.state_manager import StateManager

    state_file = tmp_path / "state.json"
    sm = StateManager(state_file=state_file)

    output_path = "german_law/aufenthaltsgesetz.txt"
    stored_sha = "deadbeef1234567890abcdef"

    # Записуємо SHA до стейту (імітуємо попереднє завантаження)
    sm.set_doc(output_path, source="kmein", github_sha=stored_sha, is_valid=True)
    sm.save()

    # Мок: kmein-дерево повертає файл з тим самим SHA
    mock_tree = [
        {"path": f"laws/AufenthG-BJNR195410004.md", "type": "blob", "sha": stored_sha}
    ]

    with patch("data_collector.downloader._get_kmein_tree", return_value=mock_tree):
        content, status = fetch_kmein(
            output_path=output_path,
            law_matcher=lambda name: name.startswith("AufenthG-BJNR"),
            state=sm,
            force=False,
        )

    assert status == "cached"
    assert content is None  # Файл не перезавантажувався
