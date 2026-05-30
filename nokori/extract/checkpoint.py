"""Per-transcript merge checkpoints so partial extract retries stay idempotent."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from ..config import Config
from ..db import dumps_json
from .extractor import Candidate


def transcript_key(path: Path) -> str:
    return str(path.expanduser().resolve())


def _legacy_candidate_key(cand: Candidate) -> str:
    payload = (
        f"{cand.trigger}\n{cand.action}\n{cand.source_type}\n"
        f"{cand.confidence}\n{cand.behavior or ''}"
    )
    return hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()[:32]


def candidate_key(cand: Candidate) -> str:
    payload = (
        f"{cand.trigger}\n{cand.action}\n{cand.source_type}\n"
        f"{cand.confidence}\n{cand.behavior or ''}\n"
        f"{dumps_json(cand.trigger_variants)}\n{dumps_json(cand.search_terms)}"
    )
    return hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()[:32]


def candidate_keys(cand: Candidate) -> frozenset[str]:
    return frozenset({candidate_key(cand), _legacy_candidate_key(cand)})


def is_candidate_merged(cfg: Config, transcript: Path, cand: Candidate) -> bool:
    merged = load_merged_keys(cfg, transcript)
    return bool(candidate_keys(cand) & merged)


def record_candidate_merged(cfg: Config, transcript: Path, cand: Candidate) -> None:
    merged = load_merged_keys(cfg, transcript)
    merged |= set(candidate_keys(cand))
    path = _checkpoint_file(cfg, transcript)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"merged": sorted(merged)}), encoding="utf-8")
    os.replace(tmp, path)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _checkpoint_file(cfg: Config, transcript: Path) -> Path:
    digest = hashlib.sha256(transcript_key(transcript).encode()).hexdigest()[:16]
    directory = cfg.data_dir / "extract_checkpoints"
    directory.mkdir(parents=True, exist_ok=True)
    try:
        directory.chmod(0o700)
    except OSError:
        pass
    return directory / f"{digest}.json"


def load_merged_keys(cfg: Config, transcript: Path) -> set[str]:
    path = _checkpoint_file(cfg, transcript)
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    merged = data.get("merged")
    if not isinstance(merged, list):
        return set()
    return {str(x) for x in merged}


def clear(cfg: Config, transcript: Path) -> None:
    path = _checkpoint_file(cfg, transcript)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
