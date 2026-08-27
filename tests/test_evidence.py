"""The loader must work on an arbitrary folder of markdown, not on this pack.

Everything here builds its own fixtures rather than asserting against the
supplied evidence, because a loader that only works on five known files is not
a loader.
"""

import datetime as dt

from app.evidence import (
    load_document,
    load_evidence,
    parse_date,
    parse_field_table,
    split_blocks,
)


def write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


# --- dates -------------------------------------------------------------------


def test_parse_date_handles_prose_and_iso():
    assert parse_date("Email — 14 May 2026, 09:41 EAT") == dt.date(2026, 5, 14)
    assert parse_date("Microsoft Teams — 6 August 2026") == dt.date(2026, 8, 6)
    assert parse_date("Submitted 2026-07-08") == dt.date(2026, 7, 8)
    assert parse_date("no date at all here") is None
    assert parse_date("31 February 2026") is None


# --- block splitting ---------------------------------------------------------


def test_table_rows_split_one_per_row_and_drop_the_header():
    blocks = split_blocks("| Field | Value |\n|---|---|\n| Status | Cancelled |\n| Owner | Someone |")
    assert [b.kind for b in blocks] == ["table_row", "table_row"]
    assert blocks[0].text == "| Status | Cancelled |"
    assert not any("Field" in b.text for b in blocks)


def test_chat_messages_stay_whole():
    blocks = split_blocks(
        "**A Person** · 11:02\nFirst message.\n\n**A Person** · 11:03\nSecond message."
    )
    assert [b.kind for b in blocks] == ["chat_message", "chat_message"]
    assert "First message." in blocks[0].text
    assert "Second message." in blocks[1].text
    assert "First" not in blocks[1].text


def test_blockquote_paragraphs_split_on_bare_markers():
    blocks = split_blocks("> Heading line\n>\n> First paragraph.\n>\n> Second paragraph.")
    assert [b.kind for b in blocks] == ["quote", "quote", "quote"]
    assert blocks[1].text == "First paragraph."


def test_bolded_metadata_lines_split_individually():
    blocks = split_blocks("**From:** A\n**To:** B\n**Subject:** C")
    assert len(blocks) == 3
    assert blocks[2].text == "**Subject:** C"


def test_list_items_split_individually():
    blocks = split_blocks("- first\n- second\n- third")
    assert [b.kind for b in blocks] == ["list_item"] * 3


def test_paragraphs_stay_whole():
    blocks = split_blocks("A paragraph that\nwraps across lines.\n\nA second one.")
    assert [b.kind for b in blocks] == ["paragraph", "paragraph"]
    assert blocks[0].text == "A paragraph that\nwraps across lines."


def test_horizontal_rules_are_dropped():
    assert not any(b.text.startswith("---") for b in split_blocks("Text.\n\n---\n\nMore."))


def test_indices_are_contiguous_and_line_spans_are_recorded():
    blocks = split_blocks("# Title\n\nA paragraph.\n\n- an item")
    assert [b.index for b in blocks] == [0, 1, 2]
    assert blocks[0].line_start == 1
    assert blocks[1].line_start == 3
    assert blocks[2].line_start == 5


# --- field tables ------------------------------------------------------------


def test_parse_field_table_strips_emphasis_and_skips_headers():
    parsed = parse_field_table(
        "| Field | Value |\n|---|---|\n| `Traffic_Light` | **Green** |\n| `Progress_Percent` | 45 |"
    )
    assert parsed == {"Traffic_Light": "Green", "Progress_Percent": "45"}


def test_parse_field_table_is_generic_over_field_names():
    parsed = parse_field_table("| Field | Value |\n|---|---|\n| `Anything_At_All` | a value |")
    assert parsed["Anything_At_All"] == "a value"


# --- document loading --------------------------------------------------------


def test_load_document_reads_title_type_and_date(tmp_path):
    path = write(tmp_path, "a.md", "# Email — 3 March 2026, 09:00 EAT\n\nBody text.")
    doc = load_document(path, "E1")
    assert doc.title == "Email — 3 March 2026, 09:00 EAT"
    assert doc.source_type == "Email"
    assert doc.doc_date == dt.date(2026, 3, 3)


def test_date_falls_back_to_the_italic_metadata_line(tmp_path):
    path = write(tmp_path, "a.md", "# Meeting note — a review\n\n*Extract. 19 August 2026.*\n\nBody.")
    assert load_document(path, "E1").doc_date == dt.date(2026, 8, 19)


def test_undated_document_is_undated_rather_than_guessed(tmp_path):
    path = write(tmp_path, "a.md", "# Two further items\n\nSomething happened on 8 September 2026.")
    doc = load_document(path, "E1")
    assert doc.doc_date is None
    assert doc.date_label == "undated"


def test_documents_are_ordered_oldest_first_with_undated_last(tmp_path):
    write(tmp_path, "zzz.md", "# Email — 1 January 2026\n\nEarliest.")
    write(tmp_path, "aaa.md", "# Email — 1 June 2026\n\nLater.")
    write(tmp_path, "mmm.md", "# Undated thing\n\nNo date.")
    docs = load_evidence(tmp_path)
    assert [d.doc_id for d in docs] == ["E1", "E2", "E3"]
    assert [d.filename for d in docs] == ["zzz.md", "aaa.md", "mmm.md"]


def test_ordering_is_stable_across_runs(tmp_path):
    write(tmp_path, "b.md", "# One\n\nx")
    write(tmp_path, "a.md", "# Two\n\ny")
    assert [d.filename for d in load_evidence(tmp_path)] == [
        d.filename for d in load_evidence(tmp_path)
    ]


def test_block_lookup_rejects_an_out_of_range_index(tmp_path):
    import pytest

    doc = load_document(write(tmp_path, "a.md", "# T\n\nOnly one paragraph."), "E1")
    doc.block(len(doc.blocks) - 1)
    with pytest.raises(IndexError):
        doc.block(len(doc.blocks))


def test_empty_folder_is_empty_not_an_error(tmp_path):
    """Evidence can be removed through the interface, so none is a real state."""
    assert load_evidence(tmp_path) == []
    assert load_evidence(tmp_path / "does-not-exist") == []
