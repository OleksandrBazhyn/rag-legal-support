# syntax=docker/dockerfile:1
FROM python:3.11-slim AS base

WORKDIR /app

# Системні залежності для pdfplumber та sentence-transformers
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# ── Шар залежностей ───────────────────────────────────────────────────────────
FROM base AS deps

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# ── Образ API ─────────────────────────────────────────────────────────────────
FROM deps AS api

COPY . /app

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]

# ── Образ Telegram-бота ───────────────────────────────────────────────────────
FROM deps AS bot

COPY . /app

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

CMD ["python", "bot/telegram_bot.py"]
