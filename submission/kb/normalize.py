"""Boilerplate removal, cleaning and table-safe chunking."""
from __future__ import annotations

import re
from collections import Counter

from contract import Page

from .model import Chunk, chunk_hash

BOILERPLATE_FRACTION = 0.20
MAX_CHUNK_CHARS = 1500

_IMAGE = re.compile(r"!\[[^\]]*\]\((?:[^()]|\([^()]*\))*\)")
_LINK = re.compile(r"\[([^\]]*)\]\((?:[^()]|\([^()]*\))*\)")
_RECAPTCHA = re.compile(r"recaptcha|g-recaptcha", re.I)
_VIN = re.compile(r"(?<![A-Za-z0-9])[A-HJ-NPR-Z0-9]{17}(?![A-Za-z0-9])")
_SEP_ROW = re.compile(r"^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?$")
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def _strip_markup(text: str) -> str:
    return _LINK.sub(r"\1", _IMAGE.sub("", text))


def _key(line: str) -> str:
    return " ".join(_strip_markup(line).split())


def _is_structural(key: str) -> bool:
    """Blank, separator-only, table-separator or heading lines never count as boilerplate."""
    return (not key or not any(ch.isalnum() for ch in key)
            or bool(_SEP_ROW.match(key)) or bool(_HEADING.match(key)))


def fit_boilerplate(pages: list[Page]) -> frozenset[str]:
    """Lines (link/image markup stripped, whitespace collapsed) on >= 20% of one snapshot's pages."""
    if not pages:
        return frozenset()
    counts: Counter[str] = Counter()
    for page in pages:
        counts.update({k for k in map(_key, page.markdown.splitlines()) if not _is_structural(k)})
    need = BOILERPLATE_FRACTION * len(pages)
    return frozenset(k for k, n in counts.items() if n >= need)


def _is_vin_row(line: str) -> bool:
    if not line.lstrip().startswith("|"):
        return False
    return any(any(c.isdigit() for c in m) and any(c.isalpha() for c in m) for m in _VIN.findall(line))


def clean(markdown: str, boilerplate: frozenset[str]) -> str:
    """Drop reCAPTCHA, VIN inventory rows, boilerplate lines, image markup and link targets (keep link text)."""
    lines = []
    for raw in markdown.splitlines():
        if _RECAPTCHA.search(raw) or _is_vin_row(raw):
            continue
        # A link may wrap lines (logo + text); strip per whole text below, boilerplate per line here.
        if _key(raw) in boilerplate:
            continue
        lines.append(raw)
    text = _strip_markup("\n".join(lines))
    out: list[str] = []
    for ln in text.splitlines():
        ln = ln.strip()
        if ln != "\\":
            out.append(ln)
    # Drop table headers whose data rows were all removed.
    final: list[str] = []
    i = 0
    while i < len(out):
        ln = out[i]
        if (ln.startswith("|") and i + 1 < len(out) and _SEP_ROW.match(out[i + 1])
                and not (i + 2 < len(out) and out[i + 2].startswith("|"))):
            i += 2
            continue
        final.append(ln)
        i += 1
    return re.sub(r"\n{3,}", "\n\n", "\n".join(final)).strip()


def _split_text(block: str, limit: int) -> list[str]:
    """Split an oversized prose block at line, sentence, then word boundaries."""
    if len(block) <= limit:
        return [block]
    units: list[str] = []
    for line in block.split("\n"):
        if len(line) <= limit:
            units.append(line)
            continue
        for sent in _SENTENCE_END.split(line):
            if len(sent) <= limit:
                units.append(sent)
                continue
            cur = ""
            for word in sent.split(" "):
                while len(word) > limit:           # unbroken token
                    if cur:
                        units.append(cur)
                        cur = ""
                    units.append(word[:limit])
                    word = word[limit:]
                if len(cur) + len(word) + (1 if cur else 0) > limit:
                    units.append(cur)
                    cur = word
                else:
                    cur = f"{cur} {word}" if cur else word
            if cur:
                units.append(cur)
    parts, cur = [], ""
    for u in units:
        if cur and len(cur) + len(u) + 1 > limit:
            parts.append(cur)
            cur = u
        else:
            cur = f"{cur}\n{u}" if cur else u
    if cur:
        parts.append(cur)
    return parts


def _table_chunks(rows: list[str], limit: int) -> list[str]:
    """Row-aligned pieces; the header row and separator are repeated in every piece."""
    head = rows[:2] if len(rows) >= 2 and _SEP_ROW.match(rows[1]) else []
    body = rows[len(head):]
    head_txt = "\n".join(head)
    pieces, cur = [], []
    size = len(head_txt)
    for r in body:
        if cur and size + 1 + len(r) > limit:
            pieces.append("\n".join(head + cur))
            cur, size = [], len(head_txt)
        cur.append(r)
        size += 1 + len(r)
    if cur or not pieces:
        pieces.append("\n".join(head + cur))
    return pieces


def _blocks(cleaned: str):
    """Yield ('heading', level, text) | ('table', rows) | ('para', text) in document order."""
    para: list[str] = []
    table: list[str] = []

    def flush_para():
        if para:
            yield ("para", "\n".join(para))
            para.clear()

    def flush_table():
        if table:
            yield ("table", list(table))
            table.clear()

    for line in cleaned.splitlines():
        s = line.strip()
        if s.startswith("|"):
            yield from flush_para()
            table.append(s)
            continue
        yield from flush_table()
        m = _HEADING.match(s)
        if m:
            yield from flush_para()
            yield ("heading", len(m.group(1)), m.group(2))
        elif not s:
            yield from flush_para()
        else:
            para.append(line.rstrip())
    yield from flush_para()
    yield from flush_table()


def chunk_page(url: str, title: str, cleaned: str) -> list[Chunk]:
    chunks: list[Chunk] = []
    seen: set[str] = set()
    stack: list[tuple[int, str]] = []
    buf: list[str] = []
    size = 0

    def path() -> str:
        names = [t for _, t in stack]
        if names and title and names[0].casefold() == title.casefold():
            names = names[1:]
        return " > ".join(names)

    def prefix(hp: str) -> str:
        return " > ".join(p for p in (title.strip(), hp) if p)

    def emit(body: str, hp: str) -> None:
        pre = prefix(hp)
        text = f"{pre}\n{body}" if pre else body
        h = chunk_hash(url, text)
        if h not in seen:
            seen.add(h)
            chunks.append(Chunk(h, url, title, hp, text))

    def flush() -> None:
        nonlocal size
        if buf:
            emit("\n\n".join(buf), path())
        buf.clear()
        size = 0

    # body budget leaves room for the joining newline so len(text) <= MAX + len(prefix)
    limit = MAX_CHUNK_CHARS - 1
    for block in _blocks(cleaned):
        kind = block[0]
        if kind == "heading":
            flush()
            level = block[1]
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, block[2]))
        elif kind == "table":
            flush()
            for piece in _table_chunks(block[1], limit):
                emit(piece, path())
        else:
            for part in _split_text(block[1], limit):
                if buf and size + 2 + len(part) > limit:
                    flush()
                buf.append(part)
                size += len(part) + (2 if len(buf) > 1 else 0)
    flush()
    return chunks
