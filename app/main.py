"""The web app the director actually uses.

Server-rendered HTML with progressive enhancement for inline edits. No build
step, one process, one command to start.

Every route either reads state or records a director's edit. None of them
submits to the system of record.
"""

from __future__ import annotations

import asyncio
import csv
import datetime as dt
import io
import json
import logging
import uuid
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from langgraph.types import Command

from . import config, progress, store, vocab
from .config import PACKAGE_DIR, settings
from .graph.build import open_graph
from .evidence import parse_field_table
from .inputs import load_inputs
from .llm import AnthropicClient, ReadOnlyClient
from .validate import advice, errors
from .schema import Correction, DraftRow

logger = logging.getLogger("csf-drafter")

app = FastAPI(title="CSF quarterly update drafter")
app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")

# Inline CSS/JS in HTML so Render free-tier cold starts still look right.
# Edge 404s (`x-render-routing: no-server`) on /static/* while HTML succeeds
# otherwise leave the page unstyled. Read per response so --reload and
# template |safe stay in sync with files on disk.
_STATIC = PACKAGE_DIR / "static"


def _inline_css() -> str:
    return "\n".join(
        (_STATIC / name).read_text(encoding="utf-8")
        for name in ("tokens.css", "app.css")
    )


def _inline_js() -> str:
    return (_STATIC / "app.js").read_text(encoding="utf-8")



def client() -> AnthropicClient:
    """The model client. Requires a key; there is no offline mode."""
    if not has_api_key():
        raise HTTPException(
            503,
            "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add a key.",
        )
    return AnthropicClient(settings)


def graph_client():
    """Client for opening the checkpointer. Read-only when no key is set."""
    if has_api_key():
        return AnthropicClient(settings)
    return ReadOnlyClient()


def has_api_key() -> bool:
    return bool(settings.resolved_api_key())


def proposal_value(raw: Any) -> Any:
    """Scalar from a staged field — flat new files or nested legacy dumps."""
    if isinstance(raw, dict) and "value" in raw:
        return raw.get("value")
    return raw


def flatten_staged_row(row: dict) -> dict:
    """Display/download shape: proposal nests become scalars."""
    flat = dict(row)
    for key in (
        "Traffic_Light",
        "Progress_Percent",
        "Key_Success",
        "Key_Challenge",
        "Support_Needed",
        "Support_From",
    ):
        if key in flat:
            flat[key] = proposal_value(flat[key])
    return flat


def key_source() -> str:
    """Where the loaded key came from, so it is obvious what is in play."""
    if config.runtime_api_key():
        return "entered here"
    if settings.anthropic_api_key:
        return ".env"
    return "environment" if has_api_key() else ""


def shell_context(
    *,
    nav: str = "",
    thread_id: str | None = None,
    staged_path: str | None = None,
    first_doc_id: str | None = None,
    extra: dict | None = None,
) -> dict:
    """Chrome shared by every page: key status, sidebar, run count."""
    ctx = {
        "nav": nav,
        "has_key": has_api_key(),
        "key_source": key_source(),
        "masked_key": config.mask(settings.resolved_api_key()),
        "run_count": len(discover_runs()),
        "thread_id": thread_id,
        "staged_path": staged_path,
        "first_doc_id": first_doc_id,
        "inline_css": _inline_css(),
        "inline_js": _inline_js(),
    }
    if extra:
        ctx.update(extra)
    return ctx


def render(
    request: Request,
    name: str,
    context: dict | None = None,
    *,
    nav: str = "",
    thread_id: str | None = None,
    staged_path: str | None = None,
    first_doc_id: str | None = None,
    status_code: int = 200,
):
    base = shell_context(
        nav=nav,
        thread_id=thread_id or (context or {}).get("thread_id"),
        staged_path=staged_path
        if staged_path is not None
        else (context or {}).get("staged_path"),
        first_doc_id=first_doc_id
        if first_doc_id is not None
        else (context or {}).get("first_doc_id"),
    )
    return templates.TemplateResponse(
        request, name, base | (context or {}), status_code=status_code
    )


def discover_runs() -> list[dict]:
    """Known runs from the live registry and run directories on disk."""
    found: dict[str, dict] = {}

    runs_dir = settings.runs_dir
    if runs_dir.exists():
        for path in runs_dir.iterdir():
            if not path.is_dir():
                continue
            if path.name in {"understanding"}:
                continue
            thread_id = path.name
            staged = path / "staged_row.json"
            entry: dict[str, Any] = {
                "thread_id": thread_id,
                "status": "staged" if staged.exists() else "review",
                "quarter": "",
                "objective_id": "",
                "traffic_light": "",
                "progress_percent": None,
                "staged": staged.exists(),
                "updated": staged.stat().st_mtime if staged.exists() else path.stat().st_mtime,
            }
            if staged.exists():
                try:
                    row = flatten_staged_row(
                        json.loads(staged.read_text(encoding="utf-8"))
                    )
                    entry["quarter"] = row.get("Quarter") or ""
                    entry["objective_id"] = row.get("Objective_ID") or ""
                    entry["traffic_light"] = row.get("Traffic_Light") or ""
                    entry["progress_percent"] = row.get("Progress_Percent")
                except (OSError, json.JSONDecodeError):
                    pass
            found[thread_id] = entry

    for thread_id, run in list(progress.registry.items()):
        entry = found.get(thread_id) or {
            "thread_id": thread_id,
            "status": "running",
            "quarter": "",
            "objective_id": "",
            "traffic_light": "",
            "progress_percent": None,
            "staged": False,
            "updated": 0,
        }
        if not run.finished:
            entry["status"] = "running"
        elif any(e.get("stage") == "failed" for e in run.events):
            entry["status"] = "failed"
        elif entry.get("staged"):
            entry["status"] = "staged"
        else:
            entry["status"] = "review"
        found[thread_id] = entry

    return sorted(found.values(), key=lambda r: r.get("updated") or 0, reverse=True)


# --- the key -----------------------------------------------------------------


@app.post("/settings/key")
async def set_key(request: Request, api_key: str = Form(default="")):
    api_key = api_key.strip()
    if not api_key:
        return _workspace(request, error="Paste a key first.")

    config.set_runtime_api_key(api_key)

    try:
        await AnthropicClient(settings).check()
    except Exception as error:
        config.clear_runtime_api_key()
        return _workspace(request, error=_readable(error))

    return _workspace(request, message="Key accepted. The API answered.")


@app.post("/settings/key/clear")
async def clear_key(request: Request):
    config.clear_runtime_api_key()
    return _workspace(request, message="Key cleared from this server.")


def _readable(error: Exception) -> str:
    """Turn an SDK exception into something a person can act on."""
    text = str(error)
    if "authentication_error" in text or "invalid x-api-key" in text or "401" in text:
        return "That key was rejected. Check it was copied whole."
    if "permission" in text.lower() or "403" in text:
        return "That key is valid but not permitted to use this model."
    if "credit" in text.lower() or "billing" in text.lower():
        return "That key has no available credit."
    return f"Could not reach the API: {text[:200]}"


def thread_config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


# --- landing / workspace -----------------------------------------------------


def workspace_context() -> dict:
    inputs = load_inputs(settings)
    return {
        "objective": inputs["objective"],
        "prior_update": inputs["prior_update"],
        "docs": inputs["docs"],
        "quarter": settings.quarter,
        "data_dir": settings.data_dir,
        "message": "",
        "error": "",
    }


@app.get("/", response_class=HTMLResponse)
async def runs_home(request: Request):
    runs = discover_runs()
    return render(
        request,
        "runs.html",
        {"runs": runs, "run_count": len(runs)},
        nav="runs",
    )


@app.get("/runs/new", response_class=HTMLResponse)
async def new_run(request: Request):
    try:
        context = workspace_context()
    except FileNotFoundError as error:
        return render(
            request,
            "error.html",
            {"message": str(error)},
            nav="new",
            status_code=500,
        )
    return render(request, "new.html", context, nav="new")


# Keep old / landing of workspace reachable via redirect if anything bookmarks it.
# Evidence CRUD still posts back to the workspace.


@app.post("/evidence/add")
async def add_evidence(
    request: Request,
    title: str = Form(default=""),
    date: str = Form(default=""),
    body: str = Form(default=""),
    files: list[UploadFile] = File(default=[]),
):
    added: list[str] = []
    try:
        for upload in files:
            if upload.filename:
                path = store.add_uploaded(
                    settings.evidence_dir, upload.filename, await upload.read()
                )
                added.append(path.name)

        if body.strip() or title.strip():
            path = store.add_pasted(settings.evidence_dir, title, body, date)
            added.append(path.name)

        if not added:
            raise store.StoreError("Nothing to add — paste some text or choose a file.")
    except store.StoreError as error:
        return _workspace(request, error=str(error))

    return _workspace(request, message=f"Added {', '.join(added)}.")


@app.post("/evidence/{filename}/delete")
async def delete_evidence(request: Request, filename: str):
    try:
        store.remove(settings.evidence_dir, filename)
    except store.StoreError as error:
        return _workspace(request, error=str(error))
    return _workspace(request, message=f"Removed {filename}.")


@app.get("/evidence/{filename}/edit", response_class=HTMLResponse)
async def edit_evidence(request: Request, filename: str):
    try:
        text = store.read_document(settings.evidence_dir, filename)
    except (store.StoreError, OSError) as error:
        raise HTTPException(404, str(error))
    return render(
        request,
        "edit.html",
        {"filename": filename, "text": text, "error": ""},
        nav="new",
    )


@app.post("/evidence/{filename}/edit")
async def save_evidence(request: Request, filename: str, text: str = Form(default="")):
    try:
        store.write_document(settings.evidence_dir, filename, text)
    except store.StoreError as error:
        return render(
            request,
            "edit.html",
            {"filename": filename, "text": text, "error": str(error)},
            nav="new",
            status_code=400,
        )
    return _workspace(request, message=f"Saved {filename}.")


@app.post("/objective")
async def save_objective(
    request: Request,
    Objective_ID: str = Form(default=""),
    Title: str = Form(default=""),
    Success_Measure: str = Form(default=""),
    Target_Completion: str = Form(default=""),
):
    existing = parse_field_table(settings.objective_file.read_text(encoding="utf-8"))
    store.update_objective(
        settings.objective_file,
        existing,
        {
            "Objective_ID": Objective_ID.strip() or existing.get("Objective_ID", ""),
            "Title": Title.strip(),
            "Success_Measure": Success_Measure.strip(),
            "Target_Completion": Target_Completion.strip(),
        },
    )
    return _workspace(request, message="Objective updated.")


def _workspace(request: Request, message: str = "", error: str = ""):
    """Re-render the new-run workspace."""
    return render(
        request,
        "new.html",
        workspace_context() | {"message": message, "error": error},
        nav="new",
        status_code=400 if error else 200,
    )


# --- generating a draft ------------------------------------------------------


@app.post("/runs")
async def create_run(request: Request, quarter: str = Form(default="")):
    """Run the graph to the review point and hand back a thread to review."""
    if not has_api_key():
        return _workspace(request, error="No API key loaded. Add one before drafting.")
    if not load_inputs(settings)["docs"]:
        return _workspace(request, error="There is no evidence to read. Add some first.")

    run_settings = settings.model_copy(update={"quarter": quarter} if quarter else {})
    thread_id = f"{dt.date.today():%Y%m%d}-{uuid.uuid4().hex[:6]}"

    run = progress.registry.start(thread_id)
    run.task = asyncio.create_task(_execute(thread_id, run_settings, run))

    return RedirectResponse(f"/runs/{thread_id}", status_code=303)


async def _execute(thread_id: str, run_settings, run: progress.Run) -> None:
    """Run the graph, publishing each stage as it completes."""
    seen: dict[str, int] = {}
    try:
        async with open_graph(client(), run_settings) as graph:
            async for chunk in graph.astream(
                load_inputs(run_settings),
                config=thread_config(thread_id),
                durability="sync",
                stream_mode="updates",
            ):
                for node, payload in chunk.items():
                    if node == "__interrupt__":
                        run.publish(progress.describe("review", payload, seen))
                    elif not node.startswith("__"):
                        run.publish(progress.describe(node, payload, seen))
        run.publish({"stage": "done", "label": "Draft ready", "detail": ""})
    except Exception as error:
        logger.exception("run %s failed", thread_id)
        run.publish(
            {
                "stage": "failed",
                "label": "The draft could not be completed",
                "detail": _readable(error),
            }
        )


@app.get("/runs/{thread_id}/progress.json")
async def run_progress(thread_id: str):
    run = progress.registry.get(thread_id)
    if run is None:
        raise HTTPException(404, f"no run called {thread_id}")
    return JSONResponse({"finished": run.finished, "events": run.events})


@app.get("/runs/{thread_id}/events")
async def run_events(thread_id: str):
    run = progress.registry.get(thread_id)
    if run is None:
        raise HTTPException(404, f"no run called {thread_id}")
    return StreamingResponse(
        progress.stream(run),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --- review ------------------------------------------------------------------


async def load_state(thread_id: str) -> dict[str, Any]:
    async with open_graph(graph_client(), settings) as graph:
        snapshot = await graph.aget_state(thread_config(thread_id))
    if not snapshot.values:
        raise HTTPException(404, f"no run called {thread_id}")
    return dict(snapshot.values) | {"_awaiting_review": bool(snapshot.next)}


def _first_doc_id(state: dict) -> str | None:
    docs = state.get("docs") or []
    if not docs:
        return None
    return docs[0].doc_id


@app.get("/runs/{thread_id}", response_class=HTMLResponse)
async def review(request: Request, thread_id: str):
    run = progress.registry.get(thread_id)

    if run is not None and not run.finished:
        return render(
            request,
            "running.html",
            {
                "thread_id": thread_id,
                "events": run.events,
                "stages": progress.STAGE_LABELS,
            },
            nav="review",
            thread_id=thread_id,
        )

    if run is not None and any(e["stage"] == "failed" for e in run.events):
        failure = next(e for e in run.events if e["stage"] == "failed")
        return render(
            request,
            "error.html",
            {
                "message": failure["label"],
                "detail": failure["detail"],
                "thread_id": thread_id,
            },
            nav="review",
            thread_id=thread_id,
            status_code=500,
        )

    try:
        state = await load_state(thread_id)
    except HTTPException as error:
        return render(
            request,
            "error.html",
            {
                "message": "Could not open this run",
                "detail": error.detail if isinstance(error.detail, str) else str(error.detail),
                "thread_id": thread_id,
            },
            nav="review",
            thread_id=thread_id,
            status_code=error.status_code,
        )

    context = review_context(thread_id, state)
    return render(
        request,
        "review.html",
        context,
        nav="review",
        thread_id=thread_id,
        staged_path=context.get("staged_path"),
        first_doc_id=context.get("first_doc_id"),
    )


def review_context(thread_id: str, state: dict) -> dict:
    row: DraftRow | None = state.get("row")
    docs = {doc.doc_id: doc for doc in state.get("docs", [])}
    claims = {claim.claim_id: claim for claim in state.get("claims", [])}
    first_doc = next(iter(docs), None)
    proposals = list(row.proposals().items()) if row else []
    acknowledged_count = sum(1 for _, p in proposals if p.edited_by_director)

    return {
        "thread_id": thread_id,
        "row": row,
        "fields": proposals,
        "acknowledged_count": acknowledged_count,
        "all_acknowledged": bool(proposals)
        and acknowledged_count == len(proposals),
        "objective": state.get("objective", {}),
        "prior_update": state.get("prior_update", {}),
        "conflicts": state.get("conflicts", []),
        "gaps": state.get("gaps", []),
        "reconciled_position": state.get("reconciled_position", ""),
        "issues": state.get("issues", []),
        "errors": errors(state.get("issues", [])),
        "advice": advice(state.get("issues", [])),
        "claims": claims,
        "docs": docs,
        "first_doc_id": first_doc,
        "staged_path": state.get("staged_path"),
        "awaiting_review": state.get("_awaiting_review", False),
        "traffic_lights": vocab.TRAFFIC_LIGHTS,
        "support_from_options": vocab.SUPPORT_FROM,
        "significant_fields": vocab.SIGNIFICANT_FIELDS,
        "max_chars": vocab.NARRATIVE_MAX_CHARS,
        "corrections": state.get("corrections", []),
    }


@app.post("/runs/{thread_id}/field/{field}", response_class=HTMLResponse)
async def edit_field(request: Request, thread_id: str, field: str):
    """Record a director's edit to one field and re-render just that field."""
    state = await load_state(thread_id)
    row: DraftRow | None = state.get("row")
    if row is None or field not in row.proposals():
        raise HTTPException(404, f"no field called {field}")

    form = await request.form()
    value = _coerce(field, form)
    proposal = getattr(row, field)

    correction = Correction(
        field=field,
        proposed_value=proposal.value,
        director_value=value,
        claim_ids_shown=proposal.claim_ids,
        timestamp=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    )

    corrections = [c for c in state.get("corrections", []) if c.field != field]
    corrections.append(correction)

    proposal.value = value
    proposal.edited_by_director = True
    proposal.needs_director_input = False

    async with open_graph(graph_client(), settings) as graph:
        await graph.aupdate_state(
            thread_config(thread_id), {"row": row, "corrections": corrections}
        )

    if request.headers.get("x-requested-with") != "fetch":
        return RedirectResponse(f"/runs/{thread_id}#field-{field}", status_code=303)

    context = review_context(thread_id, await load_state(thread_id))
    context["field_name"] = field
    return templates.TemplateResponse(request, "partials/field.html", context)


def _coerce(field: str, form) -> Any:
    if field == "Progress_Percent":
        raw = (form.get(field) or "").strip()
        try:
            return max(0, min(100, int(raw)))
        except ValueError:
            return None
    if field == "Support_From":
        return [v for v in form.getlist(field) if v in vocab.SUPPORT_FROM]
    value = (form.get(field) or "").strip()
    return value or None


# --- staging / approve -------------------------------------------------------


async def _stage_run(thread_id: str) -> None:
    if not has_api_key():
        raise HTTPException(400, "No API key loaded. Add one before approving.")
    state = await load_state(thread_id)
    row: DraftRow | None = state.get("row")
    if row is None:
        raise HTTPException(400, "No draft to approve.")
    pending = [
        name for name, proposal in row.proposals().items() if not proposal.edited_by_director
    ]
    if pending:
        raise HTTPException(
            400,
            "Acknowledge every field before approving for export. "
            f"Still needed: {', '.join(pending)}.",
        )
    corrections = [c.model_dump() for c in state.get("corrections", [])]

    async with open_graph(client(), settings) as graph:
        await graph.ainvoke(
            Command(resume={"corrections": corrections}),
            config=thread_config(thread_id),
            durability="sync",
        )


@app.post("/runs/{thread_id}/stage")
async def stage(thread_id: str):
    """Resume past review and write the staged row (legacy path name)."""
    await _stage_run(thread_id)
    return RedirectResponse(f"/runs/{thread_id}/export", status_code=303)


@app.post("/runs/{thread_id}/approve")
async def approve(thread_id: str):
    """SPA wording for stage — approve for export, never submit."""
    await _stage_run(thread_id)
    return RedirectResponse(f"/runs/{thread_id}/export", status_code=303)


@app.get("/runs/{thread_id}/staged.json")
async def staged_json(thread_id: str):
    path = settings.run_dir(thread_id) / "staged_row.json"
    if not path.exists():
        raise HTTPException(404, "nothing staged for this run yet")
    return JSONResponse(
        flatten_staged_row(json.loads(path.read_text(encoding="utf-8")))
    )


@app.get("/runs/{thread_id}/export", response_class=HTMLResponse)
async def export_page(request: Request, thread_id: str):
    path = settings.run_dir(thread_id) / "staged_row.json"
    if not path.exists():
        return render(
            request,
            "export.html",
            {
                "thread_id": thread_id,
                "approved": False,
                "row": None,
                "staged_at": None,
            },
            nav="export",
            thread_id=thread_id,
        )

    row = flatten_staged_row(json.loads(path.read_text(encoding="utf-8")))
    first_doc = None
    try:
        state = await load_state(thread_id)
        first_doc = _first_doc_id(state)
    except HTTPException:
        pass

    return render(
        request,
        "export.html",
        {
            "thread_id": thread_id,
            "approved": True,
            "row": row,
            "staged_path": str(path),
            "staged_at": dt.datetime.fromtimestamp(
                path.stat().st_mtime, tz=dt.timezone.utc
            ).isoformat(timespec="seconds"),
            "first_doc_id": first_doc,
        },
        nav="export",
        thread_id=thread_id,
        staged_path=str(path),
        first_doc_id=first_doc,
    )


@app.get("/runs/{thread_id}/export.csv")
async def export_csv(thread_id: str):
    path = settings.run_dir(thread_id) / "staged_row.json"
    if not path.exists():
        raise HTTPException(404, "nothing staged for this run yet")
    row = flatten_staged_row(json.loads(path.read_text(encoding="utf-8")))
    # Prefer SharePoint column order; drop internal bookkeeping from CSV.
    preferred = [
        "Objective_ID",
        "Quarter",
        "Traffic_Light",
        "Progress_Percent",
        "Key_Success",
        "Key_Challenge",
        "Support_Needed",
        "Support_From",
        "Source",
    ]
    keys = [k for k in preferred if k in row] + [
        k for k in row if k not in preferred and k not in {"submitted", "staged_at", "thread_id"}
    ]
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(keys)
    writer.writerow(
        [
            "; ".join(str(v) for v in row[k])
            if isinstance(row[k], list)
            else ("" if row[k] is None else str(row[k]))
            for k in keys
        ]
    )
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{thread_id}-row.csv"'
        },
    )


@app.get("/runs/{thread_id}/audit", response_class=HTMLResponse)
async def audit_page(request: Request, thread_id: str):
    events: list[dict] = []
    run = progress.registry.get(thread_id)
    if run is not None:
        events = list(run.events)

    corrections: list[Any] = []
    staged_path = None
    first_doc = None
    try:
        state = await load_state(thread_id)
        corrections = state.get("corrections") or []
        staged_path = state.get("staged_path")
        first_doc = _first_doc_id(state)
    except HTTPException:
        staged = settings.run_dir(thread_id) / "staged_row.json"
        if staged.exists():
            staged_path = str(staged)
        corrections_path = settings.run_dir(thread_id) / "corrections.jsonl"
        if corrections_path.exists():
            for line in corrections_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        corrections.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass

    return render(
        request,
        "audit.html",
        {
            "thread_id": thread_id,
            "events": events,
            "corrections": corrections,
            "staged_path": staged_path,
            "first_doc_id": first_doc,
        },
        nav="audit",
        thread_id=thread_id,
        staged_path=staged_path,
        first_doc_id=first_doc,
    )


@app.get("/runs/{thread_id}/audit.md")
async def audit_markdown(thread_id: str):
    lines = [f"# Audit trail — {thread_id}", ""]
    run = progress.registry.get(thread_id)
    if run and run.events:
        lines.append("## Pipeline events")
        for event in run.events:
            detail = f" — {event['detail']}" if event.get("detail") else ""
            lines.append(f"- **{event.get('label', event.get('stage'))}**{detail}")
        lines.append("")

    corrections: list[Any] = []
    try:
        state = await load_state(thread_id)
        corrections = state.get("corrections") or []
    except HTTPException:
        path = settings.run_dir(thread_id) / "corrections.jsonl"
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        corrections.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass

    if corrections:
        lines.append("## Director corrections")
        for c in corrections:
            if hasattr(c, "model_dump"):
                c = c.model_dump()
            lines.append(
                f"- **{c.get('field')}** proposed `{c.get('proposed_value')}` → "
                f"director `{c.get('director_value')}` ({c.get('timestamp', '')})"
            )
        lines.append("")

    staged = settings.run_dir(thread_id) / "staged_row.json"
    if staged.exists():
        lines.append("## Staging")
        lines.append(f"- Staged row written at `{staged}`")
        lines.append("- Source remains Substrate-Drafted — nothing was submitted.")

    return Response(
        content="\n".join(lines) + "\n",
        media_type="text/markdown",
        headers={
            "Content-Disposition": f'attachment; filename="{thread_id}-audit.md"'
        },
    )


@app.get("/runs/{thread_id}/state.json")
async def state_json(thread_id: str):
    """The proposed row as JSON, for anyone who would rather read the data."""
    state = await load_state(thread_id)
    row: DraftRow | None = state.get("row")
    return JSONResponse(
        {
            "thread_id": thread_id,
            "awaiting_review": state.get("_awaiting_review", False),
            "row": row.model_dump() if row else None,
            "conflicts": [c.model_dump() for c in state.get("conflicts", [])],
            "gaps": [g.model_dump() for g in state.get("gaps", [])],
            "issues": [i.model_dump() for i in state.get("issues", [])],
            "claims": [c.model_dump() for c in state.get("claims", [])],
        }
    )


# --- evidence ----------------------------------------------------------------


@app.get("/runs/{thread_id}/evidence/{doc_id}", response_class=HTMLResponse)
async def evidence(request: Request, thread_id: str, doc_id: str, claim: str = ""):
    try:
        context = await _source_context(thread_id, doc_id, claim)
    except HTTPException as error:
        return render(
            request,
            "error.html",
            {
                "message": "Could not open this evidence",
                "detail": error.detail if isinstance(error.detail, str) else str(error.detail),
                "thread_id": thread_id,
            },
            nav="evidence",
            thread_id=thread_id,
            status_code=error.status_code,
        )
    return render(
        request,
        "evidence.html",
        context,
        nav="evidence",
        thread_id=thread_id,
        staged_path=context.get("staged_path"),
        first_doc_id=context.get("first_doc_id"),
    )


async def _source_context(thread_id: str, doc_id: str, claim: str) -> dict:
    state = await load_state(thread_id)
    docs = {doc.doc_id: doc for doc in state.get("docs", [])}
    doc = docs.get(doc_id)
    if doc is None:
        raise HTTPException(404, f"no document called {doc_id}")

    claims = {c.claim_id: c for c in state.get("claims", [])}
    requested = [claims[cid] for cid in _split(claim) if cid in claims]

    return {
        "thread_id": thread_id,
        "doc": doc,
        "docs": docs,
        "first_doc_id": next(iter(docs), None),
        "staged_path": state.get("staged_path"),
        "cited_claims": requested,
        "highlighted": {
            index
            for cited_claim in requested
            for citation in cited_claim.citations
            if citation.doc_id == doc_id
            for index in citation.block_indices
        },
    }


@app.get("/runs/{thread_id}/source/{doc_id}", response_class=HTMLResponse)
async def source_fragment(
    request: Request, thread_id: str, doc_id: str, claim: str = ""
):
    context = await _source_context(thread_id, doc_id, claim)
    return templates.TemplateResponse(request, "partials/source.html", context)


def _split(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]
