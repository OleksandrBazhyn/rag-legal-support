"""Управління станом завантажень: кешування SHA/ETag та токен Ради."""
from __future__ import annotations

import json
import os
from datetime import datetime, time
from pathlib import Path
from typing import Any, Optional


_DEFAULT_STATE_FILE = Path(__file__).parent.parent / "data" / ".collection_state.json"


class StateManager:
    """Зберігає та читає стан завантажень у JSON-файлі."""

    def __init__(self, state_file: Path | str = _DEFAULT_STATE_FILE) -> None:
        self.state_file = Path(state_file)
        self._state: dict[str, Any] = self._load()

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self) -> dict[str, Any]:
        if not self.state_file.exists():
            return {"documents": {}}
        try:
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {"documents": {}}

    def save(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(
            json.dumps(self._state, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

    # ── Document state ────────────────────────────────────────────────────────

    def get_doc(self, output_path: str) -> dict[str, Any]:
        return self._state.setdefault("documents", {}).get(output_path, {})

    def set_doc(self, output_path: str, **kwargs: Any) -> None:
        docs = self._state.setdefault("documents", {})
        entry = docs.setdefault(output_path, {})
        entry.update(kwargs)
        entry["last_downloaded"] = datetime.now().isoformat(timespec="seconds")

    def get_github_sha(self, output_path: str) -> Optional[str]:
        return self.get_doc(output_path).get("github_sha")

    def get_eurlex_etag(self, output_path: str) -> Optional[str]:
        return self.get_doc(output_path).get("etag")

    def get_eurlex_last_modified(self, output_path: str) -> Optional[str]:
        return self.get_doc(output_path).get("last_modified_header")

    def get_rada_last_modified(self, output_path: str) -> Optional[str]:
        return self.get_doc(output_path).get("last_modified_header")

    def set_last_full_collection(self) -> None:
        self._state["last_full_collection"] = datetime.now().isoformat(timespec="seconds")

    def all_docs(self) -> dict[str, dict]:
        return self._state.get("documents", {})

    # ── Rada token ────────────────────────────────────────────────────────────

    def get_rada_token(self) -> Optional[str]:
        """Повертає дійсний токен Ради або None якщо протермінований/відсутній."""
        token_data = self._state.get("rada_token", {})
        value = token_data.get("value")
        valid_until_str = token_data.get("valid_until")

        if not value or not valid_until_str:
            return None

        try:
            valid_until = datetime.fromisoformat(valid_until_str)
            if datetime.now() >= valid_until:
                return None
        except ValueError:
            return None

        return value

    def save_rada_token(self, token: str, expire_seconds: int) -> None:
        """Зберігає токен Ради з терміном дії до кінця поточної доби."""
        now = datetime.now()
        # Токен діє до 23:59:59 поточного дня (незалежно від expire_seconds)
        valid_until = datetime.combine(now.date(), time(23, 59, 59))
        self._state["rada_token"] = {
            "value": token,
            "obtained_at": now.isoformat(timespec="seconds"),
            "valid_until": valid_until.isoformat(timespec="seconds"),
        }
        self.save()

    def clear_rada_token(self) -> None:
        self._state.pop("rada_token", None)
        self.save()
