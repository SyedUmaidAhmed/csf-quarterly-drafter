"""Loading evidence and splitting it into citable blocks.

Two routes to the same result. The **understanding pass** in understand.py asks
a model what the document is and where its parts divide, which is what survives
a format nobody anticipated. The **heuristics** below read markdown structure
directly, and run when there is no model and no cached understanding — in a
test, or the CLI without a key.

The heuristics are kept rather than deleted because they are right about
markdown, which is a grammar rather than a guess. What they are not right about
is everything else: that a date reads "14 May 2026", that a chat line starts
`**Name** · 11:02`, that a heading puts the kind before an em dash. Those are
observations about one pack. The understanding pass exists because the next
pack will not match them.

In production this material arrives through the approved Microsoft 365
connectors. Here it arrives as markdown files in a folder, and the folder is
the entire input surface: nothing downstream knows the filenames, the people
or the subject matter.

The block splitter is the part that matters. Blocks are the granularity Claude
cites at, because we send each document as a custom content document and the
Citations API does no further chunking of its own. Getting the blocks right
means a citation lands on one table row or one chat message rather than half a
sentence of a mangled table.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

from .schema import Block, EvidenceDoc
from .understand import Cache, Understanding, content_hash, slice_segments

# --- Date parsing ------------------------------------------------------------

MONTHS = {
    m.lower(): i
    for i, m in enumerate(
        [
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ],
        start=1,
    )
}

_DAY_MONTH_YEAR = re.compile(
    r"\b(\d{1,2})\s+(" + "|".join(MONTHS) + r")\s+(\d{4})\b", re.IGNORECASE
)
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")


def parse_date(text: str) -> dt.date | None:
    if match := _ISO_DATE.search(text):
        year, month, day = (int(g) for g in match.groups())
        try:
            return dt.date(year, month, day)
        except ValueError:
            return None
    if match := _DAY_MONTH_YEAR.search(text):
        day, month_name, year = match.groups()
        try:
            return dt.date(int(year), MONTHS[month_name.lower()], int(day))
        except ValueError:
            return None
    return None


# --- Block splitting ---------------------------------------------------------

_TABLE_SEPARATOR = re.compile(r"^\|[\s|:-]+\|$")
_BOLD_LABEL = re.compile(r"^\*\*[^*]+:\*\*")
_CHAT_LINE = re.compile(r"^\*\*[^*]+\*\*\s*[·•]\s*\d{1,2}:\d{2}")
_LIST_ITEM = re.compile(r"^\s*[-*+]\s+")
_HORIZONTAL_RULE = re.compile(r"^\s*([-*_])\1{2,}\s*$")


def split_blocks(text: str) -> list[Block]:
    """Split a markdown document into citable blocks.

    One block per table row, chat message, list item, blockquote paragraph,
    bolded metadata line, or ordinary paragraph. Line spans are preserved so
    the review UI can highlight exactly what was cited.
    """
    lines = text.splitlines()
    blocks: list[Block] = []

    def emit(body: str, start: int, end: int, kind: str) -> None:
        body = body.strip()
        if body:
            blocks.append(
                Block(index=len(blocks), text=body, line_start=start, line_end=end, kind=kind)
            )

    for chunk_lines, chunk_start in _chunks(lines):
        first = chunk_lines[0].strip()

        if _HORIZONTAL_RULE.match(first):
            continue

        if first.startswith("#"):
            emit(first.lstrip("#").strip(), chunk_start, chunk_start, "heading")
            continue

        if first.startswith("|"):
            _emit_table(chunk_lines, chunk_start, emit)
            continue

        if first.startswith(">"):
            _emit_blockquote(chunk_lines, chunk_start, emit)
            continue

        if _CHAT_LINE.match(first):
            # Speaker line plus their message: one utterance, one block.
            emit(
                "\n".join(chunk_lines),
                chunk_start,
                chunk_start + len(chunk_lines) - 1,
                "chat_message",
            )
            continue

        if all(_LIST_ITEM.match(line) for line in chunk_lines if line.strip()):
            for offset, line in enumerate(chunk_lines):
                if line.strip():
                    lineno = chunk_start + offset
                    emit(line.strip(), lineno, lineno, "list_item")
            continue

        if all(_BOLD_LABEL.match(line.strip()) for line in chunk_lines if line.strip()):
            # From/To/Cc/Subject and similar: each is a fact of its own.
            for offset, line in enumerate(chunk_lines):
                if line.strip():
                    lineno = chunk_start + offset
                    emit(line.strip(), lineno, lineno, "paragraph")
            continue

        emit(
            "\n".join(chunk_lines),
            chunk_start,
            chunk_start + len(chunk_lines) - 1,
            "paragraph",
        )

    return blocks


def _chunks(lines: list[str]) -> list[tuple[list[str], int]]:
    """Group lines into blank-line-separated chunks, keeping 1-based line numbers."""
    chunks: list[tuple[list[str], int]] = []
    current: list[str] = []
    start = 1
    for lineno, line in enumerate(lines, start=1):
        if line.strip():
            if not current:
                start = lineno
            current.append(line)
        elif current:
            chunks.append((current, start))
            current = []
    if current:
        chunks.append((current, start))
    return chunks


def _emit_table(chunk_lines: list[str], chunk_start: int, emit) -> None:
    """One block per data row.

    In markdown the row immediately above the `|---|` separator is the header,
    so it can be dropped without a guess: column labels are not evidence.
    """
    separator_at = next(
        (i for i, line in enumerate(chunk_lines) if _TABLE_SEPARATOR.match(line.strip())),
        None,
    )
    header_at = separator_at - 1 if separator_at else None

    for offset, line in enumerate(chunk_lines):
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        if offset == header_at or _TABLE_SEPARATOR.match(stripped):
            continue
        lineno = chunk_start + offset
        emit(stripped, lineno, lineno, "table_row")


def _emit_blockquote(chunk_lines: list[str], chunk_start: int, emit) -> None:
    """A blockquote is usually several paragraphs separated by bare '>' lines."""
    paragraph: list[str] = []
    para_start = chunk_start
    for offset, line in enumerate(chunk_lines):
        content = line.strip().lstrip(">").strip()
        if content:
            if not paragraph:
                para_start = chunk_start + offset
            paragraph.append(content)
        elif paragraph:
            emit("\n".join(paragraph), para_start, chunk_start + offset - 1, "quote")
            paragraph = []
    if paragraph:
        emit("\n".join(paragraph), para_start, chunk_start + len(chunk_lines) - 1, "quote")


# --- Field tables ------------------------------------------------------------


def parse_field_table(text: str) -> dict[str, str]:
    """Read a `| Field | Value |` markdown table into a dict.

    Used for the objective record and the prior quarter's update. Generic on
    purpose: a different objective with different fields loads unchanged.
    """
    fields: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") or _TABLE_SEPARATOR.match(stripped):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) < 2:
            continue
        key = cells[0].strip("`* ")
        value = cells[1].strip()
        if not key or key.lower() in ("field", "value"):
            continue
        # Strip markdown emphasis from values so downstream sees plain text.
        value = re.sub(r"\*\*(.+?)\*\*", r"\1", value)
        fields[key] = value
    return fields


# --- Document loading --------------------------------------------------------


def _heading(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def _metadata_line(text: str) -> str:
    """The italic note that often follows the heading and carries the date."""
    for line in text.splitlines()[:6]:
        stripped = line.strip()
        if stripped.startswith("*") and stripped.endswith("*") and not stripped.startswith("**"):
            return stripped.strip("*").strip()
    return ""


_DASHES = re.compile(r"\s+[—–]\s+")


def _source_type(heading: str) -> str:
    """Whatever the document calls itself, before the first dash.

    Derived from the heading rather than a list of known types, so a document
    kind nobody anticipated still gets a sensible label.
    """
    return _DASHES.split(heading, maxsplit=1)[0].strip() if heading else "Document"


def load_document(path: Path, doc_id: str, cache: Cache | None = None) -> EvidenceDoc:
    """Read one document, preferring a cached understanding over the heuristics."""
    text = path.read_text(encoding="utf-8")
    understanding = cache.get(content_hash(text)) if cache else None
    if understanding:
        document = from_understanding(path, doc_id, text, understanding)
        if document:
            return document
    return from_heuristics(path, doc_id, text)


def from_heuristics(path: Path, doc_id: str, text: str) -> EvidenceDoc:
    heading = _heading(text)
    return EvidenceDoc(
        doc_id=doc_id,
        filename=path.name,
        source_path=str(path),
        title=heading or path.stem,
        source_type=_source_type(heading),
        doc_date=parse_date(heading) or parse_date(_metadata_line(text)),
        blocks=split_blocks(text),
    )


def from_understanding(
    path: Path, doc_id: str, text: str, understanding: Understanding
) -> EvidenceDoc | None:
    """Build a document from line ranges the model chose.

    Returns None if nothing usable came back, so the caller can fall back
    rather than proceed with an empty document.
    """
    sliced = slice_segments(text, understanding)
    if not sliced:
        return None

    blocks = [
        Block(index=i, text=body, line_start=start, line_end=end, kind=kind)
        for i, (body, kind, start, end) in enumerate(sliced)
    ]
    return EvidenceDoc(
        doc_id=doc_id,
        filename=path.name,
        source_path=str(path),
        title=understanding.title or path.stem,
        source_type=understanding.source_type or "Document",
        doc_date=_iso_date(understanding.date),
        blocks=blocks,
    )


def _iso_date(value: str | None) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(value.strip())
    except ValueError:
        return None


def load_evidence(evidence_dir: Path, cache: Cache | None = None) -> list[EvidenceDoc]:
    """Load every markdown file in a folder, oldest first.

    Ordering matters: the reconciliation rules turn on which document came
    later. Undated documents sort last, by filename, so the ids are stable
    across runs.
    """
    if not evidence_dir.exists():
        return []

    # An empty folder is an ordinary state, not a failure: evidence can be
    # removed through the interface, and the caller decides whether having
    # none matters. Drafting refuses; showing the workspace does not.
    paths = sorted(evidence_dir.glob("*.md"))
    docs = [load_document(path, doc_id="", cache=cache) for path in paths]
    docs.sort(key=lambda d: (d.doc_date is None, d.doc_date or dt.date.max, d.filename))
    for position, doc in enumerate(docs, start=1):
        doc.doc_id = f"E{position}"
    return docs
