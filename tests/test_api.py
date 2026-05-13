"""Тести FastAPI ендпоінтів."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))

from api.main import app

client = TestClient(app)


class TestHealthEndpoint:
    def test_health_returns_200(self):
        """GET /health повертає HTTP 200."""
        response = client.get("/health")
        assert response.status_code == 200

    def test_health_response_structure(self):
        """GET /health повертає поля status, chroma, openai."""
        response = client.get("/health")
        data = response.json()

        assert "status" in data
        assert "chroma" in data
        assert "openai" in data

    def test_health_status_is_string(self):
        """Поле status є рядком."""
        response = client.get("/health")
        assert isinstance(response.json()["status"], str)


class TestQueryEndpoint:
    def _mock_generate_result(self):
        from generation.generator import GenerationResult
        return GenerationResult(
            answer="Відповідь: згідно з § 24 AufenthG ви маєте право на Bürgergeld.",
            sources=["sgb_ii.txt", "bamf_instructions.txt"],
            has_comparison=True,
        )

    def test_query_with_valid_body_returns_200(self):
        """POST /query з валідним тілом повертає 200."""
        with patch("api.routes.query.generate", return_value=self._mock_generate_result()):
            response = client.post(
                "/query",
                json={
                    "question": "Як отримати Bürgergeld?",
                    "profile": {
                        "legal_status": "temporary_protection",
                        "region": "Bayern",
                        "query_category": "social_benefits",
                        "language": "uk",
                    },
                },
            )
        assert response.status_code == 200

    def test_query_response_contains_answer(self):
        """Відповідь POST /query містить поле 'answer'."""
        with patch("api.routes.query.generate", return_value=self._mock_generate_result()):
            response = client.post(
                "/query",
                json={"question": "Питання про роботу?", "profile": {}},
            )
        data = response.json()
        assert "answer" in data
        assert len(data["answer"]) > 0

    def test_query_response_contains_sources(self):
        """Відповідь POST /query містить поле 'sources' (список)."""
        with patch("api.routes.query.generate", return_value=self._mock_generate_result()):
            response = client.post(
                "/query",
                json={"question": "Школа для дитини?", "profile": {}},
            )
        data = response.json()
        assert "sources" in data
        assert isinstance(data["sources"], list)

    def test_query_response_contains_has_comparison(self):
        """Відповідь POST /query містить поле 'has_comparison'."""
        with patch("api.routes.query.generate", return_value=self._mock_generate_result()):
            response = client.post(
                "/query",
                json={"question": "Медична страховка?", "profile": {}},
            )
        data = response.json()
        assert "has_comparison" in data
        assert isinstance(data["has_comparison"], bool)

    def test_query_too_short_returns_422(self):
        """POST /query з занадто коротким запитом повертає 422."""
        response = client.post(
            "/query",
            json={"question": "Як", "profile": {}},
        )
        assert response.status_code == 422

    def test_query_missing_question_returns_422(self):
        """POST /query без поля question повертає 422."""
        response = client.post(
            "/query",
            json={"profile": {}},
        )
        assert response.status_code == 422

    def test_query_default_profile_works(self):
        """POST /query без профілю використовує значення за замовчуванням."""
        with patch("api.routes.query.generate", return_value=self._mock_generate_result()):
            response = client.post(
                "/query",
                json={"question": "Загальне питання про права?"},
            )
        assert response.status_code == 200


class TestProfileEndpoint:
    def test_create_profile_returns_profile_id(self):
        """POST /profile повертає profile_id."""
        response = client.post(
            "/profile",
            json={
                "legal_status": "temporary_protection",
                "region": "Berlin",
                "query_category": "residence",
                "language": "uk",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert "profile_id" in data
        assert len(data["profile_id"]) > 0

    def test_get_profile_by_id(self):
        """GET /profile/{id} повертає збережений профіль."""
        create_resp = client.post(
            "/profile",
            json={"legal_status": "residence_permit", "region": "NRW", "language": "de"},
        )
        profile_id = create_resp.json()["profile_id"]

        get_resp = client.get(f"/profile/{profile_id}")
        assert get_resp.status_code == 200
        data = get_resp.json()
        assert data["legal_status"] == "residence_permit"
        assert data["region"] == "NRW"

    def test_get_nonexistent_profile_returns_404(self):
        """GET /profile/невідомий-id повертає 404."""
        response = client.get("/profile/nonexistent-id-12345")
        assert response.status_code == 404
