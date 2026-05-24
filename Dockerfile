# syntax=docker/dockerfile:1
FROM python:3.11-slim AS base

# curl потрібен для healthcheck; libgomp1 для sentence-transformers (OpenMP)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Створюємо непривілейованого користувача (без shell, без home directory в /root)
RUN groupadd --gid 1001 appgroup \
 && useradd --uid 1001 --gid appgroup --no-create-home --shell /sbin/nologin appuser

WORKDIR /app

# Шар залежностей
FROM base AS deps

COPY requirements.txt .

# Встановлюємо залежності ще від root (pip потребує запису у site-packages)
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# Образ API
FROM deps AS api

COPY . /app

# Директорія для даних (laws, SQLite) + кеш HuggingFace моделей
# appuser не має home-директорії  HF_HOME вказує на /app/.cache
RUN mkdir -p /app/data /app/.cache \
 && chown -R appuser:appgroup /app

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
# HuggingFace та sentence-transformers пишуть кеш моделей сюди
ENV HF_HOME=/app/.cache
ENV TRANSFORMERS_CACHE=/app/.cache

EXPOSE 8000

USER appuser

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]

# Образ Telegram-бота
FROM deps AS bot

COPY . /app

RUN mkdir -p /app/data /app/.cache \
 && chown -R appuser:appgroup /app

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV HF_HOME=/app/.cache
ENV TRANSFORMERS_CACHE=/app/.cache

USER appuser

CMD ["python", "bot/telegram_bot.py"]
