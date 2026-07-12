# rag-legal-support

> **Archived thesis.** This repository is the diploma project (legal RAG for Ukrainians in Germany). The product continuation lives in a separate repository (`ua-legal-advisor`). Tagged release: `v1.0-diploma`.

RAG-система правової підтримки для українців у Німеччині.

[![Python](https://img.shields.io/badge/Python-3.11-blue)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688)](https://fastapi.tiangolo.com)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED)](https://docker.com)

---

## Вимоги

- Docker Desktop ≥ 4.x + Docker Compose ≥ 2.x
- OpenAI API key
- Telegram Bot Token

---

## Налаштування

```bash
git clone https://github.com/OleksandrBazhyn/rag-legal-support.git
```

Створити `.env` у корені:

```env
OPENAI_API_KEY=sk-...
TELEGRAM_BOT_TOKEN=...
LLM_MODEL=gpt-4o-mini
EMBEDDING_MODEL=paraphrase-multilingual-mpnet-base-v2
CHROMA_HOST=localhost
CHROMA_PORT=8001
RATE_LIMIT_PER_IP_RPM=15
RATE_LIMIT_GLOBAL_RPM=200
ALLOWED_ORIGINS=http://localhost:8000,http://localhost:5500
```

---

## Запуск (Docker)

```bash
# Зібрати образи
docker compose build

# Запустити ChromaDB
docker compose up -d chromadb

# Проіндексувати документи (один раз)
docker compose --profile init up indexer

# Запустити API та бота
docker compose up -d api bot
```

Після першої ініціалізації:

```bash
docker compose up -d
```

---

## Запуск локально (без Docker)

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux/Mac

pip install -r requirements.txt
```

Запустити ChromaDB окремо:

```bash
chroma run --host localhost --port 8001 --path ./chroma_data
```

Проіндексувати:

```bash
python -m ingestion.indexer
```

Запустити API:

```bash
uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
```

Запустити бота (окремий термінал):

```bash
python bot/telegram_bot.py
```

---

## Адреси

| Сервіс | URL |
|--------|-----|
| Веб-інтерфейс | http://localhost:8000/web/ |
| Swagger UI | http://localhost:8000/docs |
| Health check | http://localhost:8000/health |

---

## Збір та індексація документів

```bash
# Перевірити статус документів
python data_collector/collect.py --mode check

# Завантажити оновлені
python data_collector/collect.py --mode update

# Примусове повне перезавантаження
python data_collector/collect.py --mode full

# Переіндексувати в ChromaDB
python -m ingestion.indexer
```

Перевірка кількості документів у колекціях:

```bash
python -c "
import chromadb
c = chromadb.HttpClient(host='localhost', port=8001)
print('german_law:', c.get_collection('german_law').count())
print('ukrainian_context:', c.get_collection('ukrainian_context').count())
"
```

---

## API

### POST /query

```json
{
  "question": "Які виплати отримує власник §24?",
  "profile": {
    "legal_status": "temporary_protection",
    "region": "Berlin",
    "query_category": "social_benefits",
    "language": "uk"
  },
  "chat_history": []
}
```

Відповідь:

```json
{
  "answer": "...",
  "sources": ["sgb_ii.txt", "aufenthaltsgesetz.txt"],
  "has_comparison": false
}
```

### POST /query/stream

Той самий формат запиту, повертає `text/event-stream`:

```
data: {"token": "Власники"}
data: {"token": " §24..."}
data: {"done": true, "sources": ["sgb_ii.txt"], "has_comparison": false}
```

### GET /health

```json
{"status": "ok", "chroma": "ok", "openai": "ok"}
```

---

## Тестування

```bash
pytest tests/ -v

# З покриттям
pytest tests/ --cov=. --cov-report=term-missing
```

---

## Оцінка якості (RAGAS)

```bash
python -m evaluation.ragas_eval

# За темою
python -m evaluation.ragas_eval --topic "Bürgergeld"
```

---

## Корисні команди Docker

```bash
docker compose ps
docker compose logs api --tail=30
docker compose logs -f api
docker compose exec api bash

# Rebuild після змін у коді
docker compose build api bot
docker compose up -d api bot

# Переіндексувати у Docker
docker compose stop api bot
docker compose --profile init up indexer
docker compose up -d api bot

# Зупинити все
docker compose down

# Зупинити і видалити volumes (скидає індекс ChromaDB)
docker compose down -v
```

---

## Стек

| Компонент | Версія |
|-----------|--------|
| Python | 3.11 |
| FastAPI | 0.115 |
| ChromaDB | 0.6.3 |
| sentence-transformers | 3.1.1 |
| OpenAI SDK | 2.36.0 |
| python-telegram-bot | 22.7 |
| Docker Compose | ≥ 2.x |
