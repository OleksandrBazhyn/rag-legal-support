# Makefile для управління RAG Legal Support
.PHONY: help build up down logs test index restart clean eval eval-baseline eval-compare

help:
	@echo "Доступні команди:"
	@echo "  make build         — зібрати Docker-образи"
	@echo "  make up            — запустити всі сервіси"
	@echo "  make down          — зупинити всі сервіси"
	@echo "  make logs          — перегляд логів"
	@echo "  make test          — запустити тести"
	@echo "  make index         — первинна індексація документів"
	@echo "  make restart       — перезапустити (зберігаючи дані)"
	@echo "  make clean         — зупинити і видалити volumes"
	@echo "  make eval          — оцінка якості RAG (20 питань + LLM-суддя)"
	@echo "  make eval-baseline — оцінка базової лінії (GPT без RAG)"
	@echo "  make eval-compare  — порівняння RAG vs baseline"

build:
	docker compose build

up:
	docker compose up -d
	@echo "Сервіси запущено. API: http://localhost:8000/docs"

down:
	docker compose down

logs:
	docker compose logs -f

logs-api:
	docker compose logs -f api

logs-bot:
	docker compose logs -f bot

test:
	python -m pytest tests/ -v

index:
	docker compose --profile init up indexer --abort-on-container-exit

restart:
	docker compose restart api bot

clean:
	docker compose down -v
	@echo "Volumes видалено. Дані ChromaDB втрачено!"

status:
	docker compose ps

eval:
	python -m evaluation.evaluator --api http://localhost:8000

eval-baseline:
	python -m evaluation.baseline

eval-compare:
	python -m evaluation.compare
