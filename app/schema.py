"""Data model for the drafting workflow.

Three layers, deliberately separate:

  Evidence   what was read           EvidenceDoc / Block
  Findings   what the model observed Claim / Conflict
  Draft      what is proposed        FieldProposal / DraftRow

Every proposed field carries the claim ids that support it, so the review UI
can show a director the sentence behind any number before they accept it.
"""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from . import vocab

# --- Evidence ----------------------------------------------------------------


class Block(BaseModel):
    """One citable unit of an evidence document.

    Blocks are the granularity Claude cites at: we send them as a custom
    content document so the API does no further chunking of its own, and
    citations come back as indices into this list.
    """

    index: int
    text: str
    line_start: int
    line_end: int
    # Deliberately open. The understanding pass names what it finds, and a
    # kind nobody anticipated should be shown, not rejected.
    kind: str = "paragraph"


class EvidenceDoc(BaseModel):
    doc_id: str  # E1, E2, ... assigned in date order
    filename: str
    source_path: str = ""  # where it was read from, so it can be re-read
    title: str
    source_type: str  # Email / Teams / Meeting note / Calendar / Document ...
    doc_date: dt.date | None
    blocks: list[Block]

    def block(self, index: int) -> Block:
        if not 0 <= index < len(self.blocks):
            raise IndexError(
                f"{self.doc_id}: citation points at block {index}, "
                f"but the document has {len(self.blocks)} blocks"
            )
        return self.blocks[index]

    @property
    def date_label(self) -> str:
        return self.doc_date.isoformat() if self.doc_date else "undated"


# --- Findings ----------------------------------------------------------------


class Citation(BaseModel):
    """A pointer into an evidence document, as returned by the Citations API.

    We never construct cited_text ourselves and never let the model write it:
    the API extracts it from the source, which is what makes it trustworthy.
    """

    doc_id: str
    start_block: int
    end_block: int  # exclusive, matching the API
    cited_text: str

    @property
    def block_indices(self) -> list[int]:
        return list(range(self.start_block, self.end_block))

    @property
    def label(self) -> str:
        return self.doc_id


class Claim(BaseModel):
    claim_id: str  # C1, C2, ...
    doc_id: str
    text: str  # the model's statement
    citations: list[Citation]

    @property
    def has_support(self) -> bool:
        return bool(self.citations)


class Conflict(BaseModel):
    """A disagreement between claims, and how it was resolved.

    rule_applied is recorded because a director who disagrees with the
    resolution needs to see the reasoning, not just the outcome.
    """

    topic: str
    winning_claim_id: str
    # Not enforced in the schema: a hard constraint here would fail the whole
    # tool call. The reconcile node reclassifies an empty one as a gap, which
    # guarantees the distinction rather than relying on the model to hold it.
    superseded_claim_ids: list[str] = Field(
        default_factory=list,
        description="At least one. A conflict that supersedes nothing is a gap, not a conflict.",
    )
    rule_applied: Literal["later_supersedes_earlier", "participant_supersedes_outbound", "other"]
    note: str


class Gap(BaseModel):
    """Something the evidence raises and never resolves.

    Distinct from a conflict on purpose. A conflict is two accounts that cannot
    both be true; a gap is one account and then silence — an expectation set,
    a commitment made, a question asked, with nothing later confirming or
    denying it. Both need a director's attention and they need different
    attention, so filing a gap as a conflict with nothing on the losing side
    misrepresents it.
    """

    topic: str
    raised_by_claim_ids: list[str] = Field(
        description="The claims that set up the expectation nothing later settles."
    )
    note: str = Field(description="What is unresolved, and what would settle it.")
    bears_on: str = Field(
        default="",
        description="Which part of the success measure this leaves unverified.",
    )


# --- Draft -------------------------------------------------------------------

NEEDS_DIRECTOR_INPUT = "__needs_director_input__"


class FieldProposal(BaseModel):
    """A proposed value plus its provenance.

    value is None when the evidence does not support one. An empty field a
    director must fill is better than a plausible sentence nobody said.

    rationale is the sentence a director reads while scanning; reasoning is the
    argument they read when they want to push back. Splitting them is what
    lets the second stay long enough to be worth having.

    confidence is computed from the evidence rather than self-reported — see
    provenance.assess.
    """

    value: str | int | list[str] | None
    claim_ids: list[str] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"] = "medium"
    confidence_reason: str = ""
    corroboration: int = 0
    rationale: str = ""
    reasoning: str = ""
    needs_director_input: bool = False
    edited_by_director: bool = False


class DraftRow(BaseModel):
    """The Quarterly_Updates row this workflow proposes.

    Trend_vs_Prior_Quarter is deliberately absent. It is calculated downstream
    in Power BI, so there is no field here to fill in by accident.
    """

    Objective_ID: str
    Quarter: str
    Traffic_Light: FieldProposal
    Progress_Percent: FieldProposal
    Key_Success: FieldProposal
    Key_Challenge: FieldProposal
    Support_Needed: FieldProposal
    Support_From: FieldProposal
    Source: str = vocab.SUBSTRATE_DRAFTED

    # Never set by this workflow. The director submits; we stage.
    submitted: bool = False

    def proposals(self) -> dict[str, FieldProposal]:
        return {
            name: getattr(self, name)
            for name in (
                "Traffic_Light",
                "Progress_Percent",
                "Key_Success",
                "Key_Challenge",
                "Support_Needed",
                "Support_From",
            )
        }

    def export_values(self) -> dict:
        """Flat SharePoint-shaped values for staging and download.

        FieldProposal bookkeeping stays in graph state; the staged file the
        director carries must be scalars and lists only.
        """
        out: dict = {
            "Objective_ID": self.Objective_ID,
            "Quarter": self.Quarter,
            "Source": self.Source,
            "submitted": self.submitted,
        }
        for name, proposal in self.proposals().items():
            out[name] = proposal.value
        return out


# --- Structured-output shapes ------------------------------------------------
# These are what the model fills in. They are separate from the types above so
# that provenance bookkeeping (claim ids resolved to citations, confidence
# defaults) stays in our code rather than being something the model can get
# wrong.


class Reconciliation(BaseModel):
    """Output of the reconcile pass."""

    conflicts: list[Conflict] = Field(default_factory=list)
    gaps: list[Gap] = Field(default_factory=list)
    reconciled_position: str = Field(
        description="The position the evidence actually supports, in a few sentences, "
        "citing claim ids in square brackets."
    )


class StatusAssessment(BaseModel):
    """Output of the assess pass.

    Each judgment comes in two lengths. The short one is what a director sees
    while scanning; the long one is what they read when they want to disagree.
    """

    traffic_light: Literal["Red", "Amber", "Green", "Blue"]
    # Deliberately no max_length. A hard schema limit turns an over-long
    # rationale into a tool-call failure that kills the run; the validator
    # catches the same thing and sends it back to be rewritten.
    traffic_light_rationale: str = Field(
        description=f"The decisive reason, at most {vocab.RATIONALE_MAX_CHARS} characters, "
        "naming the adjacent value rejected. Claim ids in square brackets.",
    )
    traffic_light_reasoning: str = Field(
        default="",
        description="The full argument, including what would have to be true for the "
        "adjacent value to be right.",
    )
    traffic_light_claim_ids: list[str]
    progress_percent: int = Field(ge=vocab.PROGRESS_MIN, le=vocab.PROGRESS_MAX)
    progress_rationale: str = Field(
        description=f"What the figure measures, at most {vocab.RATIONALE_MAX_CHARS} characters. "
        "It must state the submitted figure itself.",
    )
    progress_reasoning: str = Field(
        default="", description="The full derivation of the figure."
    )
    progress_claim_ids: list[str]


class NarrativeField(BaseModel):
    text: str | None = Field(
        default=None,
        description=f"At most {vocab.NARRATIVE_MAX_CHARS} characters. "
        "Null if the evidence does not support a value.",
    )
    claim_ids: list[str] = Field(default_factory=list)
    needs_director_input: bool = False

    @model_validator(mode="before")
    @classmethod
    def accept_a_bare_string(cls, value: object) -> object:
        """Take `"some text"` as well as `{"text": "some text"}`.

        Asking for an object per field is the right shape — the citations have
        to attach to something — but it is also the thing a model most reliably
        flattens, and it does so while getting the wording entirely right.
        Rejecting that outright throws away good work over a bracket.

        A coerced value arrives with no claim ids, so validation will flag it
        as an unevidenced claim and send it back for another attempt. The run
        survives either way.
        """
        if isinstance(value, str):
            return {"text": value}
        if value is None:
            return {"text": None, "needs_director_input": True}
        return value


class NarrativeSet(BaseModel):
    """Output of the compose pass."""

    key_success: NarrativeField
    key_challenge: NarrativeField
    support_needed: NarrativeField
    support_from: list[str] = Field(
        default_factory=list,
        description=f"Zero or more of: {', '.join(vocab.SUPPORT_FROM)}",
    )

    @model_validator(mode="before")
    @classmethod
    def fill_missing_narrative_fields(cls, value: object) -> object:
        """Incomplete tool calls should not kill the run.

        The model sometimes returns only one of the three narrative fields —
        often with the wording right — and LangChain rejects the whole call
        before the repair loop can send it back. Fill what is missing with
        values validation will flag, so compose can be retried instead.
        """
        if not isinstance(value, dict):
            return value
        filled = dict(value)
        # Empty text with no claim ids is repairable on the significant fields.
        placeholder = {"text": "", "claim_ids": []}
        for field in ("key_success", "key_challenge"):
            if field not in filled:
                filled[field] = dict(placeholder)
        if "support_needed" not in filled:
            filled["support_needed"] = {"text": None, "needs_director_input": True}
        return filled

    @field_validator("support_from", mode="before")
    @classmethod
    def accept_a_single_choice(cls, value: object) -> object:
        """A multi-select of one is easily returned as a bare string."""
        if isinstance(value, str):
            return [value] if value else []
        return value or []


# --- Review ------------------------------------------------------------------


class Correction(BaseModel):
    """One director edit, logged with what was on screen when it was made.

    The evidence the director was looking at is part of the record: a
    correction made after opening the source means something different from
    one made without.
    """

    field: str
    proposed_value: str | int | list[str] | None
    director_value: str | int | list[str] | None
    claim_ids_shown: list[str]
    timestamp: str


class ValidationIssue(BaseModel):
    """Something wrong with a proposed row.

    An error means the row is not usable and is worth another attempt. Advice
    is a fact the director should know that does not stop anything — a value
    wider than the SharePoint column, say, which they may still want to submit
    and shorten themselves.
    """

    field: str
    message: str
    repairable: bool = True
    severity: Literal["error", "advice"] = "error"
