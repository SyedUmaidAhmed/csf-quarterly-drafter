"""Talking to the Citations API, and resolving what it says back to our blocks.

Two things are worth understanding here.

**Why the API's citations rather than asking the model to quote.** With
`citations: {enabled: true}` the API returns pointers it extracted from the
source, so a citation cannot refer to a sentence that was never written. Asking
a model to quote and then checking the quote only catches fabrication after the
fact; this makes it impossible. `cited_text` also does not count toward output
tokens.

**Why custom content documents.** `source.type = "content"` takes our block
list as-is and does no further chunking, so citations come back as block index
ranges that map one-to-one onto what we sent. The alternative, plain text, gets
auto-chunked into sentences — which would cut `| Field | Value |` rows and
`**Name** · 11:03` chat lines in half.

One consequence, handled in graph/nodes.py rather than here: citations cannot
be combined with structured outputs. The API rejects that combination outright,
which is why reading and structuring are separate passes.
"""

from __future__ import annotations

import json
from typing import Any

from .schema import Citation, EvidenceDoc


def build_document_block(doc: EvidenceDoc, cache: bool = True) -> dict[str, Any]:
    """One Anthropic document content block for an evidence document.

    `title` and `context` are passed to the model but are not citable, so
    metadata goes in `context` where it cannot be mistaken for evidence.
    """
    block: dict[str, Any] = {
        "type": "document",
        "source": {
            "type": "content",
            "content": [{"type": "text", "text": b.text} for b in doc.blocks],
        },
        "title": f"[{doc.doc_id}] {doc.title}",
        "context": json.dumps(
            {
                "doc_id": doc.doc_id,
                "source_type": doc.source_type,
                "date": doc.date_label,
                "filename": doc.filename,
            }
        ),
        "citations": {"enabled": True},
    }
    if cache:
        # The same documents are read on every run and the evidence is the
        # bulk of the prompt, so caching it is close to free money.
        block["cache_control"] = {"type": "ephemeral"}
    return block


def extract_cited_statements(
    content: Any, docs_by_index: dict[int, EvidenceDoc]
) -> list[tuple[str, list[Citation]]]:
    """Pull (statement, citations) pairs out of a response.

    The API interleaves plain text with cited text blocks. We keep only the
    blocks that carry citations: an uncited sentence in a reading pass is
    commentary, and commentary is not what this pass is for.
    """
    if isinstance(content, str):
        return []

    statements: list[tuple[str, list[Citation]]] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        raw_citations = block.get("citations") or []
        if not raw_citations:
            continue
        citations = [_to_citation(c, docs_by_index) for c in raw_citations]
        text = (block.get("text") or "").strip()
        if text:
            statements.append((text, citations))
    return statements


def _to_citation(raw: dict[str, Any], docs_by_index: dict[int, EvidenceDoc]) -> Citation:
    doc_index = raw.get("document_index")
    doc = docs_by_index.get(doc_index)
    if doc is None:
        raise ValueError(
            f"citation refers to document index {doc_index}, "
            f"but only {sorted(docs_by_index)} were sent"
        )

    start, end = _block_range(raw)

    # Resolving every index proves our own bookkeeping is right. The API
    # guarantees the pointer is valid for what it was given; this checks that
    # what it was given is what we think we sent.
    for index in range(start, end):
        doc.block(index)

    return Citation(
        doc_id=doc.doc_id,
        start_block=start,
        end_block=end,
        cited_text=raw.get("cited_text", ""),
    )


def _block_range(raw: dict[str, Any]) -> tuple[int, int]:
    """Block indices for a custom content citation.

    Character and page locations are accepted too, so that a plain-text or PDF
    document dropped into the evidence folder degrades to a whole-document
    citation instead of crashing the run.
    """
    if "start_block_index" in raw:
        return int(raw["start_block_index"]), int(raw["end_block_index"])
    if "start_content_block_index" in raw:
        return int(raw["start_content_block_index"]), int(raw["end_content_block_index"])
    return 0, 0


def resolve_blocks(citation: Citation, doc: EvidenceDoc) -> list[str]:
    """The source text a citation points at, for display."""
    return [doc.block(i).text for i in citation.block_indices]
