from __future__ import annotations

import array
import json
import struct
import urllib.error
import urllib.request
from collections.abc import Sequence

from ..config import Config
from ..db import Db
from ..errors import EmbeddingError
from ..models import Rule, ScoredResult
from ..utils.logging import get_logger

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
        if self.cfg.embed_dimensions:
            payload["dimensions"] = self.cfg.embed_dimensions
        headers = {"Content-Type": "application/json"}
        if self.cfg.embed_api_key:
            headers["Authorization"] = f"Bearer {self.cfg.embed_api_key}"
        url = f"{self.cfg.embed_base_url.rstrip('/')}/v1/embeddings"
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
    if not vectors:
        return 0
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    with db.transaction() as tx:
        tx.execute("DELETE FROM rule_embeddings WHERE rule_id = ?", (rule.id,))
        for idx, vec in enumerate(vectors):
            tx.execute(
                "INSERT INTO rule_embeddings (rule_id, chunk_index, embedding, "
                "model_version, created_at) VALUES (?,?,?,?,?)",
                (rule.id, idx, _serialize(vec), client.cfg.embed_model, now),
            )
    return len(vectors)


def search(
    query: str,
    rules: Sequence[Rule],
    db: Db,
    client: EmbeddingClient,
    *,
    top_k: int = 10,
) -> list[ScoredResult]:
    if not client.configured() or not rules:
        return []
    try:
        qvecs = client.embed(query)
    except EmbeddingError:
        return []
    if not qvecs:
        return []
    qvec = qvecs[0]

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


def auto_enabled(cfg: Config, rule_count: int) -> bool:
    if cfg.embed_enabled:
        return cfg.embed_base_url is not None and cfg.embed_model is not None
    return rule_count >= 20 and bool(cfg.embed_base_url and cfg.embed_model)
