"""Working out what a document is and where its parts begin and end.

This replaces a pile of guesses about how other people write things: that a
date looks like "14 May 2026", that a chat line starts with `**Name** · 11:02`,
that a heading puts the document kind before an em dash. Every one of those is
true of the material I happened to look at and false of something else — a
Slack export, an ISO date, a heading with no dash, a language other than
English. A workflow that has to survive evidence nobody has seen yet cannot
rest on that.

**The model chooses boundaries; it never supplies text.** It returns line
ranges, and the lines are sliced from the file. That distinction is the whole
design: segmentation is judgment and belongs to a model, but the words a
director eventually reads must be the words in the document. If the model
returned block text it could paraphrase, and every citation downstream would
point at a paraphrase while claiming to be a quote.

Understanding is cached by content hash, so a document is read this way once
and not on every page load. Where there is no cache and no model — a test, the
CLI without a key — the heuristics in evidence.py still run. They are a weaker
answer, not a wrong one.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from pydantic import BaseModel, Field, field_validator


class Segment(BaseModel):
    """One citable span, as a line range into the source."""

    start_line: int = Field(ge=1, description="First line, 1-indexed, inclusive.")
    end_line: int = Field(ge=1, description="Last line, 1-indexed, inclusive.")
    kind: str = Field(
        default="paragraph",
        description="What this span is: heading, paragraph, table_row, "
        "chat_message, list_item, quote, or anything else that fits.",
    )

    @field_validator("end_line")
    @classmethod
    def not_backwards(cls, end: int, info) -> int:
        start = info.data.get("start_line")
        return max(end, start) if start else end


class Understanding(BaseModel):
    """What one document is, and how it divides up."""

    title: str = Field(description="The document's own title or subject line.")
    source_type: str = Field(
        description="What kind of thing this is in a few words, as the document "
        "presents itself: Email, Teams chat, Meeting note, Calendar entry, "
        "Report extract. Do not force it into a category that does not fit."
    )
    date: str | None = Field(
        default=None,
        description="The date the document was written, as YYYY-MM-DD. Null if "
        "the document does not say. Do not infer one from dates it mentions.",
    )
    date_reasoning: str = Field(
        default="", description="Briefly, where the date came from or why there is none."
    )
    segments: list[Segment] = Field(
        default_factory=list,
        description="Every part of the document that could be cited separately.",
    )


INSTRUCTION = """\
Below is a document from a director's own material, with line numbers added.

Work out three things.

**What it is.** Its title, and what kind of document it is in a few words, as
it presents itself. Do not force it into a category that does not fit.

**When it was written.** As YYYY-MM-DD. This is the date of the document
itself, not a date it happens to mention: an email written in July that
discusses a meeting in September is dated July. If the document does not say
when it was written, return null rather than guessing — an ordering built on a
guessed date is worse than one that puts the document last.

**How it divides up.** List the spans that could be cited on their own. A
citation should land on one whole thing a person would point at:

- one row of a table, not the whole table
- one message in a chat, speaker line and text together, not the conversation
- one paragraph
- one item in a list
- one paragraph of a quotation
- a heading

Cover the whole document. Skip only decoration — horizontal rules, separators,
blank space. Do not merge two separate statements into one span, and do not
split a sentence across two.

Return line ranges only. The text is taken from the file, so ranges must be
exact: 1-indexed, inclusive at both ends, and within the document.

$numbered
"""


def number_lines(text: str) -> str:
    return "\n".join(f"{n:>4} | {line}" for n, line in enumerate(text.splitlines(), start=1))


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


class Cache:
    """Understanding, stored by content hash.

    Keyed on the content rather than the filename so that editing a document
    re-reads it and renaming one does not.
    """

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def path(self, digest: str) -> Path:
        return self.directory / f"{digest}.json"

    def get(self, digest: str) -> Understanding | None:
        path = self.path(digest)
        if not path.exists():
            return None
        try:
            return Understanding.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:
            # A cache that cannot be read is a cache miss, never an error.
            return None

    def put(self, digest: str, understanding: Understanding) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path(digest).write_text(
            understanding.model_dump_json(indent=2), encoding="utf-8"
        )


def slice_segments(text: str, understanding: Understanding) -> list[tuple[str, str, int, int]]:
    """Turn line ranges into (text, kind, start, end), sliced from the source.

    Out-of-range and empty spans are dropped rather than raising: one bad span
    should cost that span, not the document. If nothing survives, the caller
    falls back to the heuristics.
    """
    lines = text.splitlines()
    sliced: list[tuple[str, str, int, int]] = []

    for segment in understanding.segments:
        start = max(1, segment.start_line)
        end = min(len(lines), segment.end_line)
        if start > end or start > len(lines):
            continue
        body = "\n".join(lines[start - 1 : end]).strip()
        if body:
            sliced.append((body, segment.kind, start, end))

    return sliced


async def understand_document(llm, text: str) -> Understanding:
    """Ask the model what this document is and where it divides."""
    from string import Template

    return await llm.structured(
        system=(
            "You are reading a document so that it can be cited from precisely. "
            "You describe what is there. You never invent a date, never force a "
            "document into a category that does not fit, and never return a line "
            "range that is not in the document."
        ),
        instruction=Template(INSTRUCTION).substitute(numbered=number_lines(text)),
        schema=Understanding,
        fast=True,  # segmentation is mechanical; the judgment calls come later
    )


async def warm(llm, paths, cache: Cache) -> dict[str, Understanding]:
    """Read anything not already understood, and remember it.

    Cheap on a rerun: only documents whose content changed are read again, so
    adding one document to a folder of ten costs one call.
    """
    import asyncio

    unseen: list[tuple[str, str]] = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        digest = content_hash(text)
        if cache.get(digest) is None:
            unseen.append((digest, text))

    if not unseen:
        return {}

    results = await asyncio.gather(
        *(understand_document(llm, text) for _, text in unseen),
        return_exceptions=True,
    )

    understood: dict[str, Understanding] = {}
    for (digest, _), result in zip(unseen, results):
        # One document failing to parse should cost that document its
        # understanding, not the whole run: it falls back to the heuristics.
        if isinstance(result, Understanding):
            cache.put(digest, result)
            understood[digest] = result
    return understood
