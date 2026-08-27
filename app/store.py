"""Writing to the data folder.

The evidence folder is the input surface, and until now the only way to change
it was a text editor. That matters more than it sounds: the whole point of this
workflow is that someone can put new material in front of it and rerun, and if
that requires a terminal then the tool only works for the person who built it.

Everything here writes plain markdown to `DATA_DIR`. No database, no upload
store — the files stay exactly as readable and as diffable as they were when
someone dropped them in by hand.
"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
from pathlib import Path

MARKDOWN_SUFFIXES = {".md", ".markdown", ".txt"}
MAX_UPLOAD_BYTES = 1_000_000


class StoreError(ValueError):
    """Something the person can fix, phrased so they can fix it."""


def slugify(text: str) -> str:
    normalised = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", normalised).strip("-").lower()
    return slug[:60] or "document"


def safe_target(evidence_dir: Path, filename: str) -> Path:
    """Resolve a filename inside the evidence folder, or refuse.

    Anything with a path separator, a parent reference, or a resolved location
    outside the folder is rejected rather than sanitised — quietly rewriting a
    suspicious path is how a traversal bug survives review.
    """
    if not filename or "/" in filename or "\\" in filename or filename.startswith("."):
        raise StoreError(f"{filename!r} is not a valid document name")

    target = (evidence_dir / filename).resolve()
    if target.parent != evidence_dir.resolve():
        raise StoreError(f"{filename!r} is outside the evidence folder")
    if target.suffix.lower() not in MARKDOWN_SUFFIXES:
        raise StoreError(f"{target.suffix or 'that'} is not a markdown file")
    return target


def unique_path(evidence_dir: Path, slug: str) -> Path:
    candidate = evidence_dir / f"{slug}.md"
    counter = 2
    while candidate.exists():
        candidate = evidence_dir / f"{slug}-{counter}.md"
        counter += 1
    return candidate


def compose_markdown(title: str, body: str, date: str = "") -> str:
    """Build a document the loader can read back.

    The loader takes the date from the heading, falling back to an italic line
    just beneath it. Writing the date on its own line means a title does not
    have to be phrased in any particular way to be dated correctly.
    """
    title = title.strip()
    body = body.strip()
    lines = [f"# {title}", ""]
    if date.strip():
        lines += [f"*{date.strip()}*", ""]
    lines += [body, ""]
    return "\n".join(lines)


def add_pasted(evidence_dir: Path, title: str, body: str, date: str = "") -> Path:
    if not title.strip():
        raise StoreError("Give the document a title — it is what the reader sees first.")
    if not body.strip():
        raise StoreError("Paste the document text.")

    evidence_dir.mkdir(parents=True, exist_ok=True)
    path = unique_path(evidence_dir, slugify(title))
    path.write_text(compose_markdown(title, body, date), encoding="utf-8")
    return path


def add_uploaded(evidence_dir: Path, filename: str, content: bytes) -> Path:
    if not content:
        raise StoreError(f"{filename} is empty.")
    if len(content) > MAX_UPLOAD_BYTES:
        raise StoreError(f"{filename} is larger than 1 MB.")

    suffix = Path(filename).suffix.lower()
    if suffix not in MARKDOWN_SUFFIXES:
        raise StoreError(f"{filename} is not markdown or plain text.")

    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise StoreError(f"{filename} is not valid UTF-8 text.") from error

    evidence_dir.mkdir(parents=True, exist_ok=True)
    path = unique_path(evidence_dir, slugify(Path(filename).stem))

    # Give it a heading if it has none, so the loader has a title to show.
    if not text.lstrip().startswith("#"):
        text = f"# {Path(filename).stem}\n\n{text}"
    path.write_text(text, encoding="utf-8")
    return path


def remove(evidence_dir: Path, filename: str) -> None:
    target = safe_target(evidence_dir, filename)
    if not target.exists():
        raise StoreError(f"{filename} is not there.")
    target.unlink()


def read_document(evidence_dir: Path, filename: str) -> str:
    return safe_target(evidence_dir, filename).read_text(encoding="utf-8")


def write_document(evidence_dir: Path, filename: str, text: str) -> Path:
    if not text.strip():
        raise StoreError("A document cannot be empty. Delete it instead.")
    target = safe_target(evidence_dir, filename)
    target.write_text(text.rstrip() + "\n", encoding="utf-8")
    return target


# --- the objective record ----------------------------------------------------


def write_field_table(path: Path, title: str, fields: dict[str, str]) -> None:
    """Write a `| Field | Value |` table, the shape the loader reads."""
    rows = "\n".join(f"| `{key}` | {value or '—'} |" for key, value in fields.items())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"# {title}\n\n| Field | Value |\n|---|---|\n{rows}\n", encoding="utf-8"
    )


def update_objective(path: Path, existing: dict[str, str], changes: dict[str, str]) -> None:
    """Apply changes, keeping every field the form did not carry.

    A form that shows four fields must not silently drop the ten it does not.
    """
    merged = dict(existing)
    merged.update({k: v for k, v in changes.items() if v is not None})
    merged["Last_Modified"] = dt.date.today().isoformat()
    write_field_table(path, f"Level2_Objectives — {merged.get('Objective_ID', 'record')}", merged)
