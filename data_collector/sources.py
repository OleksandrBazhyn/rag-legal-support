"""Каталог правових документів — описи джерел та правила збігу імен файлів."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional


class SourceKind(str, Enum):
    KMEIN = "kmein"      # github.com/kmein/gesetze
    EURLEX = "eurlex"    # eur-lex.europa.eu
    RADA = "rada"        # data.rada.gov.ua


@dataclass
class LegalDocument:
    """Описує один правовий документ та спосіб його отримання."""

    kind: SourceKind
    output_path: str          # відносний шлях від data/  (напр. "german_law/aufenthaltsgesetz.txt")
    description: str

    # ── kmein/gesetze ───────────────────────────────────────────────────────
    # Функція відповідності: отримує ім'я файлу з репо, повертає True якщо збіг
    law_matcher: Optional[Callable[[str], bool]] = field(default=None, repr=False)

    # ── EUR-Lex ──────────────────────────────────────────────────────────────
    celex_id: Optional[str] = None
    eurlex_url: Optional[str] = None

    # ── data.rada.gov.ua ─────────────────────────────────────────────────────
    rada_nreg: Optional[str] = None


def _prefix(abbrev: str) -> Callable[[str], bool]:
    """Збіг за префіксом: ім'я файлу починається з `{abbrev}-BJNR` або `{abbrev}.md`."""
    def matcher(name: str) -> bool:
        return name.startswith(f"{abbrev}-BJNR") or name.upper() == f"{abbrev.upper()}.MD"
    return matcher


def _regex(pattern: str) -> Callable[[str], bool]:
    """Збіг за регулярним виразом."""
    compiled = re.compile(pattern)
    def matcher(name: str) -> bool:
        return bool(compiled.match(name))
    return matcher


# ─── Каталог документів ───────────────────────────────────────────────────────

DOCUMENTS: list[LegalDocument] = [

    # ── kmein/gesetze: German laws ──────────────────────────────────────────

    LegalDocument(
        kind=SourceKind.KMEIN,
        output_path="german_law/aufenthaltsgesetz.txt",
        description="Aufenthaltsgesetz (AufenthG) — Закон про перебування іноземців",
        law_matcher=_prefix("AufenthG"),
    ),
    LegalDocument(
        kind=SourceKind.KMEIN,
        output_path="german_law/asylgesetz.txt",
        description="Asylgesetz (AsylG) — Закон про надання притулку",
        # AsylG- але НЕ AsylbLG-, AsylVfGNG- тощо
        law_matcher=_regex(r"AsylG-BJNR\d+"),
    ),
    LegalDocument(
        kind=SourceKind.KMEIN,
        output_path="german_law/asylbewerberleistungsgesetz.txt",
        description="Asylbewerberleistungsgesetz (AsylbLG) — Соціальні послуги для шукачів притулку",
        # Тільки основний закон, не §3-бекантмахунги
        law_matcher=_regex(r"AsylbLG-BJNR\d+"),
    ),
    LegalDocument(
        kind=SourceKind.KMEIN,
        output_path="german_law/beschaeftigungsverordnung.txt",
        description="Beschäftigungsverordnung (BeschV) — Постанова про зайнятість",
        law_matcher=_prefix("BeschV"),
    ),
    LegalDocument(
        kind=SourceKind.KMEIN,
        output_path="german_law/sgb_ii.txt",
        description="SGB II — Bürgergeld (Grundsicherung für Arbeitsuchende)",
        # SGB_2-BJNR... але НЕ SGB_10_Kap1_2 тощо
        law_matcher=_regex(r"SGB_2-BJNR\d+"),
    ),
    LegalDocument(
        kind=SourceKind.KMEIN,
        output_path="german_law/sgb_xii.txt",
        description="SGB XII — Sozialhilfe",
        law_matcher=_regex(r"SGB_12-BJNR\d+"),
    ),

    # ── EUR-Lex: EU Directives ────────────────────────────────────────────────

    LegalDocument(
        kind=SourceKind.EURLEX,
        output_path="german_law/eu_directive_temporary_protection.txt",
        description="Директива ЄС 2001/55/EG — тимчасовий захист переміщених осіб",
        celex_id="32001L0055",
        eurlex_url="https://eur-lex.europa.eu/legal-content/UK/TXT/HTML/?uri=CELEX:32001L0055",
    ),
    LegalDocument(
        kind=SourceKind.EURLEX,
        output_path="german_law/eu_directive_reception_conditions.txt",
        description="Директива ЄС 2013/33/EU — стандарти прийому шукачів притулку",
        celex_id="32013L0033",
        eurlex_url="https://eur-lex.europa.eu/legal-content/UK/TXT/HTML/?uri=CELEX:32013L0033",
    ),

    # ── data.rada.gov.ua: Ukrainian laws ────────────────────────────────────

    LegalDocument(
        kind=SourceKind.RADA,
        output_path="ukrainian_context/ua_law_refugees.txt",
        description="Закон України про біженців та осіб, які потребують захисту (3671-17)",
        rada_nreg="3671-17",
    ),
    LegalDocument(
        kind=SourceKind.RADA,
        output_path="ukrainian_context/ua_labor_code.txt",
        description="Кодекс законів про працю України — КЗпП (322-08)",
        rada_nreg="322-08",
    ),
    LegalDocument(
        kind=SourceKind.RADA,
        output_path="ukrainian_context/ua_employment_law.txt",
        description="Закон України про зайнятість населення (5067-17)",
        rada_nreg="5067-17",
    ),
    LegalDocument(
        kind=SourceKind.RADA,
        output_path="ukrainian_context/ua_social_services.txt",
        description="Закон України про соціальні послуги (2811-20)",
        rada_nreg="2811-20",
    ),
]

# Зручний індекс за output_path
DOCUMENTS_BY_PATH: dict[str, LegalDocument] = {d.output_path: d for d in DOCUMENTS}
