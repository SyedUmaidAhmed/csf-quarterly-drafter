"""Citation resolution.

The API guarantees its pointers are valid for the documents it was given. What
these tests cover is our side of that contract: that the blocks we send match
the blocks we resolve back, and that a mismatch is loud rather than silent.
"""

import pytest

from app.citations import build_document_block, extract_cited_statements, resolve_blocks
from app.evidence import load_document


@pytest.fixture
def doc(tmp_path):
    path = tmp_path / "a.md"
    path.write_text(
        "# Teams — 6 August 2026\n\n"
        "**A Person** · 11:02\nFirst utterance.\n\n"
        "**A Person** · 11:03\nSecond utterance.\n",
        encoding="utf-8",
    )
    return load_document(path, "E1")


def citation_response(**overrides):
    citation = {
        "type": "content_block_location",
        "cited_text": "Second utterance.",
        "document_index": 0,
        "document_title": "[E1] Teams",
        "start_block_index": 2,
        "end_block_index": 3,
        **overrides,
    }
    return [
        {"type": "text", "text": "Preamble with no citation."},
        {"type": "text", "text": "A cited statement.", "citations": [citation]},
    ]


def test_document_block_sends_one_content_entry_per_block(doc):
    block = build_document_block(doc)
    assert block["source"]["type"] == "content"
    assert len(block["source"]["content"]) == len(doc.blocks)
    assert block["citations"] == {"enabled": True}
    assert doc.doc_id in block["title"]


def test_document_block_puts_metadata_in_context_not_in_citable_content(doc):
    block = build_document_block(doc)
    assert "2026-08-06" in block["context"]
    assert not any("2026-08-06" in c["text"] for c in block["source"]["content"][1:])


def test_document_block_is_cacheable(doc):
    assert build_document_block(doc, cache=True)["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in build_document_block(doc, cache=False)


def test_uncited_text_is_discarded(doc):
    statements = extract_cited_statements(citation_response(), {0: doc})
    assert len(statements) == 1
    assert statements[0][0] == "A cited statement."


def test_citation_resolves_to_the_right_block(doc):
    _, citations = extract_cited_statements(citation_response(), {0: doc})[0]
    assert citations[0].doc_id == "E1"
    assert citations[0].block_indices == [2]
    assert resolve_blocks(citations[0], doc) == [doc.blocks[2].text]
    assert "Second utterance." in doc.blocks[2].text


def test_multi_block_citation_resolves_to_every_block(doc):
    response = citation_response(start_block_index=1, end_block_index=3)
    _, citations = extract_cited_statements(response, {0: doc})[0]
    assert citations[0].block_indices == [1, 2]
    assert len(resolve_blocks(citations[0], doc)) == 2


def test_out_of_range_block_index_is_an_error(doc):
    response = citation_response(start_block_index=0, end_block_index=99)
    with pytest.raises(IndexError):
        extract_cited_statements(response, {0: doc})


def test_unknown_document_index_is_an_error(doc):
    response = citation_response(document_index=7)
    with pytest.raises(ValueError, match="document index 7"):
        extract_cited_statements(response, {0: doc})


def test_character_location_citations_degrade_rather_than_crash(doc):
    """A plain-text document dropped in the folder should not break a run."""
    response = [
        {
            "type": "text",
            "text": "Cited from plain text.",
            "citations": [
                {
                    "type": "char_location",
                    "cited_text": "something",
                    "document_index": 0,
                    "start_char_index": 0,
                    "end_char_index": 9,
                }
            ],
        }
    ]
    _, citations = extract_cited_statements(response, {0: doc})[0]
    assert citations[0].doc_id == "E1"


def test_string_content_yields_nothing(doc):
    assert extract_cited_statements("just a string", {0: doc}) == []
