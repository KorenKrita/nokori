from __future__ import annotations

from ..config import Config
from ..db import open_db
from ..errors import DbError
from ..gate import prompt_ack
from ..posthoc import enqueue_posthoc_for_session
from ..utils import sessions
from ..utils.host import Host, effective_session_id
from ..utils.logging import get_logger

log = get_logger("nokori.hooks.session_end")


def handle(payload: dict, cfg: Config, *, host: Host) -> dict:
    if cfg.disabled:
        return {"continue": True}

    session_id = effective_session_id(payload)
    sessions.end(cfg, session_id)
    ack_removed = prompt_ack.cleanup_session(cfg, session_id)
    if ack_removed:
        log.info("cleaned prompt ack/deferred session=%s files=%d", session_id, ack_removed)

    # Enqueue posthoc evaluation jobs (cold pipeline picks these up asynchronously)
    try:
        db = open_db(cfg.db_path)
    except DbError as e:
        log.warning("posthoc enqueue db open failed session=%s: %s", session_id, e)
        return {"continue": True}
    try:
        enqueue_posthoc_for_session(db, session_id)
        log.info("enqueued posthoc jobs session=%s", session_id)
    except Exception as e:
        log.warning("posthoc enqueue failed session=%s: %s", session_id, e)
    finally:
        db.close()

    return {"continue": True}
