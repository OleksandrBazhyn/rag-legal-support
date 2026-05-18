"""
Baseline Evaluator (без RAG)
============================
Задає ті ж питання безпосередньо до GPT-4o-mini без будь-якого контексту з БД.
Результат зберігається у baseline_results.csv і baseline_report.md.
Порівняйте з evaluation/report.md, щоб показати вплив RAG.

Використання:
  python -m evaluation.baseline [--model gpt-4o-mini]
"""
from __future__ import annotations

import argparse
import csv
import json
import time
from datetime import datetime
from pathlib import Path
from statistics import mean

from openai import OpenAI

ROOT    = Path(__file__).parent.parent
DATASET = Path(__file__).parent / "golden_dataset.json"
RESULTS = Path(__file__).parent / "baseline_results.csv"
REPORT  = Path(__file__).parent / "baseline_report.md"

SYSTEM_PROMPT = """\
Ти — юридичний помічник для українців у Німеччині.
Відповідай точно, структуровано та на тій мові, яку вказав користувач.
"""

JUDGE_SYSTEM = """\
Ти — суворий і об'єктивний оцінювач якості відповідей юридичного асистента.
Відповідай ТІЛЬКИ валідним JSON без жодних пояснень поза JSON.
"""

JUDGE_PROMPT = """\
Оціни відповідь асистента за критеріями (1-5).

Питання: {question}
Відповідь: {answer}

{{"relevance": <1-5>, "faithfulness": <1-5>, "completeness": <1-5>, "clarity": <1-5>, "comment": "<1 речення"}}
"""


def ask_gpt(client: OpenAI, model: str, question: str, profile: dict) -> tuple[str, int]:
    lang_map = {"uk": "українська", "de": "Deutsch", "en": "English"}
    lang = lang_map.get(profile.get("language", "uk"), "українська")
    user_msg = (
        f"Мій правовий статус: {profile.get('legal_status', 'unknown')}. "
        f"Регіон: {profile.get('region', 'unknown')}. "
        f"Мова відповіді: {lang}.\n\n"
        f"Питання: {question}"
    )
    t0 = time.monotonic()
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system",  "content": SYSTEM_PROMPT},
            {"role": "user",    "content": user_msg},
        ],
        temperature=0.2,
        max_tokens=800,
    )
    latency_ms = round((time.monotonic() - t0) * 1000)
    return resp.choices[0].message.content.strip(), latency_ms


def judge(client: OpenAI, model: str, question: str, answer: str) -> dict:
    prompt = JUDGE_PROMPT.format(question=question, answer=answer)
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": JUDGE_SYSTEM},
            {"role": "user",   "content": prompt},
        ],
        temperature=0.0,
        max_tokens=150,
    )
    raw = resp.choices[0].message.content.strip()
    raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(raw)


def keyword_hit_rate(answer: str, keywords: list[str]) -> float:
    if not keywords:
        return 1.0
    lo = answer.lower()
    return round(sum(1 for kw in keywords if kw.lower() in lo) / len(keywords), 2)


def save_csv(rows: list[dict]) -> None:
    if not rows:
        return
    with RESULTS.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"CSV збережено: {RESULTS}")


def save_report(rows: list[dict], model: str) -> None:
    numeric = ["relevance", "faithfulness", "completeness", "clarity", "kw_hit"]
    avgs = {c: round(mean(float(r[c]) for r in rows if r.get(c) is not None), 2)
            for c in numeric}
    avg_lat = round(mean(float(r["latency_ms"]) for r in rows), 0)

    lines = [
        "# Baseline Evaluation (без RAG)",
        "",
        f"**Дата:** {datetime.now().strftime('%Y-%m-%d %H:%M')}  ",
        f"**Модель:** `{model}` (без контексту з документів)  ",
        f"**Кількість питань:** {len(rows)}",
        "",
        "## Зведені метрики",
        "",
        "| Метрика | Середнє (1–5) |",
        "|---------|---------------|",
        f"| Relevance | **{avgs['relevance']}** |",
        f"| Faithfulness | **{avgs['faithfulness']}** |",
        f"| Completeness | **{avgs['completeness']}** |",
        f"| Clarity | **{avgs['clarity']}** |",
        "",
        "| Метрика | Значення |",
        "|---------|----------|",
        f"| Keyword Hit Rate | **{round(avgs['kw_hit']*100,1)}%** |",
        f"| Середня затримка | **{avg_lat:.0f} мс** |",
        "",
        "## По питаннях",
        "",
        "| ID | Topic | Rel | Faith | Comp | Clar | KW% | ms |",
        "|----|-------|-----|-------|------|------|-----|----|",
    ]
    for r in rows:
        lines.append(
            f"| {r['id']} | {r['topic']} | {r['relevance']} | {r['faithfulness']} | "
            f"{r['completeness']} | {r['clarity']} | {round(float(r['kw_hit'])*100)}% | {r['latency_ms']} |"
        )
    lines += [
        "",
        "> **Порівняй з** `evaluation/report.md` щоб побачити покращення завдяки RAG.",
    ]

    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print(f"Звіт збережено: {REPORT}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Baseline Evaluator (no RAG)")
    parser.add_argument("--model", default="gpt-4o-mini")
    args = parser.parse_args()

    dataset = json.loads(DATASET.read_text(encoding="utf-8"))
    client  = OpenAI()

    rows: list[dict] = []
    total = len(dataset)
    print(f"\nBaseline Evaluation (без RAG)  |  {total} питань  |  {args.model}\n")

    for i, item in enumerate(dataset, 1):
        qid = item["id"]
        print(f"[{i:2}/{total}] {qid}…", end="", flush=True)

        try:
            answer, latency = ask_gpt(client, args.model, item["question"], item["profile"])
            kw = keyword_hit_rate(answer, item.get("expected_keywords", []))
            scores = judge(client, args.model, item["question"], answer)
        except Exception as exc:
            print(f"  ERROR: {exc}")
            continue

        row = {
            "id":           qid,
            "topic":        item["topic"],
            "question":     item["question"],
            "relevance":    scores.get("relevance"),
            "faithfulness": scores.get("faithfulness"),
            "completeness": scores.get("completeness"),
            "clarity":      scores.get("clarity"),
            "kw_hit":       kw,
            "latency_ms":   latency,
            "answer":       answer,
            "comment":      scores.get("comment", ""),
        }
        rows.append(row)
        print(f"  rel={row['relevance']} faith={row['faithfulness']} kw={kw} {latency}ms")

    print()
    save_csv(rows)
    save_report(rows, args.model)


if __name__ == "__main__":
    main()
