"""Writing to the data folder from the web.

The security-relevant part is `safe_target`: a filename arriving from a URL
must never resolve outside the evidence folder.
"""

import pytest

from app import store


@pytest.fixture
def evidence(tmp_path):
    folder = tmp_path / "evidence"
    folder.mkdir()
    return folder


# --- refusing bad paths ------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "../objective.md",
        "../../etc/passwd.md",
        "sub/dir.md",
        "sub\\dir.md",
        ".hidden.md",
        "",
    ],
)
def test_path_traversal_is_refused(evidence, name):
    with pytest.raises(store.StoreError):
        store.safe_target(evidence, name)


def test_non_markdown_is_refused(evidence):
    with pytest.raises(store.StoreError):
        store.safe_target(evidence, "notes.pdf")


def test_an_ordinary_name_resolves_inside_the_folder(evidence):
    assert store.safe_target(evidence, "a-note.md").parent == evidence.resolve()


# --- pasting -----------------------------------------------------------------


def test_pasted_text_becomes_a_loadable_document(evidence):
    path = store.add_pasted(evidence, "Email — 22 July 2026", "The body text.")
    text = path.read_text()

    assert path.parent == evidence
    assert text.startswith("# Email — 22 July 2026")
    assert "The body text." in text


def test_a_separate_date_is_written_where_the_loader_finds_it(evidence):
    from app.evidence import load_document

    path = store.add_pasted(evidence, "A meeting note", "Body.", date="19 August 2026")
    doc = load_document(path, "E1")

    assert doc.doc_date is not None
    assert doc.doc_date.isoformat() == "2026-08-19"


def test_titles_and_bodies_are_required(evidence):
    with pytest.raises(store.StoreError, match="title"):
        store.add_pasted(evidence, "", "body")
    with pytest.raises(store.StoreError, match="text"):
        store.add_pasted(evidence, "title", "   ")


def test_two_documents_with_the_same_title_do_not_collide(evidence):
    first = store.add_pasted(evidence, "Email", "One.")
    second = store.add_pasted(evidence, "Email", "Two.")

    assert first != second
    assert "One." in first.read_text() and "Two." in second.read_text()


def test_awkward_titles_produce_safe_filenames(evidence):
    path = store.add_pasted(evidence, "../../../etc/passwd  <>&", "Body.")
    assert path.parent == evidence
    assert "/" not in path.name


# --- uploading ---------------------------------------------------------------


def test_an_upload_is_stored(evidence):
    path = store.add_uploaded(evidence, "note.md", b"# A note\n\nBody.")
    assert "# A note" in path.read_text()


def test_an_upload_without_a_heading_gets_one(evidence):
    path = store.add_uploaded(evidence, "raw-export.txt", b"Just some text.")
    assert path.read_text().startswith("# raw-export")


def test_uploads_are_checked(evidence):
    with pytest.raises(store.StoreError, match="empty"):
        store.add_uploaded(evidence, "a.md", b"")
    with pytest.raises(store.StoreError, match="not markdown"):
        store.add_uploaded(evidence, "a.pdf", b"data")
    with pytest.raises(store.StoreError, match="larger than"):
        store.add_uploaded(evidence, "a.md", b"x" * (store.MAX_UPLOAD_BYTES + 1))
    with pytest.raises(store.StoreError, match="UTF-8"):
        store.add_uploaded(evidence, "a.md", b"\xff\xfe binary")


# --- editing and removing ----------------------------------------------------


def test_a_document_round_trips(evidence):
    store.add_pasted(evidence, "A note", "Original.")
    name = next(evidence.iterdir()).name

    store.write_document(evidence, name, "# A note\n\nRewritten.")
    assert "Rewritten." in store.read_document(evidence, name)


def test_a_document_cannot_be_emptied(evidence):
    store.add_pasted(evidence, "A note", "Original.")
    name = next(evidence.iterdir()).name

    with pytest.raises(store.StoreError, match="Delete it instead"):
        store.write_document(evidence, name, "   ")


def test_removal(evidence):
    store.add_pasted(evidence, "A note", "Body.")
    name = next(evidence.iterdir()).name

    store.remove(evidence, name)
    assert not list(evidence.iterdir())

    with pytest.raises(store.StoreError, match="not there"):
        store.remove(evidence, name)


# --- the objective -----------------------------------------------------------


def test_updating_the_objective_keeps_fields_the_form_never_showed(tmp_path):
    from app.evidence import parse_field_table

    path = tmp_path / "objective.md"
    existing = {
        "Objective_ID": "OBJ-1",
        "Title": "Old title",
        "Region": "somewhere",
        "Countries_In_Scope": "A, B",
    }
    store.update_objective(path, existing, {"Title": "New title"})

    reloaded = parse_field_table(path.read_text())
    assert reloaded["Title"] == "New title"
    assert reloaded["Region"] == "somewhere", "unshown fields must survive"
    assert reloaded["Countries_In_Scope"] == "A, B"
    assert reloaded["Last_Modified"]
