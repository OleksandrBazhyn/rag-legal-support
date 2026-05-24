"""Захист від prompt injection та санітизація вводу.

Модуль реалізує:
- Виявлення спроб prompt injection (jailbreak, витік системного промпту)
- Обмеження довжини та структури вводу
- Санітизацію тексту для логів (обрізання PII)
"""
from __future__ import annotations

import re
import unicodedata

# Перевірені реальні вектори атак на LLM-системи
_INJECTION_PATTERNS: list[re.Pattern] = [re.compile(p, re.IGNORECASE | re.DOTALL) for p in [
    # Перевизначення ролі / інструкцій
    r"ignore\s+(all\s+)?(previous|prior|above|your)\s+instructions?",
    r"disregard\s+(\w+\s+){0,3}instructions?",   # "disregard your previous instructions"
    r"forget\s+(all\s+)?(previous|prior|your)\s+instructions?",
    r"you\s+are\s+now\s+(a|an|the)\s+\w",
    r"act\s+as\s+(if\s+)?(you\s+are|a|an)\s+",
    r"pretend\s+(that\s+)?you('re|\s+are)\s+",
    r"roleplay\s+as\s+",
    r"simulate\s+(a|an|the)\s+\w+\s+(who|that|which)",
    r"from\s+now\s+on\s+you\s+(are|will|must|should)",

    # Витік системного промпту
    r"(reveal|show|print|display|output|repeat|tell\s+me)\s+(\w+\s+){0,3}(your\s+)?(\w+\s+)?(system\s+prompt|instructions?|rules?|constraints?|guidelines?)",
    r"what\s+(are|were)\s+your\s+(original\s+)?(instructions?|rules?|prompt|guidelines?)",
    r"ignore\s+(safety|ethical)\s+(guidelines?|rules?|constraints?)",

    # Джейлбрейк
    r"\bDAN\b",                                    # "Do Anything Now"
    r"developer\s+mode",
    r"jailbreak",
    r"god\s*mode",
    r"no\s+restrictions?",
    r"without\s+(any\s+)?(restrictions?|limitations?|filters?|rules?|guidelines?)",

    # Ін'єкція через кодування / приховані символи
    r"base64\s*:\s*[A-Za-z0-9+/=]{20,}",          # base64 payload
    r"<\|im_start\|>",                             # ChatML injection
    r"\[INST\]",                                   # Llama injection
    r"###\s*System\s*:",                           # markdown system block

    # Намагання отримати вихідні дані системи
    r"output\s+(the\s+)?(first|last|all)\s+\d+\s+(tokens?|words?|characters?|bytes?)",
    r"repeat\s+(everything|all)\s+(above|before|you\s+said)",
]]

_SUSPICIOUS_UNICODE_CATEGORIES = {
    "Cf",   # Format characters (zero-width, soft hyphen…)
    "Co",   # Private Use
    "Cs",   # Surrogate
}

_MAX_SUSPICIOUS_UNICODE = 3  # більше трьох підозрілих символів - відхиляємо


def _count_suspicious_unicode(text: str) -> int:
    return sum(
        1 for ch in text
        if unicodedata.category(ch) in _SUSPICIOUS_UNICODE_CATEGORIES
    )

class InjectionDetected(ValueError):
    """Виняток при виявленні спроби ін'єкції."""


def validate_question(text: str, max_length: int = 2000) -> str:
    """Перевіряє та нормалізує питання.

    Raises:
        InjectionDetected: при виявленні спроби ін'єкції.
        ValueError: при порушенні обмежень довжини/формату.

    Returns:
        Нормалізований текст (unicode normalize, strip, colapse whitespace).
    """
    if not text or not text.strip():
        raise ValueError("Питання не може бути порожнім.")

    # Нормалізуємо Unicode (NFC) і прибираємо зайві пробіли
    text = unicodedata.normalize("NFC", text).strip()
    text = re.sub(r"[ \t]+", " ", text)   # colapse whitespace (але не \n)

    if len(text) > max_length:
        raise ValueError(
            f"Питання завдовжки {len(text)} символів перевищує ліміт {max_length}."
        )

    # Перевіряємо підозрілі Unicode-символи
    n_suspicious = _count_suspicious_unicode(text)
    if n_suspicious > _MAX_SUSPICIOUS_UNICODE:
        raise InjectionDetected(
            f"Виявлено {n_suspicious} прихованих символів у тексті."
        )

    # Перевіряємо injection патерни
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(text):
            raise InjectionDetected(
                "Запит містить недозволені інструкції. "
                "Система призначена виключно для правових питань."
            )

    return text


def sanitize_for_log(text: str, max_chars: int = 120) -> str:
    """Обрізає текст для логування — не зберігає повний зміст запиту.

    Не логуємо більше max_chars символів питання, щоб уникнути
    потрапляння PII у лог-файли.
    """
    text = text.replace("\n", " ").replace("\r", "")
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"… [{len(text)} симв.]"


def mask_ip(ip: str) -> str:
    """Маскує останній октет IPv4 або останні 4 групи IPv6 для логів."""
    # IPv4: 192.168.1.42 в 192.168.1.***
    if re.match(r"^\d+\.\d+\.\d+\.\d+$", ip):
        parts = ip.rsplit(".", 1)
        return parts[0] + ".***"
    # IPv6: залишаємо тільки перші 2 групи
    if ":" in ip:
        groups = ip.split(":")
        return ":".join(groups[:2]) + ":****"
    return "***"
