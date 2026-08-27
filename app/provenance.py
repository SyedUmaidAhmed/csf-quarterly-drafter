"""Working out how well supported a proposed value actually is.

Confidence used to be whatever the model said it was, which produced the same
badge on every field — including one resting on three independent documents and
one resting on nothing. A model's self-assessment is weak signal, and a badge
that never varies is worse than no badge, because it looks like information.

So it is computed here instead, from properties of the evidence that can be
checked:

  how many distinct documents stand behind the value
  whether any of them was overturned during reconciliation

Both are general facts about citations, not facts about any particular
objective, so this behaves the same on evidence nobody has seen yet.
"""

from __future__ import annotations

from typing import Literal

from .schema import Claim, Conflict

Confidence = Literal["high", "medium", "low"]


def superseded_claim_ids(conflicts: list[Conflict]) -> set[str]:
    """Claims that lost a reconciliation. Anything resting on one is suspect."""
    return {cid for conflict in conflicts for cid in conflict.superseded_claim_ids}


def assess(
    claim_ids: list[str],
    claims: dict[str, Claim],
    conflicts: list[Conflict],
    abstained: bool = False,
) -> tuple[Confidence, int, str]:
    """Return (confidence, corroborating documents, why).

    The reason is returned alongside because a badge a director cannot
    interrogate is decoration. "one source" and "three sources agree" lead to
    different amounts of checking, and that is the whole point of showing it.
    """
    if abstained:
        return "low", 0, "no value proposed"

    resolved = [claims[cid] for cid in claim_ids if cid in claims]
    if not resolved:
        return "low", 0, "no evidence cited"

    overturned = superseded_claim_ids(conflicts)
    resting_on_overturned = sorted({c.claim_id for c in resolved} & overturned)
    if resting_on_overturned:
        return (
            "low",
            0,
            f"rests on evidence that was superseded ({', '.join(resting_on_overturned)})",
        )

    documents = sorted({claim.doc_id for claim in resolved})
    count = len(documents)

    if count >= 3:
        return "high", count, f"{count} independent documents agree ({', '.join(documents)})"
    if count == 2:
        return "high", count, f"two independent documents agree ({', '.join(documents)})"
    return "medium", 1, f"a single source ({documents[0]})"
