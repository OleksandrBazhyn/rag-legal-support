# Інструкція з розгортання RAG Legal Support

## Зміст
1. [Передумови](#1-передумови)
2. [Клонування та налаштування](#2-клонування-та-налаштування)
3. [Запуск локально (без Docker)](#3-запуск-локально-без-docker)
4. [Запуск у Docker](#4-запуск-у-docker)
5. [Збір та індексування документів](#5-збір-та-індексування-документів)
6. [Перевірка роботи](#6-перевірка-роботи)
7. [Часті проблеми](#7-часті-проблеми)

---

## 1. Передумови

| Компонент | Версія | Призначення |
|-----------|--------|------------|
| Python | ≥ 3.11 | Локальний запуск |
| Docker Desktop | ≥ 4.x | Docker-розгортання |
| Docker Compose | ≥ 2.x | Оркестрація контейнерів |
| OpenAI API key | — | Генерація відповідей (GPT-4o-mini) |
| Telegram Bot Token | — | Telegram-бот (опціонально) |

---

## 2. Клонування та налаштування

```bash
git clone <repo-url>
cd rag-legal-support
```

Створіть файл `.env` у корені проєкту:

```env
OPENAI_API_KEY=sk-...
TELEGRAM_BOT_TOKEN=123456:ABC...   # якщо потрібен бот
LLM_MODEL=gpt-4o-mini              # або gpt-4o
EMBEDDING_MODEL=paraphrase-multilingual-mpnet-base-v2
CHROMA_HOST=localhost
CHROMA_PORT=8001
RATE_LIMIT_PER_IP_RPM=15
RATE_LIMIT_GLOBAL_RPM=200
ALLOWED_ORIGINS=http://localhost:8000,http://localhost:5500
```

---

## 3. Запуск локально (без Docker)

### 3.1 Встановлення залежностей

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/Mac:
source .venv/bin/activate

pip install -r requirements.txt
```

### 3.2 Запуск ChromaDB локально

ChromaDB потрібен як окремий процес:

```bash
pip install chromadb
chroma run --host localhost --port 8001 --path ./chroma_data
```

Або через Docker (тільки ChromaDB):

```bash
docker run -d --name chromadb \
  -p 8001:8000 \
  -v chroma_data:/chroma/chroma \
  -e ANONYMIZED_TELEMETRY=false \
  chromadb/chroma:0.6.3
```

### 3.3 Збір документів

```bash
# Перевірити статус документів
python data_collector/collect.py --mode check

# Завантажити нові / оновлені документи
python data_collector/collect.py --mode update

# Примусове повне перезавантаження всіх документів
python data_collector/collect.py --mode full
```

### 3.4 Індексування в ChromaDB

```bash
# Повне переіндексування (очищає колекції та індексує заново)
python -m ingestion.indexer

# Перевірити кількість документів у колекціях
python -c "
import chromadb
c = chromadb.HttpClient(host='localhost', port=8001)
print('german_law:', c.get_collection('german_law').count())
print('ukrainian_context:', c.get_collection('ukrainian_context').count())
"
```

### 3.5 Запуск FastAPI

```bash
uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
```

API доступний за адресою: http://localhost:8000  
Swagger UI: http://localhost:8000/docs

### 3.6 Запуск Telegram-бота (окремий термінал)

```bash
python bot/telegram_bot.py
```

---

## 4. Запуск у Docker

### 4.1 Перший запуск (з нуля)

```bash
# Крок 1: зібрати образи
docker compose build

# Крок 2: запустити ChromaDB
docker compose up -d chromadb

# Крок 3: проіндексувати документи (одноразово)
docker compose --profile init up indexer
# Зачекати до завершення (5-15 хв залежно від машини)

# Крок 4: запустити API та бота
docker compose up -d api bot
```

### 4.2 Звичайний запуск (після першої ініціалізації)

```bash
docker compose up -d
```

### 4.3 Перевірка стану

```bash
docker compose ps
docker compose logs api --tail=20
docker compose logs bot --tail=20
```

### 4.4 Оновлення коду (після змін у файлах)

```bash
# ВАЖЛИВО: restart НЕ підтягує зміни коду — потрібен rebuild
docker compose build api bot
docker compose up -d api bot
```

### 4.5 Повне переіндексування у Docker

```bash
# Зупинити API (щоб не було конфліктів з ChromaDB)
docker compose stop api bot

# Запустити indexer
docker compose --profile init up indexer

# Запустити знову
docker compose up -d api bot
```

### 4.6 Корисні команди Docker

```bash
# Перезапуск одного сервісу
docker compose restart api

# Перегляд логів у реальному часі
docker compose logs -f api

# Зайти в контейнер
docker compose exec api bash

# Перевірити кількість індексованих документів
docker compose exec api python -c "
import chromadb
c = chromadb.HttpClient(host='chromadb', port=8000)
print('german_law:', c.get_collection('german_law').count())
print('ukrainian_context:', c.get_collection('ukrainian_context').count())
"

# Зупинити все
docker compose down

# Зупинити і видалити volumes (УВАГА: видаляє індекс ChromaDB!)
docker compose down -v
```

---

## 5. Збір та індексування документів

### 5.1 Джерела документів

| Джерело | URL | Що містить |
|---------|-----|-----------|
| kmein/gesetze | github.com/kmein/gesetze | Закони ФРН (AufenthG, SGB II/III/V/XII, AsylG, AsylbLG тощо) |
| EUR-Lex | eur-lex.europa.eu | Директиви ЄС (2001/55/EG, 2013/33/EU) |
| Верховна Рада | data.rada.gov.ua | Закони України (ЗУ про біженців, КЗпП тощо) |

### 5.2 Команди data_collector

```bash
# Показати статус усіх документів
python data_collector/collect.py --mode check

# Завантажити тільки змінені (SHA-порівняння)
python data_collector/collect.py --mode update

# Примусово перезавантажити всі
python data_collector/collect.py --mode full

# Тільки один тип джерел
python data_collector/collect.py --mode update --source kmein
python data_collector/collect.py --mode update --source eurlex
python data_collector/collect.py --mode update --source rada
```

### 5.3 Структура data/

```
data/
├── german_law/
│   ├── aufenthaltsgesetz.txt          # AufenthG
│   ├── asylbewerberleistungsgesetz.txt # AsylbLG
│   ├── asylgesetz.txt                 # AsylG
│   ├── sgb_ii.txt                     # SGB II (Bürgergeld)
│   ├── sgb_iii.txt                    # SGB III (зайнятість)
│   ├── sgb_v.txt                      # SGB V (медстрах)
│   ├── sgb_xii.txt                    # SGB XII (соціальна допомога)
│   ├── wohngeldgesetz.txt             # Житлова допомога
│   ├── bkgg.txt                       # Допомога на дитину
│   ├── bqfg.txt                       # Визнання кваліфікацій
│   ├── mindestlohngesetz.txt          # Мінімальна зарплата
│   ├── arbeitszeitgesetz.txt          # Робочий час
│   ├── bundesurlaubsgesetz.txt        # Відпустка
│   ├── kuendigungsschutzgesetz.txt    # Захист від звільнення
│   ├── agg.txt                        # Захист від дискримінації
│   ├── beschaeftigungsverordnung.txt  # Дозволи на роботу
│   ├── bamf_instructions.txt          # Інструкції BAMF
│   ├── schulpflicht.txt               # Шкільна освіта
│   └── eu_directive_*.txt             # Директиви ЄС
└── ukrainian_context/
    ├── ua_law_refugees.txt            # ЗУ про біженців
    ├── ua_labor_code.txt              # КЗпП України
    ├── ua_employment_law.txt          # ЗУ про зайнятість
    ├── ua_social_services.txt         # ЗУ про соцпослуги
    ├── ua_idp_law.txt                 # ЗУ про ВПО
    ├── social_benefits_comparison.txt # Порівняння UA↔DE соцвиплат
    ├── employment_comparison.txt      # Порівняння трудового права
    └── residence_comparison.txt       # Порівняння статусів проживання
```

---

## 6. Перевірка роботи

### 6.1 Health check API

```bash
curl http://localhost:8000/health
# {"status":"healthy","chromadb":"connected","bm25":"ready",...}
```

### 6.2 Тестовий запит

```bash
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{
    "question": "Які виплати я отримаю з тимчасовим захистом?",
    "profile": {
      "legal_status": "temporary_protection",
      "region": "Berlin",
      "language": "uk",
      "query_category": "social_benefits"
    }
  }'
```

### 6.3 Запуск автотестів

```bash
# Всі 162 тести
pytest tests/ -v

# Тільки один модуль
pytest tests/test_retriever.py -v
pytest tests/test_api.py -v

# З покриттям
pytest tests/ --cov=. --cov-report=term-missing
```

### 6.4 RAGAS оцінювання якості

```bash
# Повна оцінка на 4 питаннях (тема Bürgergeld)
python -m evaluation.ragas_eval --topic "Bürgergeld"

# Всі 20 питань, всі теми
python -m evaluation.ragas_eval

# Тільки Generation метрики (без ground truth)
python -m evaluation.ragas_eval --topic "Bürgergeld" --no-ground-truth
```

### 6.5 Swagger UI

Відкрити в браузері: **http://localhost:8000/docs**

---

## 7. Часті проблеми

### ChromaDB недоступний
```
ConnectionRefusedError: [Errno 111] Connection refused
```
**Рішення:** переконайтесь що ChromaDB запущений (`docker compose ps` або `chroma run ...`)

### Колекції порожні після запуску
```
german_law: 0 documents
```
**Рішення:** запустіть indexer:
```bash
docker compose --profile init up indexer
# або локально:
python -m ingestion.indexer
```

### Зміни в коді не застосовуються у Docker
**Рішення:** потрібен rebuild, не просто restart:
```bash
docker compose build api bot
docker compose up -d api bot
```

### Таймаут бота на складних запитах
Якщо бот не відповідає на довгі запити — перевірте `timeout` у `bot/telegram_bot.py` (зараз 120 сек).

### EUR-Lex повертає 404
EUR-Lex змінює URL структуру. Наявні файли залишаються без змін (`-- без змін`). Якщо потрібне оновлення — виправте URL у `data_collector/sources.py`.

### OpenAI помилка 401
```
AuthenticationError: Invalid API key
```
**Рішення:** перевірте `OPENAI_API_KEY` у `.env` файлі.

---

*Технічний стек: Python 3.11, FastAPI 0.115, ChromaDB 0.6.3, sentence-transformers 3.1.1, OpenAI 2.36.0, python-telegram-bot 22.7, Docker Compose*
