"""The nodes.

The shape of the pipeline is: read each document on its own, reconcile what
they say against each other, assess status against the success measure, write
the narrative, validate, then stop and wait for the director.

Reading each document in isolation is deliberate. A reader that sees all five
at once tends to harmonise them into a single tidy account, which is exactly
the information we need to keep — the disagreements between documents are the
substance of this problem, not noise to be smoothed away. Reconciliation is a
separate pass with explicit rules for that reason.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from string import Template

from langchain_core.runnables import RunnableConfig
from langgraph.types import Send, interrupt

from .. import provenance, vocab
from .. import understand
from ..citations import build_document_block, extract_cited_statements
from ..evidence import load_document
from ..understand import Cache
from ..config import Settings
from ..llm import LLMClient
from ..schema import (
    Claim,
    Correction,
    DraftRow,
    FieldProposal,
    Gap,
    NarrativeSet,
    Reconciliation,
    StatusAssessment,
)
from ..validate import blocking, errors, format_issues, repairable, validate_row
from .state import DraftState, ReadTask, claim_index

PROMPT_DIR = Path(__file__).parent.parent / "prompts"

# Which pass produces which field. A repair has to go back to the step that
# can actually change the thing that failed.
ASSESSED_FIELDS = ("Traffic_Light", "Progress_Percent")

SYSTEM = (
    "You support a director preparing a quarterly progress update against an "
    "institutional objective. You propose; the director decides and submits. "
    "You are accurate about what the evidence does and does not show, including "
    "when it shows less than someone hoped. Never present an expectation as an "
    "outcome, and never fill a gap with something plausible."
)


def prompt(name: str, **fields: object) -> str:
    template = Template((PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8"))
    return template.substitute(**fields)


# --- formatting helpers ------------------------------------------------------


def format_objective(objective: dict[str, str]) -> str:
    skip = {"Last_Modified"}
    return "\n".join(f"- {k}: {v}" for k, v in objective.items() if k not in skip and v != "—")


def format_prior_update(prior: dict[str, str]) -> str:
    if not prior:
        return "No previous update on file."
    return "\n".join(f"- {k}: {v}" for k, v in prior.items() if v and v != "—")


def format_claims(state: DraftState) -> str:
    docs = {doc.doc_id: doc for doc in state.get("docs", [])}
    lines = []
    for claim in state.get("claims", []):
        doc = docs.get(claim.doc_id)
        source = f"{doc.source_type}, {doc.date_label}" if doc else claim.doc_id
        lines.append(f"[{claim.claim_id}] ({source}) {claim.text}")
    return "\n".join(lines) if lines else "No claims were extracted."


def format_gaps(state: DraftState) -> str:
    gaps = state.get("gaps", [])
    if not gaps:
        return "None found."
    return "\n".join(
        f"- {g.topic} (raised by {', '.join(g.raised_by_claim_ids)}): {g.note}"
        for g in gaps
    )


def format_conflicts(state: DraftState) -> str:
    conflicts = state.get("conflicts", [])
    if not conflicts:
        return "None found."
    return "\n".join(
        f"- {c.topic}: [{c.winning_claim_id}] stands, "
        f"superseding {', '.join(c.superseded_claim_ids) or 'nothing'} "
        f"({c.rule_applied}). {c.note}"
        for c in conflicts
    )


class Nodes:
    """Nodes bound to a model client and settings.

    A class rather than module functions so the model client can be swapped
    for a scripted one in tests without any global state.
    """

    def __init__(self, llm: LLMClient, settings: Settings) -> None:
        self.llm = llm
        self.settings = settings

    # --- 1. understand --------------------------------------------------------

    async def load(self, state: DraftState) -> dict:
        """Work out what each document is, before reading any of it.

        Format is the part most likely to differ in evidence nobody has seen,
        so it is settled by a model rather than by rules about em dashes and
        timestamp shapes. Cached by content hash: unchanged documents cost
        nothing on a rerun.
        """
        cache = Cache(self.settings.understanding_dir)
        paths = [Path(doc.source_path) for doc in state["docs"] if doc.source_path]
        readable = [p for p in paths if p.exists()]
        if not readable:
            return {}

        await understand.warm(self.llm, readable, cache)

        # Re-read each document with whatever was just learned. Ordering can
        # change, because a date the heuristics missed may now be known.
        docs = [load_document(path, doc_id="", cache=cache) for path in readable]
        docs.sort(key=lambda d: (d.doc_date is None, d.doc_date or dt.date.max, d.filename))
        for position, doc in enumerate(docs, start=1):
            doc.doc_id = f"E{position}"
        return {"docs": docs}

    # --- 2. read ------------------------------------------------------------

    def fan_out_reading(self, state: DraftState) -> list[Send]:
        return [
            Send("read_document", ReadTask(doc=doc, objective=state["objective"]))
            for doc in state["docs"]
        ]

    async def read_document(self, task: ReadTask) -> dict:
        """Read one document with citations enabled.

        Claim ids are scoped to the document (E3.1, E3.2) so that parallel
        readers cannot collide, and so an id tells you its source on sight.
        """
        doc = task["doc"]
        content = await self.llm.read_with_citations(
            system=SYSTEM,
            instruction=prompt("read_cited", objective=format_objective(task["objective"])),
            documents=[build_document_block(doc)],
        )
        statements = extract_cited_statements(content, {0: doc})
        claims = [
            Claim(
                claim_id=f"{doc.doc_id}.{position}",
                doc_id=doc.doc_id,
                text=text,
                citations=citations,
            )
            for position, (text, citations) in enumerate(statements, start=1)
        ]
        return {"claims": claims}

    # --- 3. reconcile -------------------------------------------------------

    async def reconcile(self, state: DraftState) -> dict:
        result: Reconciliation = await self.llm.structured(
            system=SYSTEM,
            instruction=prompt(
                "reconcile",
                objective=format_objective(state["objective"]),
                success_measure=state["objective"].get("Success_Measure", "not stated"),
                claims=format_claims(state),
            ),
            schema=Reconciliation,
        )
        known = set(claim_index(state))
        conflicts, gaps = [], list(result.gaps)

        for conflict in result.conflicts:
            if conflict.winning_claim_id not in known:
                continue
            if conflict.superseded_claim_ids:
                conflicts.append(conflict)
            else:
                # Nothing was overturned, so nothing was in conflict. This is
                # a silence in the evidence and belongs with the other ones.
                gaps.append(
                    Gap(
                        topic=conflict.topic,
                        raised_by_claim_ids=[conflict.winning_claim_id],
                        note=conflict.note,
                    )
                )

        return {
            "conflicts": conflicts,
            "gaps": [g for g in gaps if set(g.raised_by_claim_ids) & known],
            "reconciled_position": result.reconciled_position,
        }

    # --- 4. assess ----------------------------------------------------------

    async def assess(self, state: DraftState) -> dict:
        objective = state["objective"]
        assessment: StatusAssessment = await self.llm.structured(
            system=SYSTEM,
            instruction=prompt(
                "assess",
                objective=format_objective(objective),
                success_measure=objective.get("Success_Measure", "not stated"),
                target_completion=objective.get("Target_Completion", "not stated"),
                quarter=state["quarter"],
                reconciled_position=state.get("reconciled_position", ""),
                conflicts=format_conflicts(state),
                gaps=format_gaps(state),
                claims=format_claims(state),
                prior_update=format_prior_update(state.get("prior_update", {})),
                anchors=vocab.traffic_light_anchors(),
                rationale_limit=vocab.RATIONALE_MAX_CHARS,
                repair_note=_repair_note(state),
            ),
            schema=StatusAssessment,
        )
        return {"assessment": assessment}

    # --- 5. compose ---------------------------------------------------------

    async def compose(self, state: DraftState) -> dict:
        objective = state["objective"]
        assessment = state["assessment"]
        narratives: NarrativeSet = await self.llm.structured(
            system=SYSTEM,
            instruction=prompt(
                "compose",
                objective=format_objective(objective),
                success_measure=objective.get("Success_Measure", "not stated"),
                reconciled_position=state.get("reconciled_position", ""),
                conflicts=format_conflicts(state),
                gaps=format_gaps(state),
                claims=format_claims(state),
                traffic_light=assessment.traffic_light,
                traffic_light_rationale=assessment.traffic_light_rationale,
                progress_percent=assessment.progress_percent,
                progress_rationale=assessment.progress_rationale,
                repair_note=_repair_note(state),
                max_chars=vocab.NARRATIVE_MAX_CHARS,
            ),
            schema=NarrativeSet,
        )
        return {"narratives": narratives, "row": self._build_row(state, narratives)}

    def _build_row(self, state: DraftState, narratives: NarrativeSet) -> DraftRow:
        assessment = state["assessment"]
        claims = claim_index(state)
        conflicts = state.get("conflicts", [])

        def proposal(
            value, claim_ids: list[str], rationale: str = "", reasoning: str = "", **kwargs
        ) -> FieldProposal:
            """Attach provenance. Confidence is derived, never self-reported."""
            abstained = kwargs.pop("abstained", value is None)
            level, corroboration, reason = provenance.assess(
                claim_ids, claims, conflicts, abstained=abstained
            )
            return FieldProposal(
                value=value,
                claim_ids=claim_ids,
                confidence=level,
                confidence_reason=reason,
                corroboration=corroboration,
                rationale=rationale,
                reasoning=reasoning,
                **kwargs,
            )

        def narrative(field) -> FieldProposal:
            return proposal(
                field.text,
                field.claim_ids,
                needs_director_input=field.needs_director_input or field.text is None,
            )

        # Empty agent output becomes Other so the review form always has a tick.
        support_from = narratives.support_from or ["Other"]

        return DraftRow(
            Objective_ID=state["objective"].get("Objective_ID", "unknown"),
            Quarter=state["quarter"],
            Traffic_Light=proposal(
                assessment.traffic_light,
                assessment.traffic_light_claim_ids,
                rationale=assessment.traffic_light_rationale,
                reasoning=assessment.traffic_light_reasoning,
            ),
            Progress_Percent=proposal(
                assessment.progress_percent,
                assessment.progress_claim_ids,
                rationale=assessment.progress_rationale,
                reasoning=assessment.progress_reasoning,
            ),
            Key_Success=narrative(narratives.key_success),
            Key_Challenge=narrative(narratives.key_challenge),
            Support_Needed=narrative(narratives.support_needed),
            # Support_From names the function behind Support_Needed, so it
            # rests on the same evidence rather than on none at all.
            Support_From=proposal(
                support_from,
                narratives.support_needed.claim_ids if support_from else [],
                abstained=not support_from,
            ),
        )

    # --- 6. validate --------------------------------------------------------

    def validate(self, state: DraftState) -> dict:
        row = state["row"]
        issues = validate_row(row, known_claim_ids=set(claim_index(state)))
        return {"issues": issues}

    def after_validation(self, state: DraftState) -> str:
        """Retry a repairable failure, at the step that can actually fix it.

        Which step matters. The status fields come from assess and the
        narrative fields from compose, so sending everything back to compose
        means a bad rationale on the traffic light can never be repaired —
        the retries are spent re-writing text that was never the problem.

        A blocking issue is not something to loop over until it goes away. If
        the row cannot be made valid, the director needs to see that rather
        than wait while the workflow tries again.
        """
        issues = errors(state.get("issues", []))
        if not issues or blocking(issues):
            return "review"
        if state.get("repair_attempts", 0) >= self.settings.max_repair_attempts:
            return "review"

        failing = {issue.field for issue in repairable(issues)}
        if failing & set(ASSESSED_FIELDS):
            # assess flows on through compose, so this repairs both.
            return "repair_assessment"
        return "repair_narrative"

    def repair(self, state: DraftState) -> dict:
        return {"repair_attempts": state.get("repair_attempts", 0) + 1}

    # --- 7. review ----------------------------------------------------------

    def review(self, state: DraftState) -> dict:
        """Stop. Everything above this line is a proposal.

        interrupt() is the first statement in this node on purpose: on resume
        LangGraph re-runs the node from the top, not from the interrupt, so
        anything above it would run twice. Side effects live in the nodes
        after this one.
        """
        corrections = interrupt(
            {
                "reason": "director_review",
                "row": state["row"].model_dump() if state.get("row") else None,
                "issues": [i.model_dump() for i in state.get("issues", [])],
            }
        )
        return {"corrections": _as_corrections(corrections)}

    # --- 8. apply and stage -------------------------------------------------

    def apply_corrections(self, state: DraftState) -> dict:
        row = state["row"].model_copy(deep=True)
        for correction in state.get("corrections", []):
            proposal = getattr(row, correction.field, None)
            if proposal is None:
                continue
            proposal.value = correction.director_value
            proposal.edited_by_director = True
            proposal.needs_director_input = False
        issues = validate_row(row, known_claim_ids=set(claim_index(state)))
        return {"row": row, "issues": issues}

    def stage(self, state: DraftState, config: RunnableConfig) -> dict:
        """Write the row where the director can pick it up.

        Staging, not submitting. There is no code path in this project that
        writes to a system of record, and the flag in the file says so.
        """
        thread_id = _thread_id(config)
        run_dir = self.settings.run_dir(thread_id)
        run_dir.mkdir(parents=True, exist_ok=True)

        row = state["row"]
        payload = {
            **row.export_values(),
            "staged_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "thread_id": thread_id,
        }
        staged_path = run_dir / "staged_row.json"
        staged_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        _append_corrections_log(run_dir, state.get("corrections", []))
        return {"staged_path": str(staged_path)}


# --- helpers -----------------------------------------------------------------


def _repair_note(state: DraftState) -> str:
    issues = repairable(state.get("issues", []))
    if not issues:
        return ""
    return (
        "## A previous attempt was rejected\n\n"
        "Fix these and keep everything else you can:\n\n"
        f"{format_issues(issues)}"
    )


def _as_corrections(payload: object) -> list[Correction]:
    if not payload:
        return []
    if isinstance(payload, dict):
        payload = payload.get("corrections", [])
    return [Correction.model_validate(item) for item in payload or []]


def _thread_id(config: RunnableConfig | None) -> str:
    thread_id = (config or {}).get("configurable", {}).get("thread_id")
    if not thread_id:
        raise ValueError("stage requires a thread_id: staged rows are filed under one")
    return str(thread_id)


def _append_corrections_log(run_dir: Path, corrections: list[Correction]) -> None:
    """Append-only, one JSON object per line.

    This log is the raw material for improving future drafts. It records what
    was proposed, what the director changed it to, and which evidence was on
    screen at the time — a correction made after opening the source means
    something different from one made without.
    """
    if not corrections:
        return
    path = run_dir / "corrections.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        for correction in corrections:
            handle.write(json.dumps(correction.model_dump()) + "\n")
