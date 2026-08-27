"""Understanding a document's format, rather than assuming it.

The heuristics in evidence.py encode what the supplied pack happens to look
like: "14 May 2026" dates, `**Name** · 11:02` chat lines, a document kind
before an em dash in the heading. Those are observations about one folder. The
tests here are about material that does not match any of them.

The property that matters most is the last one: the model chooses where blocks
begin and end, and never supplies their text.
"""

import datetime as dt

import pytest

from app.evidence import from_understanding, load_document, load_evidence
from app.understand import (
    Cache,
    Segment,
    Understanding,
    content_hash,
    number_lines,
    slice_segments,
    warm,
)


def write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


class Understander:
    """Returns a scripted Understanding, and counts how often it is asked."""

    def __init__(self, understanding):
        self._understanding = understanding
        self.calls = 0

    async def structured(self, system, instruction, schema, fast=False):
        self.calls += 1
        if isinstance(self._understanding, Exception):
            raise self._understanding
        return self._understanding


# --- the integrity property --------------------------------------------------


def test_block_text_is_sliced_from_the_file_never_supplied(tmp_path):
    """The model picks boundaries; the words come from the document.

    If block text came back from the model it could be paraphrased, and every
    downstream citation would point at a paraphrase while claiming to quote.
    """
    source = "# A title\n\nFirst thing said.\n\nSecond thing said.\n"
    path = write(tmp_path, "a.md", source)

    understanding = Understanding(
        title="A title",
        source_type="Note",
        date="2026-03-01",
        segments=[Segment(start_line=3, end_line=3), Segment(start_line=5, end_line=5)],
    )
    doc = from_understanding(path, "E1", source, understanding)

    assert [b.text for b in doc.blocks] == ["First thing said.", "Second thing said."]
    for block in doc.blocks:
        assert block.text in source, "every block must appear verbatim in the source"


def test_a_segment_running_past_the_end_is_clipped_not_fatal(tmp_path):
    source = "# T\n\nOnly line.\n"
    understanding = Understanding(
        title="T", source_type="Note", segments=[Segment(start_line=3, end_line=999)]
    )
    sliced = slice_segments(source, understanding)
    assert sliced == [("Only line.", "paragraph", 3, 3)]


def test_segments_entirely_out_of_range_are_dropped(tmp_path):
    source = "# T\n\nOnly line.\n"
    understanding = Understanding(
        title="T", source_type="Note", segments=[Segment(start_line=50, end_line=60)]
    )
    assert slice_segments(source, understanding) == []


def test_a_backwards_range_is_corrected_rather_than_rejected():
    segment = Segment(start_line=9, end_line=2)
    assert segment.end_line == 9


def test_nothing_usable_falls_back_to_the_heuristics(tmp_path):
    source = "# A title\n\nA paragraph.\n"
    path = write(tmp_path, "a.md", source)
    empty = Understanding(title="x", source_type="y", segments=[])

    assert from_understanding(path, "E1", source, empty) is None


# --- formats the heuristics cannot read --------------------------------------


def test_a_document_the_heuristics_cannot_date_is_dated_by_understanding(tmp_path):
    """Slash dates, month-first, no recognisable heading structure."""
    source = "Subject: Weekly note\nSent: 05/14/2026 09:41\n\nThe thing happened.\n"
    path = write(tmp_path, "odd.md", source)

    assert load_document(path, "E1").doc_date is None, "heuristics cannot read this"

    cache = Cache(tmp_path / "cache")
    cache.put(
        content_hash(source),
        Understanding(
            title="Weekly note",
            source_type="Email",
            date="2026-05-14",
            segments=[Segment(start_line=1, end_line=2), Segment(start_line=4, end_line=4)],
        ),
    )
    doc = load_document(path, "E1", cache=cache)

    assert doc.doc_date == dt.date(2026, 5, 14)
    assert doc.source_type == "Email"
    assert doc.title == "Weekly note"


def test_an_unfamiliar_chat_format_still_splits_per_message(tmp_path):
    """Not `**Name** · 11:02` — the shape the regex was written for."""
    source = "[09:14] alex: first message\n[09:15] sam: second message\n"
    path = write(tmp_path, "chat.md", source)

    heuristic = load_document(path, "E1")
    assert len(heuristic.blocks) == 1, "the heuristics see one paragraph"

    cache = Cache(tmp_path / "cache")
    cache.put(
        content_hash(source),
        Understanding(
            title="Chat",
            source_type="Chat export",
            segments=[
                Segment(start_line=1, end_line=1, kind="chat_message"),
                Segment(start_line=2, end_line=2, kind="chat_message"),
            ],
        ),
    )
    doc = load_document(path, "E1", cache=cache)

    assert len(doc.blocks) == 2
    assert doc.blocks[1].text == "[09:15] sam: second message"
    assert doc.blocks[1].kind == "chat_message"


def test_an_unforeseen_block_kind_is_kept_not_rejected(tmp_path):
    source = "Some content.\n"
    path = write(tmp_path, "a.md", source)
    understanding = Understanding(
        title="T",
        source_type="Something new",
        segments=[Segment(start_line=1, end_line=1, kind="transcript_turn")],
    )
    doc = from_understanding(path, "E1", source, understanding)
    assert doc.blocks[0].kind == "transcript_turn"


def test_an_unparseable_date_string_means_undated(tmp_path):
    source = "Content.\n"
    path = write(tmp_path, "a.md", source)
    understanding = Understanding(
        title="T", source_type="T", date="sometime in May",
        segments=[Segment(start_line=1, end_line=1)],
    )
    assert from_understanding(path, "E1", source, understanding).doc_date is None


# --- the cache ---------------------------------------------------------------


def test_the_cache_is_keyed_on_content_not_filename(tmp_path):
    source = "# A\n\nBody.\n"
    cache = Cache(tmp_path / "cache")
    understanding = Understanding(
        title="A", source_type="Note", segments=[Segment(start_line=3, end_line=3)]
    )
    cache.put(content_hash(source), understanding)

    renamed = write(tmp_path, "renamed.md", source)
    assert load_document(renamed, "E1", cache=cache).title == "A", "renaming reuses it"

    edited = write(tmp_path, "renamed.md", "# A\n\nDifferent body.\n")
    assert cache.get(content_hash(edited.read_text())) is None, "editing invalidates it"


def test_an_unreadable_cache_entry_is_a_miss_not_an_error(tmp_path):
    cache = Cache(tmp_path / "cache")
    cache.directory.mkdir(parents=True)
    (cache.directory / "deadbeef.json").write_text("{ not json", encoding="utf-8")
    assert cache.get("deadbeef") is None


@pytest.mark.asyncio
async def test_warm_only_reads_documents_it_has_not_seen(tmp_path):
    first = write(tmp_path, "a.md", "# A\n\nOne.\n")
    second = write(tmp_path, "b.md", "# B\n\nTwo.\n")
    cache = Cache(tmp_path / "cache")
    understander = Understander(
        Understanding(title="x", source_type="y", segments=[Segment(start_line=1, end_line=1)])
    )

    await warm(understander, [first, second], cache)
    assert understander.calls == 2

    await warm(understander, [first, second], cache)
    assert understander.calls == 2, "a warm cache costs nothing"

    third = write(tmp_path, "c.md", "# C\n\nThree.\n")
    await warm(understander, [first, second, third], cache)
    assert understander.calls == 3, "only the new document is read"


@pytest.mark.asyncio
async def test_one_document_failing_does_not_lose_the_others(tmp_path):
    """A document that cannot be understood falls back; the rest are unaffected."""
    path = write(tmp_path, "a.md", "# A\n\nOne.\n")
    cache = Cache(tmp_path / "cache")

    understood = await warm(Understander(RuntimeError("model said no")), [path], cache)

    assert understood == {}
    assert load_document(path, "E1", cache=cache).title == "A", "heuristics still work"


# --- ordering ----------------------------------------------------------------


def test_ordering_uses_dates_the_understanding_found(tmp_path):
    later = "Sent: 12/01/2026\n\nLater thing.\n"
    earlier = "Sent: 01/06/2026\n\nEarlier thing.\n"
    write(tmp_path, "a.md", later)
    write(tmp_path, "b.md", earlier)

    cache = Cache(tmp_path / "cache")
    cache.put(content_hash(later), Understanding(
        title="Later", source_type="Note", date="2026-12-01",
        segments=[Segment(start_line=3, end_line=3)]))
    cache.put(content_hash(earlier), Understanding(
        title="Earlier", source_type="Note", date="2026-01-06",
        segments=[Segment(start_line=3, end_line=3)]))

    docs = load_evidence(tmp_path, cache=cache)
    assert [d.title for d in docs] == ["Earlier", "Later"]


def test_line_numbering_is_one_indexed():
    numbered = number_lines("first\nsecond")
    assert numbered.splitlines()[0].strip().startswith("1 |")
    assert "2 | second" in numbered
