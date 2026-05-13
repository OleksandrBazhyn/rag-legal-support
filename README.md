# Система правової підтримки для українців у Німеччині

RAG-система (Retrieval-Augmented Generation) для надання персоналізованих відповідей на правові питання українських громадян у ФРН. Порівнює правові норми Німеччини та України, враховує правовий статус та регіон користувача.

## Архітектура

```
data_collector/ → data/ → ingestion/ → ChromaDB → search/ → generation/ → api/ / bot/
```

| Компонент | Опис |
|---|---|
| `data_collector/` | Завантаження правових документів з kmein/gesetze, EUR-Lex, data.rada.gov.ua |
| `ingestion/` | Розбиття на чанки та векторизація (sentence-transformers) |
| `search/` | Семантичний пошук у ChromaDB |
| `generation/` | Персоналізація промпту + GPT-4o-mini |
| `api/` | FastAPI REST API |
| `bot/` | Telegram-бот |

## Джерела правових документів

| Документ | Джерело | Частота оновлення | Статус |
|---|---|---|---|
| Aufenthaltsgesetz (AufenthG) | [kmein/gesetze](https://github.com/kmein/gesetze) | При зміні SHA | Активний |
| Asylgesetz (AsylG) | kmein/gesetze | При зміні SHA | Активний |
| Asylbewerberleistungsgesetz (AsylbLG) | kmein/gesetze | При зміні SHA | Активний |
| Beschäftigungsverordnung (BeschV) | kmein/gesetze | При зміні SHA | Активний |
| SGB II — Bürgergeld | kmein/gesetze | При зміні SHA | Активний |
| SGB XII — Sozialhilfe | kmein/gesetze | При зміні SHA | Активний |
| Директива ЄС 2001/55/EG (тимчасовий захист) | EUR-Lex | При зміні ETag | Резервний mock |
| Директива ЄС 2013/33/EU (умови прийому) | EUR-Lex | При зміні ETag | Резервний mock |
| Закон України про біженців (3671-17) | data.rada.gov.ua | Щоденно | Активний |
| КЗпП України (322-08) | data.rada.gov.ua | Щоденно | Активний |
| Закон про зайнятість населення (5067-17) | data.rada.gov.ua | Щоденно | Активний |
| Закон про соціальні послуги (2811-20) | data.rada.gov.ua | Щоденно | Активний |

> **Примітка:** EUR-Lex захищений AWS WAF і блокує автоматичні запити. Директиви ЄС зберігаються як вивірені резервні тексти та оновлюються вручну при виході нових редакцій.

## Перший запуск

### 1. Налаштування середовища

```bash
cp .env.example .env
```

Відредагуйте `.env`:
```
OPENAI_API_KEY=sk-...          # Обов'язково
TELEGRAM_BOT_TOKEN=...         # Для Telegram-бота
GITHUB_TOKEN=...               # Рекомендовано (60 req/h → 5000 req/h)
EMBEDDING_MODEL=paraphrase-multilingual-mpnet-base-v2
LLM_MODEL=gpt-4o-mini
```

### 2. Встановлення залежностей

```bash
pip install -r requirements.txt
```

### 3. Завантаження правових документів

```bash
# Перше повне завантаження (~5-10 хвилин через rate-limiting Ради)
python data_collector/collect.py --mode full

# Перевірка статусу завантажених документів
python data_collector/collect.py --mode check
```

### 4. Індексування в ChromaDB

```bash
# Перша індексація (~5-8 хвилин на CPU)
python -m ingestion.indexer
```

Індекс зберігається в `~/.chroma_rag_legal/` (ASCII-безпечний шлях для коректної роботи hnswlib на Windows).

### 5. Запуск API

```bash
uvicorn api.main:app --reload --port 8000
```

API автоматично виконає розумний re-index при старті:
- Якщо ChromaDB порожній → повна індексація
- Якщо є змінені файли → переіндексація лише їх

### 6. Запуск Telegram-бота (опціонально)

```bash
python -m bot.telegram_bot
```

### 7. Docker Compose (рекомендовано для продакшну)

```bash
docker compose up --build
```

## API Endpoints

| Метод | URL | Опис |
|---|---|---|
| `GET` | `/health` | Стан сервісу (ChromaDB + OpenAI) |
| `POST` | `/query` | Правовий запит (основний endpoint) |
| `GET` | `/profile/{id}` | Отримати профіль користувача |
| `POST` | `/profile` | Зберегти профіль |
| `POST` | `/admin/reindex` | Повне переіндексування |
| `POST` | `/admin/refresh-data` | Оновити дані + переіндексувати змінені |

### Приклад запиту

```bash
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{
    "question": "Які соціальні виплати я можу отримати як власник §24 AufenthG?",
    "profile": {
      "legal_status": "temporary_protection",
      "region": "Bayern",
      "language": "uk"
    }
  }'
```

## Структура проєкту

```
rag-legal-support/
├── data/
│   ├── german_law/          # Німецьке законодавство (txt)
│   ├── ukrainian_context/   # Українське законодавство (txt)
│   └── .collection_state.json  # Стан завантажень
├── data_collector/          # Модуль збору даних
│   ├── sources.py           # Каталог документів
│   ├── downloader.py        # HTTP-завантаження з кешуванням
│   ├── converter.py         # MD→txt, HTML→txt
│   ├── validator.py         # Валідація перед збереженням
│   ├── state_manager.py     # JSON-стан (SHA, ETag, токен Ради)
│   └── collect.py           # CLI: --mode check|update|full
├── ingestion/
│   ├── loader.py            # Завантаження txt/pdf файлів
│   ├── chunker.py           # Розбиття на чанки (512 токенів)
│   ├── indexer.py           # Векторизація + ChromaDB
│   └── index_state.py       # SHA256-стан проіндексованих файлів
├── search/
│   └── retriever.py         # Семантичний пошук (cosine similarity)
├── generation/
│   ├── personalizer.py      # Побудова персоналізованого промпту
│   ├── comparator.py        # Порівняння UA/DE норм
│   └── generator.py         # GPT-4o-mini відповідь
├── api/
│   ├── main.py              # FastAPI app + startup reindex + daily update
│   ├── models.py            # Pydantic моделі
│   └── routes/
│       ├── query.py         # POST /query
│       ├── profile.py       # GET|POST /profile
│       └── health.py        # GET /health, POST /admin/*
├── bot/
│   └── telegram_bot.py      # Telegram ConversationHandler
├── web/
│   └── index.html           # Веб-інтерфейс (Vanilla JS SPA)
├── tests/
│   ├── test_api.py          # 13 тестів API
│   ├── test_generator.py    # 16 тестів generation pipeline
│   ├── test_retriever.py    # 5 тестів retriever
│   └── test_data_collector.py  # 5 тестів data_collector
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
└── .env.example
```

## Оновлення даних

### Ручне оновлення

```bash
# Лише змінені файли (з кешуванням SHA/ETag)
python data_collector/collect.py --mode update

# Примусове повне перезавантаження
python data_collector/collect.py --mode full

# Лише з одного джерела
python data_collector/collect.py --source kmein
python data_collector/collect.py --source rada
```

### Автоматичне оновлення через API

```bash
# Оновити дані та переіндексувати змінені файли
curl -X POST http://localhost:8000/admin/refresh-data
# → {"status":"ok","updated_files":["sgb_ii.txt"],"reindexed":12}
```

API також виконує автоматичне оновлення кожні 24 години у фоновому режимі.

## Тестування

```bash
pytest tests/ -v
```

34+ тести охоплюють: API endpoints, RAG pipeline, retriever, data collector.

## Технічний стек

- **Python** 3.11+
- **FastAPI** 0.115 + Uvicorn
- **ChromaDB** 0.6.3 (persistent local або HTTP)
- **sentence-transformers** 3.1 (`paraphrase-multilingual-mpnet-base-v2`)
- **OpenAI** GPT-4o-mini (temperature=0.3)
- **python-telegram-bot** 22.7
- **Docker** + Docker Compose
