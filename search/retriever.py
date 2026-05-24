"""Семантичний пошук у векторній базі ChromaDB.

Покращений пайплайн пошуку:
  1. Hybrid search  — Reciprocal Rank Fusion (vector cosine + BM25)
  2. Profile boost  — підвищення ранку документів, релевантних до правового статусу
  3. Cross-encoder reranking — точне попарне зіставлення (query, passage)
  4. Query cache    — TTL-кеш (5 хв) для повторних запитів

BM25-корпус будується у фоні одразу після старту; до готовності система
працює у режимі чисто векторного пошуку (деградація без відмови).
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

import chromadb
from chromadb.config import Settings
from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "paraphrase-multilingual-mpnet-base-v2")
RERANKER_MODEL  = os.getenv("RERANKER_MODEL",  "cross-encoder/ms-marco-MiniLM-L-6-v2")
CHROMA_HOST     = os.getenv("CHROMA_HOST", "localhost")
CHROMA_PORT     = int(os.getenv("CHROMA_PORT", "8001"))

_DEFAULT_CHROMA_DIR = Path.home() / ".chroma_rag_legal"
CHROMA_PERSIST_DIR  = Path(os.getenv("CHROMA_PERSIST_DIR", str(_DEFAULT_CHROMA_DIR)))

COLLECTION_GERMAN     = "german_law"
COLLECTION_UKRAINIAN  = "ukrainian_context"

# ─── Правило буст-скорингу: статус → частини назв файлів з перевагою ─────────
_STATUS_FILE_BOOST: dict[str, list[str]] = {
    "temporary_protection": [
        "aufenthaltsgesetz", "sgb_ii", "sgb_v", "beschaeftigungsverordnung",
        "eu_directive_temporary_protection", "bkgg", "wohngeldgesetz",
        "mindestlohngesetz", "kuendigungsschutz", "arbeitszeitgesetz",
    ],
    "asylum_seeker": [
        "asylgesetz", "asylbewerberleistungsgesetz",
        "eu_directive_reception_conditions", "aufenthaltsgesetz",
    ],
    "residence_permit": [
        "aufenthaltsgesetz", "sgb_ii", "sgb_xii", "bqfg",
        "beschaeftigungsverordnung", "bundesurlaubsgesetz",
    ],
}
_PROFILE_BOOST_DELTA        = 0.12  # буст до rrf_score (до cross-encoder)
_PROFILE_BOOST_DELTA_RERANK = 3.00  # буст до rerank_score (після cross-encoder, шкала -5..+5)

# Якщо ці файли не потрапили до top-k через semantic search — інжектуємо вручну.
# Тільки для German law (критичні закони по статусу).
# Гарантовані джерела за правовим статусом:
# Лише для законів, що охоплюють БІЛЬШІСТЬ запитів даного статусу.
# Вузько-тематичні закони (bqfg → диплом, wohngeld → житло) керуються
# через _CATEGORY_CRITICAL_GERMAN за категорією запиту.
_STATUS_CRITICAL_GERMAN: dict[str, list[str]] = {
    "asylum_seeker": ["asylbewerberleistungsgesetz.txt"],
}

# Гарантовані джерела за категорією запиту (незалежно від статусу)
_CATEGORY_CRITICAL_GERMAN: dict[str, list[str]] = {
    "education":      ["bqfg.txt"],
    "social_benefits": [],
    "employment":     [],
    "residence":      [],
    "healthcare":     [],
    "other":          [],
}

# Ukrainian context: forced injection навмисно відсутній —
# порівняльний блок UA↔DE з'являється лише при органічному збігу (distance < 0.50)
# щоб LLM не генерував порівняння без перевіреного контексту.
_STATUS_CRITICAL_UKRAINIAN: dict[str, list[str]] = {}

# Ключові слова для авто-визначення категорії, якщо query_category == "other"
# Перевірка нечутлива до регістру. Перший збіг виграє (порядок важливий).
_CATEGORY_KEYWORDS: list[tuple[str, list[str]]] = [
    ("education", [
        "диплом", "кваліфікац", "ступінь", "бакалавр", "магістр", "освіт",
        "визнання документ", "аналог", "nostrification", "anerkennung",
        "bqfg", "zab", "anabin",
    ]),
    ("healthcare", [
        "лікар", "медицин", "лікуванн", "страхуванн", "krankenversicherung",
        "krankenhau", "arzt", "gesundheit", "аптека", "рецепт",
    ]),
    ("employment", [
        "робот", "прац", "зайнятіст", "звільнен", "відпустк",
        "зарплат", "arbeit", "beschäftig", "kündigung", "mindestlohn",
    ]),
    ("social_benefits", [
        "виплат", "допомог", "bürgergeld", "бюргергельд", "sozialleistung",
        "jobcenter", "sgb ii", "sgb 2", "пособ",
    ]),
    ("residence", [
        "дозвіл на проживанн", "aufenthaltserlaubnis", "aufenthaltstitel",
        "реєстрац", "anmeldung", "meldebescheinigung", "статус проживанн",
    ]),
]


def _detect_query_category(query: str, provided: str = "other") -> str:
    """Авто-визначення категорії запиту за ключовими словами.

    Якщо provided != "other" — повертає provided без змін (профіль пріоритетний).
    Якщо provided == "other" — намагається визначити категорію за текстом запиту.
    """
    if provided != "other":
        return provided
    q_lower = query.lower()
    for category, keywords in _CATEGORY_KEYWORDS:
        if any(kw in q_lower for kw in keywords):
            logger.debug("Авто-категорія: '%s' → %s", query[:50], category)
            return category
    return "other"


_chroma_client: chromadb.ClientAPI | None = None


def _get_client() -> chromadb.ClientAPI:
    """Повертає клієнт ChromaDB; перевіряє heartbeat при кожному виклику."""
    global _chroma_client
    if _chroma_client is not None:
        try:
            _chroma_client.heartbeat()
            return _chroma_client
        except Exception:
            logger.warning("ChromaDB heartbeat failed — reconnecting…")
            _chroma_client = None

    try:
        client = chromadb.HttpClient(
            host=CHROMA_HOST,
            port=CHROMA_PORT,
            settings=Settings(anonymized_telemetry=False),
        )
        client.heartbeat()
        logger.info("Підключено до ChromaDB: %s:%s", CHROMA_HOST, CHROMA_PORT)
        _chroma_client = client
        return client
    except Exception:
        logger.warning("ChromaDB HTTP недоступний — локальний PersistentClient.")
        CHROMA_PERSIST_DIR.mkdir(parents=True, exist_ok=True)
        _chroma_client = chromadb.PersistentClient(
            path=str(CHROMA_PERSIST_DIR),
            settings=Settings(anonymized_telemetry=False),
        )
        return _chroma_client


@lru_cache(maxsize=1)
def _get_embedding_model() -> SentenceTransformer:
    logger.info("Завантаження embedding-моделі: %s", EMBEDDING_MODEL)
    return SentenceTransformer(EMBEDDING_MODEL, device="cpu")


_reranker_lock = threading.Lock()
_reranker: Any = None   # CrossEncoder | None


def _get_reranker() -> Any | None:
    """Повертає cross-encoder або None якщо недоступний."""
    global _reranker
    if _reranker is not None:
        return _reranker
    with _reranker_lock:
        if _reranker is not None:
            return _reranker
        try:
            from sentence_transformers import CrossEncoder
            _reranker = CrossEncoder(RERANKER_MODEL, device="cpu")
            logger.info("Cross-encoder завантажено: %s", RERANKER_MODEL)
        except Exception as exc:
            logger.warning("Cross-encoder недоступний (%s) — reranking вимкнено.", exc)
            _reranker = None
    return _reranker


_bm25_corpus: dict[str, tuple[Any, list[dict]]] = {}  # col → (BM25Okapi, corpus)
_bm25_lock   = threading.Lock()
_bm25_ready  = threading.Event()
_HAS_BM25    = False

try:
    from rank_bm25 import BM25Okapi
    _HAS_BM25 = True
except ImportError:
    logger.warning("rank-bm25 не встановлено — hybrid search вимкнено. pip install rank-bm25")


def _tokenize_bm25(text: str) -> list[str]:
    """Простий токенайзер для BM25: lowercase + split на слова."""
    return text.lower().split()


def _build_bm25_corpus_sync(client: chromadb.ClientAPI) -> None:
    """Завантажує всі чанки з ChromaDB і будує BM25-індекс (блокуючий).

    Викликається у фоновому потоці, тому не блокує API.
    """
    if not _HAS_BM25:
        return

    BATCH = 5_000

    for col_name in [COLLECTION_GERMAN, COLLECTION_UKRAINIAN]:
        try:
            col   = client.get_collection(col_name)
            total = col.count()
            if total == 0:
                logger.debug("BM25: колекція '%s' порожня, пропускаємо.", col_name)
                continue

            all_texts:  list[str]  = []
            all_metas:  list[dict] = []

            for offset in range(0, total, BATCH):
                res = col.get(
                    limit=BATCH, offset=offset,
                    include=["documents", "metadatas"],
                )
                all_texts.extend(res["documents"])
                all_metas.extend(res["metadatas"])

            tokenized = [_tokenize_bm25(t) for t in all_texts]
            bm25_obj  = BM25Okapi(tokenized)

            corpus = [
                {
                    "text":        t,
                    "source_file": m.get("source_file", ""),
                    "category":    m.get("category", col_name),
                    "chunk_index": m.get("chunk_index", 0),
                    "language":    m.get("language", ""),
                    "distance":    0.5,   # placeholder, буде замінено при пошуку
                }
                for t, m in zip(all_texts, all_metas)
            ]

            with _bm25_lock:
                _bm25_corpus[col_name] = (bm25_obj, corpus)

            logger.info("BM25: '%s' індексовано %d чанків.", col_name, total)

        except Exception as exc:
            logger.warning("BM25: не вдалось побудувати індекс '%s': %s", col_name, exc)

    _bm25_ready.set()
    logger.info("BM25: всі індекси готові.")


def start_bm25_warmup() -> None:
    """Запускає побудову BM25-індексу у фоновому daemon-потоці."""
    if not _HAS_BM25:
        return

    def _worker():
        try:
            client = _get_client()
            _build_bm25_corpus_sync(client)
        except Exception as exc:
            logger.error("BM25 warmup error: %s", exc)
            _bm25_ready.set()   # щоб не блокувати forever

    t = threading.Thread(target=_worker, name="bm25-warmup", daemon=True)
    t.start()
    logger.info("BM25: фоновий warmup запущено.")


def _bm25_search(
    query: str,
    collection_name: str,
    top_k: int,
) -> list[dict]:
    """BM25-пошук у збудованому корпусі. Повертає [] якщо індекс не готовий."""
    if not _HAS_BM25 or not _bm25_ready.is_set():
        return []

    with _bm25_lock:
        if collection_name not in _bm25_corpus:
            return []
        bm25_obj, corpus = _bm25_corpus[collection_name]

    tokenized_query = _tokenize_bm25(query)
    scores          = bm25_obj.get_scores(tokenized_query)

    # Нормалізуємо BM25 score до [0, 1] (відстань: 0 = ідеально)
    max_score = float(scores.max()) if scores.max() > 0 else 1.0
    top_idxs  = scores.argsort()[::-1][:top_k]

    results: list[dict] = []
    for idx in top_idxs:
        if scores[idx] <= 0:
            continue
        item = corpus[idx].copy()
        # distance: 0 = дуже близько, 1 = нічого спільного
        item["distance"]  = max(0.0, 1.0 - float(scores[idx]) / max_score)
        item["bm25_score"] = float(scores[idx])
        results.append(item)

    return results


def _rrf(
    vector_results: list[dict],
    bm25_results:   list[dict],
    k: int = 60,
) -> list[dict]:
    """Об'єднує два списки рейтингів через Reciprocal Rank Fusion.

    RRF score = Σ 1 / (k + rank_i) , де k=60 — стандартне значення.
    Дедуплікація за першими 120 символами тексту.
    """
    scores:  dict[str, float] = {}
    doc_map: dict[str, dict]  = {}

    def _key(item: dict) -> str:
        return item["text"][:120]

    for rank, item in enumerate(vector_results):
        key = _key(item)
        scores[key]  = scores.get(key, 0.0) + 1.0 / (k + rank + 1)
        if key not in doc_map:
            doc_map[key] = item

    for rank, item in enumerate(bm25_results):
        key = _key(item)
        scores[key]  = scores.get(key, 0.0) + 1.0 / (k + rank + 1)
        if key not in doc_map:
            doc_map[key] = item

    sorted_keys = sorted(scores, key=lambda x: scores[x], reverse=True)
    result = []
    for key in sorted_keys:
        item = doc_map[key].copy()
        item["rrf_score"] = round(scores[key], 6)
        result.append(item)
    return result


def _diversify(chunks: list[dict], top_k: int, max_per_source: int = 2) -> list[dict]:
    """Вибирає top_k чанків з обмеженням max_per_source на один файл-джерело.

    Після cross-encoder reranking top-K може містити 5+ чанків з одного документа
    (наприклад sgb_ii.txt), залишаючи поза увагою факти з інших законів.
    Диверсифікація забезпечує покриття більшої кількості джерел → вищий Context Recall.

    Алгоритм: жадібний прохід по відсортованих чанках, пропускаємо джерело
    якщо воно вже представлене max_per_source разів.
    """
    source_count: dict[str, int] = {}
    selected: list[dict] = []

    for chunk in chunks:
        if len(selected) >= top_k:
            break
        source = chunk.get("source_file", "")
        count = source_count.get(source, 0)
        if count < max_per_source:
            selected.append(chunk)
            source_count[source] = count + 1

    # Якщо не вистачило через обмеження — доповнюємо без обмеження
    if len(selected) < top_k:
        seen_texts = {c["text"][:80] for c in selected}
        for chunk in chunks:
            if len(selected) >= top_k:
                break
            if chunk["text"][:80] not in seen_texts:
                selected.append(chunk)
                seen_texts.add(chunk["text"][:80])

    return selected


def _inject_critical_chunks(
    query: str,
    chunks: list[dict],
    critical_files: list[str],
    top_k: int,
    collection_name: str,
    client: chromadb.ClientAPI,
) -> list[dict]:
    """Гарантує присутність критичних файлів-джерел у результаті пошуку.

    Якщо жоден з поточних чанків не походить з critical_files — виконує
    цільовий запит до ChromaDB і замінює останній(-і) слот(и) результату.
    Це забезпечує, що профільно-критичні закони (наприклад AsylbLG для
    asylum_seeker) завжди присутні у відповіді, навіть якщо семантично
    вони ранжуються нижче за більш об'ємні закони (SGB V тощо).
    """
    if not critical_files:
        return chunks

    present = {c.get("source_file", "") for c in chunks}
    missing = [f for f in critical_files if f not in present]

    if not missing:
        return chunks  # всі критичні джерела вже є

    model = _get_embedding_model()
    embedding = model.encode([query])[0].tolist()

    injected: list[dict] = []
    try:
        col = client.get_collection(collection_name)
        for source_file in missing:
            results = col.query(
                query_embeddings=[embedding],
                n_results=1,
                where={"source_file": source_file},
                include=["documents", "metadatas", "distances"],
            )
            if results["documents"] and results["documents"][0]:
                doc  = results["documents"][0][0]
                meta = results["metadatas"][0][0]
                dist = results["distances"][0][0]
                injected.append({
                    "text":             doc,
                    "source_file":      meta.get("source_file", source_file),
                    "category":         meta.get("category", collection_name),
                    "chunk_index":      meta.get("chunk_index", 0),
                    "language":         meta.get("language", ""),
                    "distance":         round(float(dist), 4),
                    "rrf_score":        round(1.0 - float(dist), 4),
                    "profile_boosted":  True,
                    "forced_inclusion": True,
                })
                logger.info(
                    "Forced inclusion: '%s' для колекції '%s' (dist=%.3f)",
                    source_file, collection_name, dist,
                )
    except Exception as exc:
        logger.warning("Forced inclusion failed for '%s': %s", collection_name, exc)
        return chunks

    if not injected:
        return chunks

    # Замінюємо останні N слотів інжектованими чанками
    n = len(injected)
    result = chunks[: max(0, top_k - n)] + injected
    return result


def _apply_profile_boost(
    chunks: list[dict],
    legal_status: str,
    boost: float = _PROFILE_BOOST_DELTA,
) -> list[dict]:
    """Підвищує ранг чанків з файлів, що відповідають правовому статусу.

    Буст застосовується до rerank_score (після cross-encoder) або rrf_score
    (до cross-encoder). Після бусту список пересортовується за відповідним полем.
    """
    preferred = _STATUS_FILE_BOOST.get(legal_status, [])
    if not preferred:
        return chunks

    boosted: list[dict] = []
    for chunk in chunks:
        src = chunk.get("source_file", "").lower()
        item = chunk.copy()
        if any(pref in src for pref in preferred):
            # rerank_score: більший буст (шкала -5..+5 cross-encoder)
            if "rerank_score" in item:
                item["rerank_score"] = item["rerank_score"] + _PROFILE_BOOST_DELTA_RERANK
            # rrf_score: малий буст (шкала 0..1)
            item["rrf_score"] = item.get("rrf_score", 0.0) + boost
            item["profile_boosted"] = True
        boosted.append(item)

    # Сортуємо за rerank_score (пріоритет) або rrf_score
    return sorted(
        boosted,
        key=lambda c: c.get("rerank_score", c.get("rrf_score", 0.0)),
        reverse=True,
    )


def _rerank(query: str, chunks: list[dict]) -> list[dict]:
    """Переранжує чанки cross-encoder моделлю.

    Cross-encoder оцінює кожну пару (запит, чанк) спільно → вища точність
    ніж bi-encoder (окрема векторизація запиту та документа).
    """
    reranker = _get_reranker()
    if reranker is None or not chunks:
        return chunks

    try:
        pairs  = [(query, c["text"]) for c in chunks]
        scores = reranker.predict(pairs)   # ndarray shape (n,)

        ranked = sorted(
            zip(chunks, scores),
            key=lambda x: float(x[1]),
            reverse=True,
        )
        result = []
        for chunk, score in ranked:
            item = chunk.copy()
            item["rerank_score"] = round(float(score), 4)
            result.append(item)
        return result

    except Exception as exc:
        logger.warning("Reranking failed (%s) — повертаємо оригінальний порядок.", exc)
        return chunks


class _QueryCache:
    """In-memory TTL-кеш для результатів пошуку.

    Ключ: SHA256 (query + legal_status + колекції).
    Дедуплікує повторні ідентичні запити протягом TTL-вікна.
    """

    def __init__(self, ttl_seconds: int = 300, max_entries: int = 256) -> None:
        self._store: dict[str, tuple[float, Any]] = {}
        self._ttl   = ttl_seconds
        self._max   = max_entries
        self._lock  = threading.Lock()

    def _make_key(self, query: str, legal_status: str, suffix: str = "") -> str:
        raw = f"{query}|{legal_status}|{suffix}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def get(self, query: str, legal_status: str, suffix: str = "") -> Any | None:
        key = self._make_key(query, legal_status, suffix)
        with self._lock:
            if key in self._store:
                ts, val = self._store[key]
                if time.monotonic() - ts < self._ttl:
                    return val
                del self._store[key]
        return None

    def set(self, query: str, legal_status: str, val: Any, suffix: str = "") -> None:
        key = self._make_key(query, legal_status, suffix)
        with self._lock:
            if len(self._store) >= self._max:
                # Видаляємо найстаріший запис
                oldest = min(self._store, key=lambda k: self._store[k][0])
                del self._store[oldest]
            self._store[key] = (time.monotonic(), val)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()


_query_cache = _QueryCache(ttl_seconds=300, max_entries=256)


def retrieve(
    query: str,
    collection: str,
    top_k: int = 5,
    client: chromadb.ClientAPI | None = None,
) -> list[dict[str, Any]]:
    """Базовий векторний пошук у вказаній колекції ChromaDB.

    Returns:
        Список словників: {text, source_file, category, distance, chunk_index}
    """
    if not query.strip():
        return []

    if client is None:
        client = _get_client()

    model = _get_embedding_model()

    try:
        col = client.get_collection(collection)
    except Exception as exc:
        logger.error("Колекція '%s' не знайдена: %s", collection, exc)
        return []

    count = col.count()
    if count == 0:
        return []

    embedding = model.encode([query])[0].tolist()

    results = col.query(
        query_embeddings=[embedding],
        n_results=min(top_k, count),
        include=["documents", "metadatas", "distances"],
    )

    if not results["documents"] or not results["documents"][0]:
        return []

    output: list[dict] = []
    for doc, meta, dist in zip(
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    ):
        output.append({
            "text":        doc,
            "source_file": meta.get("source_file", ""),
            "category":    meta.get("category", collection),
            "chunk_index": meta.get("chunk_index", 0),
            "language":    meta.get("language", ""),
            "distance":    round(float(dist), 4),
            "rrf_score":   round(1.0 - float(dist), 4),  # для сумісності з RRF
        })

    return output


def _retrieve_hybrid(
    query: str,
    collection_name: str,
    top_k: int,
    client: chromadb.ClientAPI,
) -> list[dict]:
    """Hybrid-пошук у одній колекції: vector + BM25, злитих через RRF."""
    # Беремо більше кандидатів для злиття та подальшого reranking
    # top_k * 4 дає cross-encoder більший пул для вибору → краще Context Precision
    n_candidates = min(top_k * 4, 60)

    vector_res = retrieve(query, collection_name, top_k=n_candidates, client=client)

    bm25_res = _bm25_search(query, collection_name, top_k=n_candidates)

    if bm25_res:
        return _rrf(vector_res, bm25_res)
    else:
        # BM25 ще не готовий або недоступний → чисто векторний результат
        return vector_res


def retrieve_parallel(
    query: str,
    top_k_german: int = 5,
    top_k_ukrainian: int = 2,
) -> tuple[list[dict], list[dict]]:
    """Backward-compatible: паралельний пошук без персоналізації.

    Рекомендується використовувати retrieve_parallel_enhanced() для нових запитів.
    """
    return retrieve_parallel_enhanced(
        query=query,
        legal_status="unknown",
        top_k_german=top_k_german,
        top_k_ukrainian=top_k_ukrainian,
    )


def retrieve_parallel_enhanced(
    query: str,
    legal_status: str = "unknown",
    top_k_german: int = 5,
    top_k_ukrainian: int = 2,
    query_category: str = "other",
) -> tuple[list[dict], list[dict]]:
    """Повний покращений пайплайн пошуку:

    1. Кеш — повертає збережений результат якщо TTL не вийшов
    2. Hybrid retrieval — vector (ChromaDB) + BM25, злитих через RRF
    3. Profile boost — підвищення ранку документів під правовий статус
    4. Cross-encoder reranking — точне переранжування (query, passage) парами
    5. Trim — повертаємо top_k_german + top_k_ukrainian

    Args:
        query:           текст запиту (будь-яка мова)
        legal_status:    статус користувача для profile boost
        top_k_german:    кількість результатів з german_law
        top_k_ukrainian: кількість результатів з ukrainian_context

    Returns:
        (german_chunks, ukrainian_chunks)
    """
    if not query.strip():
        return [], []

    # Якщо клієнт не вказав конкретну категорію — визначаємо автоматично
    query_category = _detect_query_category(query, query_category)

    cached = _query_cache.get(query, legal_status, f"{top_k_german}|{top_k_ukrainian}|{query_category}")
    if cached is not None:
        logger.debug("Cache hit: '%s'", query[:60])
        return cached

    client = _get_client()

    german_raw    = _retrieve_hybrid(query, COLLECTION_GERMAN,    top_k_german    * 3, client)
    ukrainian_raw = _retrieve_hybrid(query, COLLECTION_UKRAINIAN, top_k_ukrainian * 3, client)

    # Reranking відбувається ДО profile boost, щоб буст застосовувався
    # до rerank_score і реально впливав на фінальний порядок
    german_reranked    = _rerank(query, german_raw)
    ukrainian_reranked = _rerank(query, ukrainian_raw)

    german_boosted = _apply_profile_boost(german_reranked, legal_status)

    german_trimmed    = german_boosted[:top_k_german]
    ukrainian_trimmed = ukrainian_reranked[:top_k_ukrainian]

    # Якщо профільно-ключові закони не потрапили до top-k через semantic search,
    # додаємо їх примусово (замінюємо останній слот).
    # Об'єднуємо: закони за статусом (широкі) + закони за категорією (вузькі)
    critical_german = list(dict.fromkeys(
        _STATUS_CRITICAL_GERMAN.get(legal_status, []) +
        _CATEGORY_CRITICAL_GERMAN.get(query_category, [])
    ))
    german = _inject_critical_chunks(
        query, german_trimmed,
        critical_german,
        top_k_german, COLLECTION_GERMAN, client,
    )
    ukrainian = _inject_critical_chunks(
        query, ukrainian_trimmed,
        _STATUS_CRITICAL_UKRAINIAN.get(legal_status, []),
        top_k_ukrainian, COLLECTION_UKRAINIAN, client,
    )

    logger.info(
        "Пошук '%s': %d german, %d ukrainian (bm25=%s rerank=%s boost=%s)",
        query[:60], len(german), len(ukrainian),
        "✓" if _bm25_ready.is_set() else "⏳",
        "✓" if _get_reranker() is not None else "✗",
        legal_status,
    )

    _query_cache.set(query, legal_status, (german, ukrainian),
                     f"{top_k_german}|{top_k_ukrainian}|{query_category}")

    return german, ukrainian
