from __future__ import annotations

import array
import functools
import json
import urllib.error
import urllib.request
from collections.abc import Sequence

from ..config import Config
from ..db import Db
from ..errors import EmbeddingError
from ..models import Rule, ScoredResult
from ..utils.logging import get_logger
from ..utils.time import now_iso

log = get_logger("nokori.search.embedding")


def _serialize(vec: Sequence[float]) -> bytes:
    return array.array("f", vec).tobytes()


def _deserialize(blob: bytes) -> list[float]:
    arr = array.array("f")
    arr.frombytes(blob)
    return list(arr)


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _chunk_text(text: str, chunk_size: int, chunk_count: int) -> list[str]:
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text) and len(chunks) < chunk_count:
        end = min(start + chunk_size, len(text))
        if end < len(text):
            for sep in ("\n", ". ", " "):
                pos = text.rfind(sep, start, end)
                if pos > start + chunk_size // 2:
                    end = pos + len(sep)
                    break
        chunks.append(text[start:end])
        start = end
    return chunks


def _rule_text(rule: Rule) -> str:
    parts = [rule.trigger_text, rule.action]
    if rule.rationale:
        parts.append(rule.rationale)
    parts.extend(rule.trigger_variants)
    for items in rule.search_terms.values():
        parts.extend(items)
    return "\n".join(p for p in parts if p)


class EmbeddingClient:
    def __init__(self, cfg: Config, *, http_open=None):
        self.cfg = cfg
        self._open = http_open or urllib.request.urlopen

    def configured(self) -> bool:
        return bool(self.cfg.embed_base_url and self.cfg.embed_model)

    def embed(self, text: str, *, timeout: int = 10) -> list[list[float]]:
        if not self.configured():
            raise EmbeddingError("embedding not configured")
        chunks = _chunk_text(
            text,
            chunk_size=self.cfg.embed_chunk_size,
            chunk_count=self.cfg.embed_chunk_count,
        )
        vectors: list[list[float]] = []
        for chunk in chunks:
            vectors.append(self._embed_one(chunk, timeout))
        return vectors

    def _embed_one(self, text: str, timeout: int) -> list[float]:
        payload: dict = {"model": self.cfg.embed_model, "input": text}
        if self.cfg.embed_dimensions and self.cfg.embed_dimensions > 0:
            payload["dimensions"] = self.cfg.embed_dimensions
        headers = {"Content-Type": "application/json"}
        if self.cfg.embed_api_key:
            headers["Authorization"] = f"Bearer {self.cfg.embed_api_key}"
        url = f"{self.cfg.embed_base_url.rstrip('/')}/embeddings"
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with self._open(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            raise EmbeddingError(f"HTTP {e.code} on {url}") from e
        except (urllib.error.URLError, TimeoutError) as e:
            raise EmbeddingError(str(e)) from e
        try:
            data = json.loads(body)
            return list(data["data"][0]["embedding"])
        except (KeyError, IndexError, ValueError, json.JSONDecodeError) as e:
            raise EmbeddingError(f"bad response: {e}") from e


def store_rule_embedding(db: Db, rule: Rule, client: EmbeddingClient) -> int:
    if not client.configured():
        return 0
    try:
        vectors = client.embed(_rule_text(rule))
    except EmbeddingError as e:
        log.warning("embed store failed rule=%s err=%s", rule.id, e)
        return 0
    return _store_impl(db, rule.id, vectors, client.cfg.embed_model)


def _store_impl(db: Db, rule_id: str, vectors: list[list[float]], model_name: str) -> int:
    if not vectors:
        return 0
    now = now_iso()
    with db.transaction() as tx:
        tx.execute("DELETE FROM rule_embeddings WHERE rule_id = ?", (rule_id,))
        for idx, vec in enumerate(vectors):
            tx.execute(
                "INSERT INTO rule_embeddings (rule_id, chunk_index, embedding, "
                "model_version, created_at) VALUES (?,?,?,?,?)",
                (rule_id, idx, _serialize(vec), model_name, now),
            )
    return len(vectors)


def search(
    query: str,
    rules: Sequence[Rule],
    db: Db,
    client: EmbeddingClient,
    *,
    top_k: int = 10,
    timeout: int = 10,
) -> list[ScoredResult]:
    if not client.configured() or not rules:
        return []
    try:
        qvecs = client.embed(query, timeout=timeout)
    except EmbeddingError:
        return []
    if not qvecs:
        return []
    return _search_impl(qvecs[0], rules, db, top_k)


def _search_impl(
    qvec: list[float],
    rules: Sequence[Rule],
    db: Db,
    top_k: int,
) -> list[ScoredResult]:
    if not rules:
        return []
    placeholders = ",".join(["?"] * len(rules))
    rows = db.fetchall(
        f"SELECT rule_id, chunk_index, embedding FROM rule_embeddings "
        f"WHERE rule_id IN ({placeholders})",
        tuple(r.id for r in rules),
    )
    by_rule: dict[str, list[list[float]]] = {}
    for row in rows:
        by_rule.setdefault(row["rule_id"], []).append(_deserialize(row["embedding"]))

    results: list[ScoredResult] = []
    for rule in rules:
        embeddings = by_rule.get(rule.id) or []
        if not embeddings:
            continue
        best = max(_cosine(qvec, emb) for emb in embeddings)
        results.append(ScoredResult(rule=rule, cosine=best))
    results.sort(key=lambda r: r.cosine or 0.0, reverse=True)
    return results[:top_k]


LOCAL_MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"
LOCAL_DIMENSIONS = 384


@functools.lru_cache(maxsize=1)
def _sentence_transformers_available() -> bool:
    try:
        import sentence_transformers  # noqa: F401
        return True
    except ImportError:
        return False


class LocalEmbeddingClient:
    """Uses sentence-transformers for local embedding when no remote endpoint is configured."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._model = None
        self._model_name = LOCAL_MODEL_NAME
        self._cache_dir = str(cfg.data_dir / "models")

    def available(self) -> bool:
        return _sentence_transformers_available()

    def _load_model(self):
        if self._model is not None:
            return self._model
        from sentence_transformers import SentenceTransformer
        self._model = SentenceTransformer(
            self._model_name, cache_folder=self._cache_dir
        )
        return self._model

    def embed(self, text: str) -> list[list[float]]:
        chunks = _chunk_text(
            text,
            chunk_size=self.cfg.embed_chunk_size,
            chunk_count=self.cfg.embed_chunk_count,
        )
        if not chunks:
            return []
        model = self._load_model()
        vectors = model.encode(chunks, show_progress_bar=False)
        return [v.tolist() for v in vectors]


def store_rule_embedding_local(db: Db, rule: Rule, client: LocalEmbeddingClient) -> int:
    if not client.available():
        return 0
    try:
        vectors = client.embed(_rule_text(rule))
    except Exception as e:
        log.warning("local embed store failed rule=%s err=%s", rule.id, e)
        return 0
    return _store_impl(db, rule.id, vectors, LOCAL_MODEL_NAME)


def search_local_shared(
    query: str,
    rules: Sequence[Rule],
    db: Db,
    cfg: Config,
    *,
    top_k: int = 10,
    timeout: float = 5.0,
    interaction: str = "cli",
) -> tuple[list[ScoredResult], str]:
    """Local embed via shared embed server. Hook path never falls back to in-process."""
    from . import embed_ipc

    if not rules or not _sentence_transformers_available():
        return [], "off"

    max_wait = embed_ipc._STARTUP_WAIT_SECONDS if interaction == "hook" else 15.0
    if not embed_ipc.ensure_running(cfg, max_wait=max_wait):
        return [], "off"

    qvecs = embed_ipc.embed_text(cfg, query, timeout=timeout)
    if not qvecs:
        return [], "off"

    return _search_impl(qvecs[0], rules, db, top_k), "local"


def auto_enabled(cfg: Config, rule_count: int) -> bool:
    # Remote embedding configured explicitly
    if cfg.embed_enabled:
        if cfg.embed_base_url and cfg.embed_model:
            return True
        # Explicit enable but no remote — check local availability
        return _sentence_transformers_available()
    # Auto-enable threshold: rules >= 20
    if rule_count < 20:
        return False
    if cfg.embed_base_url and cfg.embed_model:
        return True
    return _sentence_transformers_available()


def use_local(cfg: Config) -> bool:
    """True when local embedding should be used (no remote configured, local available)."""
    if cfg.embed_base_url and cfg.embed_model:
        return False
    return _sentence_transformers_available()


def index_rule_if_enabled(db: Db, rule: Rule, cfg: Config) -> None:
    """Index a rule's embedding if embedding is enabled. Best-effort, logs on failure."""
    try:
        from ..db import total_rule_count

        if not auto_enabled(cfg, total_rule_count(db)):
            return
        if use_local(cfg):
            from . import embed_ipc

            text = _rule_text(rule)
            if not embed_ipc.ensure_running(cfg, max_wait=15.0):
                return
            vectors = embed_ipc.embed_text(cfg, text, timeout=60.0)
            if vectors:
                _store_impl(db, rule.id, vectors, LOCAL_MODEL_NAME)
        else:
            store_rule_embedding(db, rule, EmbeddingClient(cfg))
    except Exception:
        log.warning("embed index failed rule=%s", rule.id, exc_info=True)
