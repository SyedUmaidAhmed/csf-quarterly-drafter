"""Watching a run while it happens.

A minute of silence is indistinguishable from a broken button, and it is also
the moment when someone is deciding whether to trust the thing. Showing which
stage is running turns dead time into an explanation of the method: five
documents read separately, then reconciled, then assessed. That sequence is the
argument for the design, and this is the one place a director actually sees it.

Runs live in this process. A restart loses the progress stream but not the run
itself — that is checkpointed to SQLite, so the draft is still there.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import AsyncIterator

# Human wording for each graph node. Anything not listed is shown as-is, so a
# new node appears in the stream without needing to be registered here first.
STAGE_LABELS: dict[str, str] = {
    "load": "Working out what each document is",
    "read_document": "Reading",
    "reconcile": "Reconciling what the documents disagree about",
    "assess": "Assessing against the success measure",
    "compose": "Writing the narrative",
    "validate": "Checking against the CSF schema",
    "repair": "Fixing a validation problem",
    "review": "Ready for review",
}

TERMINAL = {"done", "failed"}


@dataclass
class Run:
    """One in-flight run and everyone watching it.

    The task is held here rather than left to the caller: asyncio keeps only a
    weak reference to a running task, so one that nobody holds can be collected
    part-way through and the run simply stops with no error anywhere.
    """

    thread_id: str
    events: list[dict] = field(default_factory=list)
    subscribers: list[asyncio.Queue] = field(default_factory=list)
    finished: bool = False
    task: asyncio.Task | None = None

    def publish(self, event: dict) -> None:
        self.events.append(event)
        if event.get("stage") in TERMINAL:
            self.finished = True
        for queue in self.subscribers:
            queue.put_nowait(event)


class Registry:
    """In-process runs, by thread id."""

    def __init__(self) -> None:
        self._runs: dict[str, Run] = {}

    def start(self, thread_id: str) -> Run:
        run = Run(thread_id=thread_id)
        self._runs[thread_id] = run
        return run

    def get(self, thread_id: str) -> Run | None:
        return self._runs.get(thread_id)

    def is_running(self, thread_id: str) -> bool:
        run = self._runs.get(thread_id)
        return run is not None and not run.finished

    def forget(self, thread_id: str) -> None:
        self._runs.pop(thread_id, None)

    def items(self):
        """Live runs for the workspace list."""
        return self._runs.items()


registry = Registry()


def plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def describe(node: str, payload: object, seen: dict[str, int]) -> dict:
    """Turn a graph update into something worth reading.

    The reading stage fires once per document, so it counts rather than
    repeating itself.
    """
    label = STAGE_LABELS.get(node, node.replace("_", " "))
    seen[node] = seen.get(node, 0) + 1

    detail = ""
    if node == "read_document":
        claims = len(payload.get("claims") or []) if isinstance(payload, dict) else 0
        detail = f"document {seen[node]} — {plural(claims, 'statement')}"
    elif node == "validate" and isinstance(payload, dict):
        count = len(payload.get("issues") or [])
        detail = "no problems" if not count else f"{count} to fix"
    elif node == "review":
        detail = "paused — nothing is submitted until you say so"
    elif node == "reconcile" and isinstance(payload, dict):
        conflicts = len(payload.get("conflicts") or [])
        gaps = len(payload.get("gaps") or [])
        detail = f"{plural(conflicts, 'conflict')}, {plural(gaps, 'gap')}"

    return {"stage": node, "label": label, "detail": detail, "count": seen[node]}


async def stream(run: Run) -> AsyncIterator[str]:
    """Server-sent events for one run.

    Replays what has already happened before following along, so arriving late
    — or reloading the page — still shows the whole sequence.
    """
    queue: asyncio.Queue = asyncio.Queue()
    for event in run.events:
        queue.put_nowait(event)
    run.subscribers.append(queue)

    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=15)
            except TimeoutError:
                # A comment frame keeps proxies from closing an idle stream.
                yield ": still working\n\n"
                continue

            yield f"data: {json.dumps(event)}\n\n"
            if event.get("stage") in TERMINAL:
                return
    finally:
        if queue in run.subscribers:
            run.subscribers.remove(queue)
