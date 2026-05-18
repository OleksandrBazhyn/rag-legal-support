"""
RAG Quality Evaluator
=====================
Запускає кожне питання з golden_dataset.json через RAG API,
потім просить GPT-4o оцінити відповідь за чотирма метриками.

Метрики (1-5):
  • relevance      — чи відповідь стосується питання?
  • faithfulness   — чи факти підкріплені джерелами (без галюцинацій)?
  • completeness   — чи відповідь повна і вичерпна?
  • clarity        — чи відповідь зрозуміла й структурована?

Додатково:
  • keyword_hit    — частка expected_keywords, що з'явились у відповіді
  • source_hit     — чи хоча б одне expected_sources_contain є у sources
  • latency_ms     — час відповіді API

Виводить таблицю в консоль і зберігає:
  • evaluation/results.csv
  • evaluation/report.md

Використання:
  python -m evaluation.evaluator [--api http://localhost:8000] [--model gpt-4o-mini]
"""
from __future__ import annotations

import argparse
import csv
import json
import time
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any

import httpx
from openai import OpenAI

# ── Шляхи ────────────────────────────────────────────────────────────────────
ROOT    = Path(__file__).parent.parent
DATASET = Path(__file__).parent / "golden_dataset.json"
RESULTS = Path(__file__).parent / "results.csv"
REPORT  = Path(__file__).parent / "report.md"

# ── Промпт для LLM-судді ─────────────────────────────────────────────────────
JUDGE_SYSTEM = """\
Ти — суворий і об'єктивний оцінювач якості систем RAG (Retrieval-Augmented Generation).
Відповідай ТІЛЬКИ валідним JSON без жодних пояснень поза JSON.
"""

JUDGE_PROMPT = """\
Оціни відповідь RAG-системи за такими критеріями. Кожен критерій — від 1 до 5.

Питання: {question}

Відповідь системи:
{answer}

Отримані джерела: {sources}

Критерії оцінки (1 = погано, 5 = відмінно):
- relevance: чи відповідь відповідає суті питання?
- faithfulness: чи факти підкріплені джерелами (немає галюцинацій)?
- completeness: чи відповідь повна та охоплює ключові аспекти?
- clarity: чи відповідь чітка, структурована і зрозуміла?

Відповідай ТІЛЬКИ JSON без markdown-блоків:
{{"relevance": <1-5>, "faithfulness": <1-5>, "completeness": <1-5>, "clarity": <1-5>, "comment": "<1 речення"}}
"""


def query_rag(api_base: str, question: str, profile: dict) -> dict[str, Any]:
    """Відправляє запит до RAG API і повертає відповідь + latency."""
    url = f"{api_base.rstrip('/')}/query"
    payload = {
        "question": question,
        "profile": profile,
        "chat_history": [],
    }
    t0 = time.monotonic()
    resp = httpx.post(url, json=payload, timeout=60.0)
    latency_ms = round((time.monotonic() - t0) * 1000)
    resp.raise_for_status()
    data = resp.json()
    data["latency_ms"] = latency_ms
    return data


def judge_answer(
    client: OpenAI,
    model: str,
    question: str,
    answer: str,
    sources: list[str],
) -> dict[str, Any]:
    """Просить GPT оцінити якість відповіді."""
    prompt = JUDGE_PROMPT.format(
        question=question,
        answer=answer,
        sources=", ".join(sources) if sources else "немає",
    )
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": JUDGE_SYSTEM},
            {"role": "user",   "content": prompt},
        ],
        temperature=0.0,
        max_tokens=200,
    )
    raw = response.choices[0].message.content.strip()
    # Strip possible markdown fences
    raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(raw)


def keyword_hit_rate(answer: str, keywords: list[str]) -> float:
    """Частка ключових слів, що містяться у відповіді (case-insensitive)."""
    if not keywords:
        return 1.0
    answer_lower = answer.lower()
    hits = sum(1 for kw in keywords if kw.lower() in answer_lower)
    return round(hits / len(keywords), 2)


def source_hit(sources: list[str], expected_fragments: list[str]) -> bool:
    """Перевіряє, чи є хоча б одне очікуване джерело у списку sources."""
    if not expected_fragments:
        return True
    src_text = " ".join(sources).lower()
    return any(frag.lower() in src_text for frag in expected_fragments)


def print_table(rows: list[dict]) -> None:
    """Виводить результати у вигляді ASCII-таблиці."""
    cols = ["id", "topic", "relevance", "faithfulness", "completeness",
            "clarity", "kw_hit", "src_hit", "latency_ms"]
    widths = {c: max(len(c), max(len(str(r.get(c, ""))) for r in rows))
              for c in cols}

    sep  = "+-" + "-+-".join("-" * widths[c] for c in cols) + "-+"
    head = "| " + " | ".join(c.ljust(widths[c]) for c in cols) + " |"

    print(sep)
    print(head)
    print(sep)
    for r in rows:
        line = "| " + " | ".join(str(r.get(c, "")).ljust(widths[c]) for c in cols) + " |"
        print(line)
    print(sep)

    # Averages
    numeric = ["relevance", "faithfulness", "completeness", "clarity",
               "kw_hit", "latency_ms"]
    avgs = {c: round(mean(float(r[c]) for r in rows if r.get(c) is not None), 2)
            for c in numeric}
    avg_src = round(mean(1 if r.get("src_hit") else 0 for r in rows), 2)
    print(f"\nСереднє: relevance={avgs['relevance']}  faithfulness={avgs['faithfulness']}  "
          f"completeness={avgs['completeness']}  clarity={avgs['clarity']}  "
          f"kw_hit={avgs['kw_hit']}  src_hit={avg_src}  latency={avgs['latency_ms']}ms")


def save_csv(rows: list[dict]) -> None:
    """Зберігає результати у CSV."""
    if not rows:
        return
    fieldnames = list(rows[0].keys())
    with RESULTS.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nCSV збережено: {RESULTS}")


def save_report(rows: list[dict], api_base: str, judge_model: str) -> None:
    """Генерує Markdown-звіт."""
    numeric = ["relevance", "faithfulness", "completeness", "clarity", "kw_hit"]
    avgs = {c: round(mean(float(r[c]) for r in rows if r.get(c) is not None), 2)
            for c in numeric}
    avg_lat = round(mean(float(r["latency_ms"]) for r in rows), 0)
    avg_src = round(mean(1 if r.get("src_hit") else 0 for r in rows) * 100, 1)
    avg_kw  = round(avgs["kw_hit"] * 100, 1)

    lines = [
        f"# Звіт оцінки якості RAG-системи",
        f"",
        f"**Дата:** {datetime.now().strftime('%Y-%m-%d %H:%M')}  ",
        f"**API:** `{api_base}`  ",
        f"**LLM-суддя:** `{judge_model}`  ",
        f"**Кількість питань:** {len(rows)}",
        f"",
        f"## Зведені метрики",
        f"",
        f"| Метрика | Середнє (1–5) |",
        f"|---------|---------------|",
        f"| Relevance (релевантність) | **{avgs['relevance']}** |",
        f"| Faithfulness (достовірність) | **{avgs['faithfulness']}** |",
        f"| Completeness (повнота) | **{avgs['completeness']}** |",
        f"| Clarity (зрозумілість) | **{avgs['clarity']}** |",
        f"",
        f"| Додаткова метрика | Значення |",
        f"|-------------------|----------|",
        f"| Keyword Hit Rate | **{avg_kw}%** |",
        f"| Source Hit Rate | **{avg_src}%** |",
        f"| Середня затримка | **{avg_lat:.0f} мс** |",
        f"",
        f"## Результати по темах",
        f"",
    ]

    # Group by topic
    topics: dict[str, list[dict]] = {}
    for r in rows:
        topics.setdefault(r["topic"], []).append(r)

    for topic, topic_rows in sorted(topics.items()):
        t_avgs = {c: round(mean(float(r[c]) for r in topic_rows), 2) for c in numeric}
        lines += [
            f"### {topic}",
            f"",
            f"| ID | Relevance | Faithfulness | Completeness | Clarity | KW Hit | Src Hit | ms | Коментар |",
            f"|----|-----------|--------------|--------------|---------|--------|---------|-----|----------|",
        ]
        for r in topic_rows:
            src_icon = "✅" if r.get("src_hit") else "❌"
            lines.append(
                f"| {r['id']} | {r['relevance']} | {r['faithfulness']} | "
                f"{r['completeness']} | {r['clarity']} | {r['kw_hit']} | "
                f"{src_icon} | {r['latency_ms']} | {r.get('comment', '')} |"
            )
        avg_line = (f"| **avg** | **{t_avgs['relevance']}** | **{t_avgs['faithfulness']}** | "
                    f"**{t_avgs['completeness']}** | **{t_avgs['clarity']}** | "
                    f"**{round(t_avgs['kw_hit']*100,1)}%** | — | — | |")
        lines += [avg_line, ""]

    lines += [
        "## Детальні відповіді",
        "",
    ]
    for r in rows:
        lines += [
            f"### {r['id']} — {r['topic']}",
            f"**Питання:** {r['question']}",
            f"",
            f"**Відповідь RAG:**",
            f"> {r['answer'][:600].replace(chr(10), '  \\n> ')}{'…' if len(r['answer']) > 600 else ''}",
            f"",
            f"**Джерела:** {', '.join(r['sources']) if r['sources'] else '_немає_'}",
            f"",
            f"**Оцінки:** Relevance={r['relevance']} | Faithfulness={r['faithfulness']} | "
            f"Completeness={r['completeness']} | Clarity={r['clarity']}",
            f"",
            f"**Коментар судді:** {r.get('comment', '')}",
            f"",
            "---",
            "",
        ]

    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print(f"Звіт збережено: {REPORT}")


def main() -> None:
    parser = argparse.ArgumentParser(description="RAG Quality Evaluator")
    parser.add_argument("--api",   default="http://localhost:8000",
                        help="Base URL of the RAG API")
    parser.add_argument("--model", default="gpt-4o-mini",
                        help="OpenAI model used as judge")
    parser.add_argument("--skip-judge", action="store_true",
                        help="Skip LLM judging (only measure keyword/source/latency)")
    args = parser.parse_args()

    dataset = json.loads(DATASET.read_text(encoding="utf-8"))
    client  = OpenAI() if not args.skip_judge else None

    rows: list[dict] = []
    total = len(dataset)

    print(f"\nRAG Quality Evaluation  |  {total} питань  |  суддя: {args.model}\n")
    print(f"API: {args.api}\n")

    for i, item in enumerate(dataset, 1):
        qid = item["id"]
        print(f"[{i:2}/{total}] {qid} — {item['question'][:60]}…", end="", flush=True)

        # 1. Query RAG
        try:
            rag_result = query_rag(args.api, item["question"], item["profile"])
        except Exception as exc:
            print(f"  ERROR: {exc}")
            rows.append({
                "id": qid, "topic": item["topic"], "question": item["question"],
                "relevance": None, "faithfulness": None,
                "completeness": None, "clarity": None,
                "kw_hit": None, "src_hit": None,
                "latency_ms": None, "answer": f"ERROR: {exc}", "sources": [],
                "comment": str(exc),
            })
            continue

        answer  = rag_result.get("answer", "")
        sources = rag_result.get("sources", [])
        latency = rag_result.get("latency_ms", 0)

        # 2. Keyword / source hit
        kw_hit  = keyword_hit_rate(answer, item.get("expected_keywords", []))
        src_hit_val = source_hit(sources, item.get("expected_sources_contain", []))

        # 3. LLM judge
        if client:
            try:
                scores = judge_answer(client, args.model, item["question"], answer, sources)
            except Exception as exc:
                print(f"  JUDGE ERROR: {exc}")
                scores = {"relevance": None, "faithfulness": None,
                          "completeness": None, "clarity": None, "comment": str(exc)}
        else:
            scores = {"relevance": "—", "faithfulness": "—",
                      "completeness": "—", "clarity": "—", "comment": "skip"}

        row = {
            "id":           qid,
            "topic":        item["topic"],
            "question":     item["question"],
            "relevance":    scores.get("relevance"),
            "faithfulness": scores.get("faithfulness"),
            "completeness": scores.get("completeness"),
            "clarity":      scores.get("clarity"),
            "kw_hit":       kw_hit,
            "src_hit":      src_hit_val,
            "latency_ms":   latency,
            "answer":       answer,
            "sources":      sources,
            "comment":      scores.get("comment", ""),
        }
        rows.append(row)

        rel = scores.get("relevance", "—")
        fai = scores.get("faithfulness", "—")
        print(f"  rel={rel} faith={fai} kw={kw_hit} src={'✓' if src_hit_val else '✗'} {latency}ms")

    print()
    print_table(rows)
    save_csv(rows)
    save_report(rows, args.api, args.model)


if __name__ == "__main__":
    main()
