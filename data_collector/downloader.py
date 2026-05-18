"""Завантаження правових документів з трьох джерел із кешуванням та rate limiting."""
from __future__ import annotations

import logging
import os
import random
import time
from pathlib import Path
from typing import Optional
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from data_collector.state_manager import StateManager

logger = logging.getLogger(__name__)

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_API_BASE = "https://api.github.com"
RAW_GITHUB_BASE = "https://raw.githubusercontent.com/kmein/gesetze/master/laws"
RADA_API_BASE = "https://data.rada.gov.ua"

_MAX_RETRIES = 3
_BACKOFF = [10, 20, 40]          # секунди між спробами
_RADA_DELAY = (5.0, 7.0)         # пауза між Rada-запитами (random)
_MAX_FILE_SIZE = 500 * 1024      # 500 KB
_TIMEOUT = 30


def _github_headers() -> dict[str, str]:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "rag-legal-support"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"token {GITHUB_TOKEN}"
    return headers


def _http_get(url: str, headers: dict[str, str] | None = None) -> tuple[bytes, dict[str, str]]:
    """Виконує HTTP GET із базовою логікою retry. Повертає (body, response_headers)."""
    req = Request(url, headers=headers or {})
    last_exc: Exception = RuntimeError("No attempts made")
    for attempt, backoff in enumerate([0] + _BACKOFF, start=0):
        if backoff:
            logger.debug("Retry %d/%d, очікування %ds…", attempt, _MAX_RETRIES, backoff)
            time.sleep(backoff)
        try:
            with urlopen(req, timeout=_TIMEOUT) as resp:
                body = resp.read(_MAX_FILE_SIZE + 1)
                resp_headers = {k.lower(): v for k, v in resp.headers.items()}
                return body, resp_headers
        except HTTPError as exc:
            if exc.code in (304, 401, 403, 404):
                raise  # не повторювати: 304=not modified, 4xx=auth/not found
            last_exc = exc
            logger.warning("HTTP %d для %s", exc.code, url)
        except URLError as exc:
            last_exc = exc
            logger.warning("Мережева помилка для %s: %s", url, exc)
    raise last_exc


# ─── kmein/gesetze ───────────────────────────────────────────────────────────

_kmein_tree_cache: list[dict] | None = None


def _get_kmein_tree() -> list[dict]:
    """Отримує повне дерево файлів репозиторію kmein/gesetze (без ліміту розміру).

    Відповідь Git Trees API ~1.6 MB — не застосовуємо _MAX_FILE_SIZE.
    Кешуємо в пам'яті щоб не завантажувати повторно в рамках одного запуску.
    """
    global _kmein_tree_cache
    if _kmein_tree_cache is not None:
        return _kmein_tree_cache

    import json as _json
    url = f"{GITHUB_API_BASE}/repos/kmein/gesetze/git/trees/HEAD?recursive=1"
    req = Request(url, headers=_github_headers())
    last_exc: Exception = RuntimeError("No attempts made")
    for attempt, backoff in enumerate([0] + _BACKOFF, start=0):
        if backoff:
            time.sleep(backoff)
        try:
            with urlopen(req, timeout=60) as resp:
                body = resp.read()          # без обмеження розміру
            data = _json.loads(body)
            _kmein_tree_cache = [
                item for item in data.get("tree", [])
                if item.get("path", "").startswith("laws/")
                and item.get("type") == "blob"
            ]
            logger.info("kmein tree: %d файлів завантажено.", len(_kmein_tree_cache))
            return _kmein_tree_cache
        except Exception as exc:
            last_exc = exc
            logger.warning("Помилка tree API (спроба %d): %s", attempt + 1, exc)
    raise last_exc


def fetch_kmein(
    output_path: str,
    law_matcher,
    state: StateManager,
    force: bool = False,
) -> tuple[Optional[bytes], str]:
    """Завантажує файл з kmein/gesetze (з кешуванням за GitHub SHA).

    Returns:
        (content_bytes, status) де status: 'downloaded' | 'cached' | 'not_found' | 'error'
    """
    tree = _get_kmein_tree()

    # Знаходимо файл за matcher
    match = None
    for item in tree:
        name = item["path"].split("/")[-1]
        if law_matcher(name):
            match = item
            break

    if match is None:
        logger.warning("Файл не знайдено у kmein/gesetze для: %s", output_path)
        return None, "not_found"

    remote_sha = match.get("sha", "")
    stored_sha = state.get_github_sha(output_path)

    if not force and stored_sha == remote_sha:
        logger.info("⏩ Cached (SHA збігається): %s", output_path)
        return None, "cached"

    # Завантажуємо через raw URL
    filename = match["path"].split("/")[-1]
    url = f"{RAW_GITHUB_BASE}/{filename}"
    try:
        body, _ = _http_get(url, {"User-Agent": "rag-legal-support"})
    except Exception as exc:
        logger.error("Помилка завантаження %s: %s", url, exc)
        return None, "error"

    state.set_doc(output_path, source="kmein", github_sha=remote_sha,
                  file_size_bytes=len(body), is_valid=True)
    logger.info("✅ Завантажено з kmein: %s (%d bytes)", filename, len(body))
    return body, "downloaded"


# ─── EUR-Lex ─────────────────────────────────────────────────────────────────

def _is_waf_response(raw: bytes) -> bool:
    """Перевіряє, чи відповідь є AWS WAF-сторінкою (bot challenge)."""
    sniff = raw[:512].decode("utf-8", "replace").lower()
    return "awswafcookiedomainlist" in sniff or "gokuprops" in sniff


def fetch_eurlex(
    output_path: str,
    url: str,
    state: StateManager,
    force: bool = False,
) -> tuple[Optional[bytes], str]:
    """Завантажує HTML-документ з EUR-Lex (кешування через ETag/Last-Modified).

    EUR-Lex може бути захищений AWS WAF. У цьому разі повертає 'waf_blocked'.

    Returns:
        (html_bytes, status): status може бути 'downloaded'|'cached'|'waf_blocked'|'error'
    """
    headers: dict[str, str] = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "uk,en-US;q=0.9,en;q=0.8,de;q=0.7",
        "Accept-Encoding": "identity",
        "Connection": "keep-alive",
    }

    if not force:
        etag = state.get_eurlex_etag(output_path)
        last_mod = state.get_eurlex_last_modified(output_path)
        if etag:
            headers["If-None-Match"] = etag
        elif last_mod:
            headers["If-Modified-Since"] = last_mod

    try:
        body, resp_headers = _http_get(url, headers)
    except HTTPError as exc:
        if exc.code == 304:
            logger.info("⏩ EUR-Lex Not Modified: %s", output_path)
            return None, "cached"
        logger.error("EUR-Lex HTTP %d для %s", exc.code, url)
        return None, "error"
    except Exception as exc:
        logger.error("EUR-Lex помилка %s: %s", url, exc)
        return None, "error"

    # Перевірка на WAF-блокування
    if _is_waf_response(body):
        logger.warning(
            "EUR-Lex заблокований AWS WAF для %s. "
            "Буде збережено існуючий mock-документ (якщо є).", output_path
        )
        return None, "waf_blocked"

    new_etag = resp_headers.get("etag", "")
    new_last_mod = resp_headers.get("last-modified", "")

    state.set_doc(
        output_path,
        source="eurlex",
        etag=new_etag,
        last_modified_header=new_last_mod,
        file_size_bytes=len(body),
        is_valid=True,
    )
    logger.info("✅ Завантажено з EUR-Lex: %s (%d bytes)", output_path, len(body))
    return body, "downloaded"


# ─── data.rada.gov.ua ────────────────────────────────────────────────────────
#
# За документацією API:
#   - TXT формат: User-Agent: OpenData  (токен НЕ потрібен)
#   - JSON формат: User-Agent: <uuid-токен> (потребує реєстрації IP)
#   - ЗАБОРОНЕНО: звертатись до /api/token перед кожним запитом
#   Джерело: https://data.rada.gov.ua → розділ API


def fetch_rada(
    output_path: str,
    nreg: str,
    state: StateManager,
    force: bool = False,
) -> tuple[Optional[bytes], str]:
    """Завантажує текст документа з data.rada.gov.ua у форматі TXT.

    Використовує User-Agent: OpenData (без токена) — офіційний спосіб
    доступу до TXT-формату згідно з документацією Ради.
    Між запитами — обов'язкова пауза 5-7 секунд.

    Returns:
        (text_bytes, status): status = 'downloaded' | 'cached' | 'not_found' | 'error'
    """
    url = f"{RADA_API_BASE}/laws/show/{nreg}.txt"
    headers: dict[str, str] = {
        "User-Agent": "OpenData",
        "Accept": "text/plain, */*",
    }

    if not force:
        last_mod = state.get_rada_last_modified(output_path)
        if last_mod:
            headers["If-Modified-Since"] = last_mod

    # Обов'язкова пауза між запитами (до 60 запитів/хвилину)
    delay = random.uniform(*_RADA_DELAY)
    logger.debug("Рада: пауза %.1fs перед запитом %s", delay, nreg)
    time.sleep(delay)

    for attempt in range(_MAX_RETRIES):
        try:
            body, resp_headers = _http_get(url, headers)
        except HTTPError as exc:
            if exc.code == 304:
                logger.info("⏩ Рада Not Modified: %s (%s)", output_path, nreg)
                return None, "cached"
            if exc.code == 404:
                logger.warning("Рада: nreg %s не знайдено (404)", nreg)
                return None, "not_found"
            logger.error("Рада: HTTP %d для %s", exc.code, url)
            return None, "error"
        except Exception as exc:
            logger.error("Рада: мережева помилка для %s: %s", nreg, exc)
            return None, "error"

        # Якщо відповідь — HTML-сторінка (редирект або помилка) — повторити
        sniff = body[:200].decode("utf-8", "replace").lower()
        is_html = "<html" in sniff or "<!doctype" in sniff or "redirect" in sniff
        if is_html and attempt < _MAX_RETRIES - 1:
            extra = _BACKOFF[attempt]
            logger.warning(
                "Рада: HTML-відповідь %d байт для %s (спроба %d). Пауза %ds…",
                len(body), nreg, attempt + 1, extra,
            )
            time.sleep(extra)
            continue
        if is_html:
            logger.error("Рада: сервер повертає HTML після %d спроб для %s", _MAX_RETRIES, nreg)
            return None, "error"

        break  # відповідь нормальна (plain text)
    else:
        logger.error("Рада: усі %d спроб вичерпано для %s", _MAX_RETRIES, nreg)
        return None, "error"

    # Обмеження розміру: перші 500 KB
    if len(body) > _MAX_FILE_SIZE:
        logger.warning("Рада: %s перевищує 500 KB, зберігаємо перші 500 KB.", nreg)
        body = body[:_MAX_FILE_SIZE]

    new_last_mod = resp_headers.get("last-modified", "")
    state.set_doc(
        output_path,
        source="rada",
        last_modified_header=new_last_mod,
        file_size_bytes=len(body),
        is_valid=True,
    )
    logger.info("✅ Завантажено з Ради: %s (%s, %d bytes)", nreg, output_path, len(body))
    return body, "downloaded"
