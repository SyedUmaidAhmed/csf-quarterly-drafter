"""Graph state.

Three groups: what was read, what was found, what is proposed. Claims accumulate
from a parallel fan-out over the documents, so they need a reducer; everything
else is written by exactly one node.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from ..schema import (
    Claim,
    Conflict,
    Correction,
    DraftRow,
    Gap,
    EvidenceDoc,
    NarrativeSet,
    StatusAssessment,
    ValidationIssue,
)


class DraftState(TypedDict, total=False):
    # --- inputs
    quarter: str
    objective: dict[str, str]
    prior_update: dict[str, str]
    docs: list[EvidenceDoc]

    # --- findings
    claims: Annotated[list[Claim], operator.add]
    conflicts: list[Conflict]
    gaps: list[Gap]
    reconciled_position: str
    assessment: StatusAssessment | None
    narratives: NarrativeSet | None

    # --- proposal
    row: DraftRow | None
    issues: list[ValidationIssue]
    repair_attempts: int

    # --- review
    corrections: list[Correction]
    staged_path: str | None


class ReadTask(TypedDict):
    """Payload for one parallel reading task."""

    doc: EvidenceDoc
    objective: dict[str, str]


def claim_index(state: DraftState) -> dict[str, Claim]:
    return {claim.claim_id: claim for claim in state.get("claims", [])}


def docs_by_id(state: DraftState) -> dict[str, EvidenceDoc]:
    return {doc.doc_id: doc for doc in state.get("docs", [])}


def initial_state(
    quarter: str,
    objective: dict[str, str],
    prior_update: dict[str, str],
    docs: list[EvidenceDoc],
) -> dict[str, Any]:
    return {
        "quarter": quarter,
        "objective": objective,
        "prior_update": prior_update,
        "docs": docs,
        "claims": [],
        "conflicts": [],
        "gaps": [],
        "reconciled_position": "",
        "assessment": None,
        "narratives": None,
        "row": None,
        "issues": [],
        "repair_attempts": 0,
        "corrections": [],
        "staged_path": None,
    }
