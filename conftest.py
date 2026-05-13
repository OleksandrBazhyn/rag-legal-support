"""Глобальні фікстури pytest."""
import os
import sys
from pathlib import Path

# Забезпечуємо правильний PYTHONPATH при запуску з будь-якої директорії
root = Path(__file__).parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

# Встановлюємо тестові змінні оточення
os.environ.setdefault("OPENAI_API_KEY", "sk-test-dummy-key")
os.environ.setdefault("CHROMA_HOST", "localhost")
os.environ.setdefault("CHROMA_PORT", "8001")
os.environ.setdefault("EMBEDDING_MODEL", "paraphrase-multilingual-mpnet-base-v2")
os.environ.setdefault("LLM_MODEL", "gpt-4o-mini")
