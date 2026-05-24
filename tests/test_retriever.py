"""Тести семантичного пошуку."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Додаємо корінь проєкту до sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))


def _make_chroma_result(texts: list[str], files: list[str], distances: list[float]) -> dict:
    """Будує mock-результат ChromaDB query."""
    return {
        "documents": [texts],
        "metadatas": [[{"source_file": f, "category": "german_law", "chunk_index": i} for i, f in enumerate(files)]],
        "distances": [distances],
    }


@pytest.fixture()
def mock_chroma_client():
    """Фіктивний клієнт ChromaDB."""
    client = MagicMock()
    client.heartbeat.return_value = True
    return client


@pytest.fixture()
def mock_model():
    """Фіктивна модель ембеддингів."""
    import numpy as np
    model = MagicMock()
    model.encode.return_value = np.zeros((1, 768))
    return model


class TestRetriever:
    def test_retrieve_temporary_protection_returns_bamf_chunks(self, mock_chroma_client, mock_model):
        """Пошук 'тимчасовий захист' повертає чанки з bamf_instructions.txt."""
        bamf_text = "Vorübergehender Schutz nach § 24 AufenthG für ukrainische Staatsangehörige"
        mock_collection = MagicMock()
        mock_collection.count.return_value = 5
        mock_collection.query.return_value = _make_chroma_result(
            [bamf_text, "Aufenthaltserlaubnis wird für ein Jahr erteilt"],
            ["bamf_instructions.txt", "aufenthaltsgesetz.txt"],
            [0.12, 0.25],
        )
        mock_chroma_client.get_collection.return_value = mock_collection

        with (
            patch("search.retriever._get_client", return_value=mock_chroma_client),
            patch("search.retriever._get_embedding_model", return_value=mock_model),
        ):
            from search.retriever import retrieve

            results = retrieve("тимчасовий захист", "german_law", top_k=5, client=mock_chroma_client)

        assert len(results) >= 1
        source_files = [r["source_file"] for r in results]
        assert "bamf_instructions.txt" in source_files

    def test_retrieve_buergergeld_returns_sgb_ii_chunks(self, mock_chroma_client, mock_model):
        """Пошук 'Bürgergeld' повертає чанки з sgb_ii.txt."""
        mock_collection = MagicMock()
        mock_collection.count.return_value = 5
        mock_collection.query.return_value = _make_chroma_result(
            ["Bürgergeld — staatliche Grundsicherungsleistung. Regelbedarfe 2024: 563 Euro"],
            ["sgb_ii.txt"],
            [0.08],
        )
        mock_chroma_client.get_collection.return_value = mock_collection

        with (
            patch("search.retriever._get_client", return_value=mock_chroma_client),
            patch("search.retriever._get_embedding_model", return_value=mock_model),
        ):
            from search.retriever import retrieve

            results = retrieve("Bürgergeld", "german_law", top_k=5, client=mock_chroma_client)

        assert len(results) >= 1
        assert results[0]["source_file"] == "sgb_ii.txt"

    def test_retrieve_empty_query_returns_empty(self, mock_chroma_client):
        """Порожній запит повертає порожній список без звернення до БД."""
        with patch("search.retriever._get_client", return_value=mock_chroma_client):
            from search.retriever import retrieve

            results = retrieve("   ", "german_law", client=mock_chroma_client)

        assert results == []
        mock_chroma_client.get_collection.assert_not_called()

    def test_retrieve_missing_collection_returns_empty(self, mock_chroma_client, mock_model):
        """Якщо колекція не існує — повертає порожній список."""
        mock_chroma_client.get_collection.side_effect = Exception("Collection not found")

        with (
            patch("search.retriever._get_client", return_value=mock_chroma_client),
            patch("search.retriever._get_embedding_model", return_value=mock_model),
        ):
            from search.retriever import retrieve

            results = retrieve("будь-який запит", "german_law", client=mock_chroma_client)

        assert results == []

    def test_retrieve_result_structure(self, mock_chroma_client, mock_model):
        """Перевірка структури кожного результату."""
        mock_collection = MagicMock()
        mock_collection.count.return_value = 3
        mock_collection.query.return_value = _make_chroma_result(
            ["Krankenversicherung ist Pflicht in Deutschland"],
            ["krankenversicherung.txt"],
            [0.18],
        )
        mock_chroma_client.get_collection.return_value = mock_collection

        with (
            patch("search.retriever._get_client", return_value=mock_chroma_client),
            patch("search.retriever._get_embedding_model", return_value=mock_model),
        ):
            from search.retriever import retrieve

            results = retrieve("медичне страхування", "german_law", client=mock_chroma_client)

        assert len(results) == 1
        r = results[0]
        assert "text" in r
        assert "source_file" in r
        assert "category" in r
        assert "distance" in r
        assert isinstance(r["distance"], float)
