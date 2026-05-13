"""FastAPI додаток — правова підтримка для українців у Німеччині."""
from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# Завантаження .env
load_dotenv(Path(__file__).parent.parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

from api.routes.query import router as query_router
from api.routes.profile import router as profile_router
from api.routes.health import router as health_router

app = FastAPI(
    title="Система правової підтримки для українців у Німеччині",
    description=(
        "RAG-система, що надає персоналізовані відповіді на правові запити "
        "українських громадян на території ФРН з урахуванням порівняльного "
        "контексту між правовими системами України та Німеччини."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(query_router, tags=["Запити"])
app.include_router(profile_router, tags=["Профіль"])
app.include_router(health_router, tags=["Система"])

# Статичні файли (веб-інтерфейс)
web_dir = Path(__file__).parent.parent / "web"
if web_dir.exists():
    app.mount("/web", StaticFiles(directory=str(web_dir), html=True), name="web")


@app.get("/", include_in_schema=False)
async def root():
    return {
        "service": "Правова підтримка для українців у Німеччині",
        "docs": "/docs",
        "health": "/health",
        "web": "/web/",
    }
