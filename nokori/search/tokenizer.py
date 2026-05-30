from __future__ import annotations

import unicodedata


def is_cjk(ch: str) -> bool:
    cp = ord(ch)
    return (
        0x3000 <= cp <= 0x303F
        or 0x3040 <= cp <= 0x309F
        or 0x30A0 <= cp <= 0x30FF
        or 0x3400 <= cp <= 0x4DBF
        or 0x4E00 <= cp <= 0x9FFF
        or 0xF900 <= cp <= 0xFAFF
        or 0x20000 <= cp <= 0x2EBEF
        or 0xAC00 <= cp <= 0xD7AF
    )


def _flush_latin(buf: str, out: list[str]) -> None:
    if not buf:
        return
    for word in buf.split():
        w = word.strip("_-")
        if len(w) >= 2:
            out.append(w)


def _flush_cjk(buf: str, out: list[str]) -> None:
    # Single CJK char → unigram (recall over strict bigram-only spec).
    if len(buf) == 1:
        out.append(buf)
        return
    for i in range(len(buf) - 1):
        out.append(buf[i : i + 2])


def tokenize(text: str) -> list[str]:
    """Latin words (lowercase, len ≥ 2) + CJK char bigrams.

    Mixed text alternates between Latin and CJK regions; transitions flush
    each buffer.
    """
    text = unicodedata.normalize("NFKC", text or "").lower()
    out: list[str] = []
    latin: list[str] = []
    cjk: list[str] = []

    for ch in text:
        if is_cjk(ch):
            if latin:
                _flush_latin("".join(latin), out)
                latin.clear()
            cjk.append(ch)
        elif ch.isalnum() or ch == "_":
            if cjk:
                _flush_cjk("".join(cjk), out)
                cjk.clear()
            latin.append(ch)
        else:
            if latin:
                _flush_latin("".join(latin), out)
                latin.clear()
            if cjk:
                _flush_cjk("".join(cjk), out)
                cjk.clear()

    if latin:
        _flush_latin("".join(latin), out)
    if cjk:
        _flush_cjk("".join(cjk), out)

    return out
