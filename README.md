# Система правової підтримки для українців у Німеччині

> **Кваліфікаційна робота бакалавра**  
> КНУ імені Тараса Шевченка · Спеціальність 122 «Комп'ютерні науки» · 2026  
> RAG-система персоналізованої правової підтримки з порівняльним контекстом Україна–Німеччина

[![Python](https://img.shields.io/badge/Python-3.11-blue)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688)](https://fastapi.tiangolo.com)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED)](https://docker.com)
[![Tests](https://img.shields.io/badge/tests-162%20passed-success)](#тестування)

---

## Зміст

- [Про проект](#про-проект)
- [Ключові особливості](#ключові-особливості)
- [Архітектура](#архітектура)
- [Швидкий старт](#швидкий-старт)
- [Структура проекту](#структура-проекту)
- [API](#api)
- [Оцінка якості](#оцінка-якості)
- [Безпека](#безпека)
- [Тестування](#тестування)

---

## Про проект

Система надає персоналізовані відповіді на правові запити українських громадян в Німеччині. На відміну від загальних чат-ботів (ChatGPT, Gemini):

| Характеристика | Загальний LLM | **Ця система** |
|----------------|---------------|----------------|
| Джерела | Загальні знання (можуть бути застарілі) | Актуальні тексти законів ФРН |
| Персоналізація | Відсутня | §24 vs Asylbewerber vs Niederlassungserlaubnis |
| Збереження даних | Немає між сесіями | SQLite: профіль, Bescheid, терміни документів |
| Нагадування | Немає | APScheduler: 60/30/7 днів до закінчення |
| Порівняльний контекст | Немає | Система права України vs ФРН |
| Безпека | Без захисту | Rate limit, injection guard, security headers |

---

## Ключові особливості

### 🔍 RAG-пайплайн
- Семантичний пошук у **ChromaDB** (векторна БД)
- Моделі ембедингів: `paraphrase-multilingual-mpnet-base-v2`
- Два корпуси: `german_law` (SGB II/XII, AufenthG, AsylG…) + `ukrainian_context`
- Streaming відповіді (SSE) — текст з'являється токен за токеном

### 🤖 Telegram-бот
- **Bescheid-сканер**: фото документа → GPT Vision → структуровані дані (тип, термін, сума)
- **Нагадування**: автоматичні повідомлення за 60/30/7 днів до закінчення терміну
- **Inline-кнопки**: 📚 Джерела · 📋 Чеклист · 🔄 Уточнити
- **Генератор чеклистів**: 7 юридичних процедур з персоналізованим списком документів

### 🌐 Веб-інтерфейс
- Chat-bubble UI з streaming відповідями
- Темна/світла тема, i18n (🇺🇦/🇩🇪/🇬🇧)
- Профіль у localStorage, checklist-модаль
- Адаптивний дизайн (мобільний sidebar)

### 🛡 Безпека
- **Rate limiting**: 15 req/хв per-IP · 200 req/хв глобально
- **Injection guard**: 15+ regex-патернів (DAN, jailbreak, prompt leak…)
- **Security headers**: CSP, X-Frame-Options, Referrer-Policy…
- **CORS**: обмежений список origins
- **Ліміти файлів**: PDF max 5 МБ, 20 сторінок; фото max 8 МБ

---

## Архітектура

```
┌─────────────────────────────────────────────────────────────────┐
│                        Клієнти                                  │
│    Веб-браузер (index.html)    Telegram Bot                     │
└────────────┬───────────────────────────┬────────────────────────┘
             │ HTTP/SSE                  │ HTTP (httpx)
             ▼                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                   FastAPI (api/)                                 │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────────────┐ │
│  │ Rate Limit  │  │ Injection    │  │  Security Headers      │ │
│  │ Middleware  │  │ Guard        │  │  Middleware             │ │
│  └─────────────┘  └──────────────┘  └────────────────────────┘ │
│                                                                  │
│  POST /query  POST /query/stream  GET /health  POST /profile    │
└────────────┬───────────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────────────┐
│                   RAG Pipeline (generation/)                     │
│                                                                  │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────────┐  │
│  │  Retriever   │───▶│ Personalizer │───▶│  Generator       │  │
│  │  (search/)   │    │ (prompting)  │    │  OpenAI GPT-4o   │  │
│  └──────┬───────┘    └──────────────┘    └──────────────────┘  │
│         │                                                        │
│  ┌──────▼───────┐    ┌──────────────┐                           │
│  │  ChromaDB    │    │  Comparator  │ (Ukraine vs Germany)      │
│  │  (2 колекції)│    │              │                           │
│  └──────────────┘    └──────────────┘                           │
└─────────────────────────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────────────┐
│                   Дані (data/)                                   │
│                                                                  │
│  german_law/          ukrainian_context/      users.db          │
│  ├── sgb_ii.txt       ├── constitution.txt    (SQLite)          │
│  ├── aufenthg.txt     ├── labor_code.txt      ├── user_profiles │
│  ├── asylg.txt        └── …                  ├── user_documents │
│  └── …                                       └── reminders_sent │
└─────────────────────────────────────────────────────────────────┘
```

---

## Швидкий старт

### Вимоги
- Docker + Docker Compose
- OpenAI API ключ
- Telegram Bot Token (необов'язково)

### 1. Клонувати та налаштувати

```bash
git clone <repo-url>
cd rag-legal-support
cp .env.example .env
# Відредагуйте .env: OPENAI_API_KEY, TELEGRAM_BOT_TOKEN
```

`.env.example`:
```env
OPENAI_API_KEY=sk-...
TELEGRAM_BOT_TOKEN=...
LLM_MODEL=gpt-4o-mini
EMBEDDING_MODEL=paraphrase-multilingual-mpnet-base-v2
DATA_UPDATE_INTERVAL_HOURS=24
ALLOWED_ORIGINS=http://localhost:8000,http://localhost:5500
RATE_LIMIT_PER_IP_RPM=15
RATE_LIMIT_GLOBAL_RPM=200
```

### 2. Збудувати та запустити

```bash
make build   # збудувати Docker-образи
make index   # проіндексувати документи (перший раз)
make up      # запустити всі сервіси
```

### 3. Відкрити

| Сервіс | URL |
|--------|-----|
| Веб-інтерфейс | http://localhost:8000/web/ |
| API Docs (Swagger) | http://localhost:8000/docs |
| Health check | http://localhost:8000/health |

---

## Структура проекту

```
rag-legal-support/
├── api/                    # FastAPI додаток
│   ├── main.py             # Lifespan, middleware, роутери
│   ├── models.py           # Pydantic-моделі
│   ├── security.py         # Injection guard, sanitization
│   ├── middleware.py       # Rate limiting, security headers
│   └── routes/
│       ├── query.py        # POST /query, /query/stream
│       ├── health.py       # GET /health
│       └── profile.py      # POST /profile
│
├── generation/             # RAG-пайплайн
│   ├── generator.py        # generate() + generate_stream() (SSE)
│   ├── personalizer.py     # Побудова персоналізованого промпту
│   └── comparator.py       # Порівняльний контекст UA–DE
│
├── search/                 # Semantic retrieval
│   └── retriever.py        # Паралельний пошук у ChromaDB
│
├── ingestion/              # Індексація документів
│   ├── indexer.py          # Chunking + ChromaDB upsert
│   └── index_state.py      # SHA256-трекінг змін
│
├── bot/                    # Telegram-бот
│   ├── telegram_bot.py     # Handlers, ConversationHandler
│   └── db.py               # SQLite: профілі, документи, нагадування
│
├── data_collector/         # Автоматичне оновлення законів
│   ├── sources.py          # Реєстр джерел (SGB, AufenthG…)
│   ├── downloader.py       # kmein.de, EUR-Lex, rada.gov.ua
│   └── validator.py        # Валідація завантажених текстів
│
├── evaluation/             # Оцінка якості RAG
│   ├── golden_dataset.json # 20 еталонних питань
│   ├── evaluator.py        # RAG + LLM-суддя (4 метрики)
│   ├── baseline.py         # Чистий GPT без RAG
│   └── compare.py          # Порівняльний звіт
│
├── tests/                  # 162 тести
│   ├── test_bot_db.py      # SQLite storage (21 тест)
│   ├── test_bot_helpers.py # Bot utilities (39 тестів)
│   ├── test_security.py    # Security module (62 тести)
│   ├── test_api.py         # FastAPI endpoints
│   └── …
│
├── web/
│   └── index.html          # SPA: chat UI, streaming, i18n, dark mode
│
├── data/                   # Правові документи + users.db
├── docker-compose.yml
├── Dockerfile
├── Makefile
└── requirements.txt
```

---

## API

### `POST /query` — Синхронний запит
```json
{
  "question": "Чи можу я працювати з §24?",
  "profile": {
    "legal_status": "temporary_protection",
    "region": "Berlin",
    "query_category": "employment",
    "language": "uk"
  },
  "chat_history": []
}
```
**Відповідь:**
```json
{
  "answer": "Так, власники §24 AufenthG мають право на роботу...",
  "sources": ["aufenthg.txt", "beschaeftigungsverordnung.txt"],
  "has_comparison": true
}
```

### `POST /query/stream` — Streaming (SSE)
Той самий запит → `text/event-stream`:
```
data: {"token": "Так"}
data: {"token": ", власники"}
data: {"token": " §24..."}
data: {"done": true, "sources": ["aufenthg.txt"], "has_comparison": true}
```

### `GET /health`
```json
{"status": "ok", "chroma": "ok", "openai": "ok"}
```

---

## Оцінка якості

Запуск повної оцінки (потребує запущеного стеку):
```bash
make eval           # RAG: 20 питань × 4 метрики → evaluation/report.md
make eval-baseline  # GPT без RAG → evaluation/baseline_report.md
make eval-compare   # Порівняння → evaluation/comparison_report.md
```

### Результати оцінки (LLM-суддя: GPT-4o-mini, 20 питань)

| Метрика | RAG | Baseline (GPT без RAG) | Δ |
|---------|-----|------------------------|---|
| **Relevance** | **5.0 / 5** | 5.0 / 5 | ➖ |
| **Faithfulness** | **5.0 / 5** | 5.0 / 5 | ➖ |
| **Completeness** | **4.9 / 5** | 5.0 / 5 | −0.1 |
| **Clarity** | **5.0 / 5** | 5.0 / 5 | ➖ |
| **Keyword Hit Rate** | 27% | 29% | −2% |
| **Source Hit Rate** | **80%** | N/A | — |
| **Середня затримка** | 7 746 мс | 6 196 мс | +1 550 мс |

> **Примітка:** LLM-as-judge не диференціює системи за суб'єктивними метриками — це відома проблема авто-оцінювання. Реальна перевага RAG: **80% відповідей підкріплені конкретними правовими документами** (Source Hit Rate), тоді як baseline не надає жодних джерел. Додаткова затримка (~1.5 с) — ціна векторного пошуку.

### Опис метрик

| Метрика | Опис | Шкала |
|---------|------|-------|
| **Relevance** | Відповідь відповідає питанню | 1–5 |
| **Faithfulness** | Факти підкріплені джерелами | 1–5 |
| **Completeness** | Повнота відповіді | 1–5 |
| **Clarity** | Структурованість і зрозумілість | 1–5 |
| **Keyword Hit Rate** | % ключових юридичних термінів | 0–100% |
| **Source Hit Rate** | Правильне джерело знайдено | 0–100% |

---

## Безпека

| Загроза | Захист |
|---------|--------|
| Prompt injection / jailbreak | 15+ regex-патернів + Unicode-фільтр |
| DoS через дорогі API-виклики | 15 req/хв per-IP, 200 req/хв глобально |
| XSS у веб-інтерфейсі | Повне HTML-escaping + whitelist тегів |
| Clickjacking | `X-Frame-Options: DENY` |
| MIME-sniffing | `X-Content-Type-Options: nosniff` |
| Зловмисний CORS | Обмежений список `ALLOWED_ORIGINS` |
| PDF bomb / великі файли | Ліміт 5 МБ, max 20 сторінок |
| PII у логах | Truncate до 120 симв., маскування IP |

---

## Тестування

```bash
make test           # запустити всі тести
python -m pytest tests/ -v  # з детальним виводом
```

**Поточне покриття: 162 тести**

| Файл | Тести | Покриття |
|------|-------|---------|
| `test_bot_db.py` | 21 | SQLite профілі, документи, нагадування |
| `test_bot_helpers.py` | 39 | HTML-конвертер, greeting detection, profiling |
| `test_security.py` | 62 | Injection guard, rate limiter, security headers |
| `test_api.py` | 20 | Endpoints health/query/profile |
| `test_generator.py` | 10 | RAG generation pipeline |
| `test_retriever.py` | 10 | ChromaDB retrieval |

---

## Команди Makefile

```bash
make build          # збудувати Docker-образи
make up             # запустити всі сервіси
make down           # зупинити
make logs           # перегляд логів (всіх сервісів)
make logs-api       # логи тільки API
make logs-bot       # логи тільки бота
make test           # запустити тести
make index          # первинна індексація документів
make restart        # перезапустити api + bot
make status         # статус контейнерів
make clean          # зупинити + видалити volumes
make eval           # оцінка якості RAG
make eval-baseline  # оцінка baseline (без RAG)
make eval-compare   # порівняльний звіт
```

---

## Технічний стек

| Компонент | Технологія |
|-----------|-----------|
| API | FastAPI 0.115 + Uvicorn |
| LLM | OpenAI GPT-4o-mini |
| Embeddings | sentence-transformers (multilingual-mpnet) |
| Vector DB | ChromaDB 0.6.3 |
| Bot | python-telegram-bot 22 |
| Storage | SQLite (профілі + документи) |
| Scheduler | APScheduler (через PTB job_queue) |
| Containerization | Docker + Docker Compose |
| Testing | pytest 9 + pytest-asyncio |
