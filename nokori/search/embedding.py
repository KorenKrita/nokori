from __future__ import annotations

import array
import functools
import importlib.util
import json
import urllib.error
import urllib.request
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

EmbedKind = Literal["query", "document"]

from ..config import Config
from ..db import Db
from ..errors import EmbeddingError
from ..models import Rule, ScoredResult
from ..utils.logging import get_logger
from ..utils.sql_batch import batched
from ..utils.time import now_iso

log = get_logger("nokori.search.embedding")


def _serialize(vec: Sequence[float]) -> bytes:
    return array.array("f", vec).tobytes()


def _deserialize(blob: bytes) -> list[float]:
    arr = array.array("f")
    try:
        arr.frombytes(blob)
    except ValueError as e:
        raise ValueError(f"invalid embedding blob length {len(blob)}") from e
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


def _cosine_with_norm(a_norm: float, a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity when caller already knows ||a||."""
    if a_norm == 0 or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    nb = sum(y * y for y in b) ** 0.5
    if nb == 0:
        return 0.0
    return dot / (a_norm * nb)


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
    model = client.cfg.embed_model or ""
    return _search_impl(qvecs[0], rules, db, top_k, model)


def _search_impl(
    qvec: list[float],
    rules: Sequence[Rule],
    db: Db,
    top_k: int,
    model_version: str,
) -> list[ScoredResult]:
    if not rules or not model_version:
        return []
    rule_ids = [r.id for r in rules]
    rows: list = []
    for batch in batched(rule_ids):
        placeholders = ",".join(["?"] * len(batch))
        rows.extend(
            db.fetchall(
                f"SELECT rule_id, chunk_index, embedding FROM rule_embeddings "
                f"WHERE rule_id IN ({placeholders}) AND model_version = ?",
                (*batch, model_version),
            )
        )
    by_rule: dict[str, list[list[float]]] = {}
    for row in rows:
        try:
            vec = _deserialize(row["embedding"])
        except ValueError:
            log.warning("skipping corrupt embedding for rule %s", row["rule_id"])
            continue
        by_rule.setdefault(row["rule_id"], []).append(vec)

    qnorm = sum(x * x for x in qvec) ** 0.5
    results: list[ScoredResult] = []
    for rule in rules:
        embeddings = by_rule.get(rule.id) or []
        if not embeddings:
            continue
        best = max(_cosine_with_norm(qnorm, qvec, emb) for emb in embeddings)
        results.append(ScoredResult(rule=rule, cosine=best))
    results.sort(key=lambda r: r.cosine or 0.0, reverse=True)
    return results[:top_k]


# IBM Granite R2: 97M params, 384-dim, bi-encoder retrieval (query vs document).
# Requires sentence-transformers>=3.0 (encode_query / encode_document).
LOCAL_MODEL_HF_ID = "ibm-granite/granite-embedding-97m-multilingual-r2"
LOCAL_MODEL_NAME = LOCAL_MODEL_HF_ID
LOCAL_DIMENSIONS = 384

# Local Granite path: one vector per rule/query unless user sets [embed] chunk_* / env.
# 24576 chars covers schema max (trigger 16KiB + action 8KiB + metadata).
LOCAL_EMBED_CHUNK_SIZE = 24_576
LOCAL_EMBED_CHUNK_COUNT = 1

_LOCAL_WEIGHT_NAMES = ("model.safetensors", "pytorch_model.bin", "onnx/model.onnx")


def embed_chunk_params(cfg: Config, *, local: bool) -> tuple[int, int]:
    """Chunk size/count for embedding.

    Remote: always ``cfg`` values (defaults 4000×2).
    Local: per-field — unset项用 Granite 默认 (24576×1)；只配一项时另一项仍走本地默认。
    """
    if not local:
        return cfg.embed_chunk_size, cfg.embed_chunk_count
    size = (
        cfg.embed_chunk_size
        if cfg.embed_chunk_size_configured
        else LOCAL_EMBED_CHUNK_SIZE
    )
    count = (
        cfg.embed_chunk_count
        if cfg.embed_chunk_count_configured
        else LOCAL_EMBED_CHUNK_COUNT
    )
    return size, count



def local_model_hub_dir(model_id: str = LOCAL_MODEL_HF_ID) -> str:
    """HuggingFace hub cache folder name under ``data_dir/models/``."""
    if "/" in model_id:
        return "models--" + model_id.replace("/", "--")
    return f"models--sentence-transformers--{model_id}"


def remote_embed_configured(cfg: Config) -> bool:
    return bool(cfg.embed_base_url and cfg.embed_model)


def local_embed_package_available() -> bool:
    """True when sentence-transformers is installed (no import of the package)."""
    return importlib.util.find_spec("sentence_transformers") is not None


def local_model_cache_dir(cfg: Config) -> Path:
    return Path(cfg.data_dir) / "models"


def local_model_cached(cfg: Config) -> bool:
    """True when HuggingFace cache under data_dir/models has loadable weights."""
    cache = local_model_cache_dir(cfg)
    hub = cache / local_model_hub_dir(LOCAL_MODEL_HF_ID)
    snapshots = hub / "snapshots"
    if not snapshots.is_dir():
        return False
    for snap in snapshots.iterdir():
        if not snap.is_dir():
            continue
        if any((snap / name).is_file() for name in _LOCAL_WEIGHT_NAMES):
            return True
    return False


def local_embed_capable(cfg: Config) -> bool:
    """Local embed can run: package installed and/or weights already on disk."""
    return local_embed_package_available() or local_model_cached(cfg)


def embedding_active(cfg: Config, rule_count: int) -> bool:
    """Whether retrieval should use embedding at all (no sentence-transformers import)."""
    if cfg.embed_enabled:
        if remote_embed_configured(cfg):
            return True
        return local_embed_capable(cfg)
    if rule_count < 20:
        return False
    if remote_embed_configured(cfg):
        return True
    return local_embed_capable(cfg)


def use_local_config(cfg: Config) -> bool:
    """True when configured to use the local embed server (not remote HTTP API)."""
    return not remote_embed_configured(cfg)


def prefetch_local_model(cfg: Config) -> str:
    """Download/load local model weights into data_dir/models. Requires local-embed extra."""
    if not local_embed_package_available():
        raise EmbeddingError(
            "sentence-transformers>=3.0 not installed; use: pip install -e '.[local-embed]'"
        )
    cfg.ensure_dirs()
    client = LocalEmbeddingClient(cfg)
    client.load_model()
    return str(local_model_cache_dir(cfg))


@functools.lru_cache(maxsize=1)
def _sentence_transformers_available() -> bool:
    try:
        import sentence_transformers  # noqa: F401
        return True
    except ImportError:
        return False


def _encode_chunks(model, chunks: list[str], *, kind: EmbedKind) -> list[list[float]]:
    """Encode with Granite R2 retrieval API when available (encode_query / encode_document)."""
    kwargs: dict = {"show_progress_bar": False, "convert_to_numpy": True}
    if kind == "query" and hasattr(model, "encode_query"):
        raw = model.encode_query(chunks, **kwargs)
    elif kind == "document" and hasattr(model, "encode_document"):
        raw = model.encode_document(chunks, **kwargs)
    else:
        raw = model.encode(chunks, normalize_embeddings=True, **kwargs)
    if getattr(raw, "ndim", 0) == 1:
        return [raw.tolist()]
    return [v.tolist() for v in raw]


class LocalEmbeddingClient:
    """Loads sentence-transformers inside the embed server process only (not in hooks)."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._model = None
        self._model_name = LOCAL_MODEL_HF_ID
        self._cache_dir = str(cfg.data_dir / "models")

    def available(self) -> bool:
        return _sentence_transformers_available()

    def load_model(self):
        if self._model is not None:
            return self._model
        from sentence_transformers import SentenceTransformer
        self._model = SentenceTransformer(
            self._model_name, cache_folder=self._cache_dir
        )
        return self._model

    def embed(self, text: str, *, kind: EmbedKind = "document") -> list[list[float]]:
        chunk_size, chunk_count = embed_chunk_params(self.cfg, local=True)
        chunks = _chunk_text(text, chunk_size=chunk_size, chunk_count=chunk_count)
        if not chunks:
            return []
        model = self.load_model()
        return _encode_chunks(model, chunks, kind=kind)


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

    if not rules:
        return [], "off"

    if interaction == "hook":
        if not local_model_cached(cfg):
            log.info(
                "embed skipped on hook (local weights missing; run `nokori embed prefetch`)"
            )
            return [], "off"
        if not local_embed_package_available():
            log.info(
                "embed skipped on hook (install `pip install -e \".[local-embed]\"`)"
            )
            return [], "off"
        if not embed_ipc.kickstart_server(cfg):
            log.info("embed skipped on hook (server not ready; BM25-only this turn)")
            return [], "off"
    elif not _sentence_transformers_available():
        return [], "off"
    elif not embed_ipc.ensure_running(cfg, max_wait=15.0):
        return [], "off"

    qvecs = embed_ipc.embed_text(
        cfg, query, timeout=timeout, auto_start=False, kind="query"
    )
    if not qvecs:
        return [], "off"

    return _search_impl(qvecs[0], rules, db, top_k, LOCAL_MODEL_NAME), "local"


def auto_enabled(cfg: Config, rule_count: int) -> bool:
    return embedding_active(cfg, rule_count)


def use_local(cfg: Config) -> bool:
    """True when local embed server path applies and runtime can use it."""
    if remote_embed_configured(cfg):
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
            vectors = embed_ipc.embed_text(
                cfg, text, timeout=60.0, auto_start=False, kind="document"
            )
            if vectors:
                _store_impl(db, rule.id, vectors, LOCAL_MODEL_NAME)
        else:
            store_rule_embedding(db, rule, EmbeddingClient(cfg))
    except Exception:
        log.warning("embed index failed rule=%s", rule.id, exc_info=True)
