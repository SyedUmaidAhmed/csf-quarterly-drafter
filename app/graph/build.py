"""Wiring.

    load ─▶ read_document (one per document, in parallel)
              │
              ▼
          reconcile ─▶ assess ─▶ compose ─▶ validate
                    ▲               ▲           │
                    └─ repair ◀──────┴─ repair ◀─┤  back to whichever pass
                       (status)       (narrative) │  produced the bad field
                                                │
                                                ▼
                                             review  ── interrupt ──▶ director
                                                │
                                                ▼
                                      apply_corrections ─▶ stage ─▶ END
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from langgraph.graph import END, START, StateGraph

from ..config import Settings
from ..llm import LLMClient
from .nodes import Nodes
from .state import DraftState


def build_graph(llm: LLMClient, settings: Settings, checkpointer=None):
    nodes = Nodes(llm, settings)
    builder = StateGraph(DraftState)

    builder.add_node("load", nodes.load)
    builder.add_node("read_document", nodes.read_document)
    builder.add_node("reconcile", nodes.reconcile)
    builder.add_node("assess", nodes.assess)
    builder.add_node("compose", nodes.compose)
    builder.add_node("validate", nodes.validate)
    builder.add_node("repair_assessment", nodes.repair)
    builder.add_node("repair_narrative", nodes.repair)
    builder.add_node("review", nodes.review)
    builder.add_node("apply_corrections", nodes.apply_corrections)
    builder.add_node("stage", nodes.stage)

    builder.add_edge(START, "load")
    builder.add_conditional_edges("load", nodes.fan_out_reading, ["read_document"])
    builder.add_edge("read_document", "reconcile")
    builder.add_edge("reconcile", "assess")
    builder.add_edge("assess", "compose")
    builder.add_edge("compose", "validate")
    builder.add_conditional_edges(
        "validate",
        nodes.after_validation,
        {
            "repair_assessment": "repair_assessment",
            "repair_narrative": "repair_narrative",
            "review": "review",
        },
    )
    builder.add_edge("repair_assessment", "assess")
    builder.add_edge("repair_narrative", "compose")
    builder.add_edge("review", "apply_corrections")
    builder.add_edge("apply_corrections", "stage")
    builder.add_edge("stage", END)

    return builder.compile(checkpointer=checkpointer)


@asynccontextmanager
async def open_graph(llm: LLMClient, settings: Settings):
    """A graph with a durable checkpointer.

    SQLite because this is a pilot on one machine. The checkpointer is the
    only reason a director can be interrupted mid-review, close the tab, and
    come back to the same draft — so it is worth having even at this size.
    """
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    settings.runs_dir.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(settings.checkpoint_db)) as checkpointer:
        yield build_graph(llm, settings, checkpointer=checkpointer)
