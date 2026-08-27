"""Controlled vocabularies for the CSF Quarterly_Updates list.

Transcribed verbatim from the CSF Framework candidate extract v1.1, section 3.
These drive the Power BI reporting model, so drift here degrades everything
downstream. Treat this module as the single source of truth: nothing else in
the codebase should hard-code one of these strings.
"""

from __future__ import annotations

import re

# --- Traffic_Light -----------------------------------------------------------
# Closed vocabulary. The anchors are shown to the model and to the director so
# that both are reasoning against the same definitions.

TRAFFIC_LIGHTS: dict[str, str] = {
    "Red": "Significant risk; unlikely to deliver without intervention",
    "Amber": "Some challenges; may need support",
    "Green": "On track",
    "Blue": "Achieved / complete",
}

# --- Support_From ------------------------------------------------------------
# Choice, multi.

SUPPORT_FROM: tuple[str, ...] = (
    "ILT",
    "BDU",
    "Finance",
    "HR",
    "IDDT",
    "Communications",
    "Other",
)

# --- Source ------------------------------------------------------------------
# A row originating from the Personal Layer is Substrate-Drafted. That is what
# this workflow produces, and it is not the director's own value to pick.

SOURCES: tuple[str, ...] = ("Director", "Team-Approved", "Substrate-Drafted")

SUBSTRATE_DRAFTED = "Substrate-Drafted"

# --- Quarter -----------------------------------------------------------------

QUARTER_PATTERN = re.compile(r"^\d{4}-Q[1-4]$")

# --- Field limits ------------------------------------------------------------

# The SharePoint column width, from the CSF extract. Advisory rather than
# enforced: a longer value is shown with a note and still submittable, because
# the director may want to send it and trim it themselves. Silently rejecting
# their words would be worse than telling them the column is narrow.
NARRATIVE_MAX_CHARS = 200

NARRATIVE_FIELDS: tuple[str, ...] = (
    "Key_Success",
    "Key_Challenge",
    "Support_Needed",
)

PROGRESS_MIN = 0
PROGRESS_MAX = 100

# A rationale is read at a glance before a deadline, so it has a limit.
#
# Reasoning has none. It sits behind a disclosure and is read by someone who
# has decided they want to argue with the assessment; truncating that to hit a
# number costs real analysis and saves nobody anything.
RATIONALE_MAX_CHARS = 280

# Claim references, as they appear in prose: [E3.1] or [E3.1, E4.2].
# Every square bracket in a rationale must contain these and nothing else —
# a bracket holding prose reads as a citation and cannot be followed.
CLAIM_ID_PATTERN = re.compile(r"^E\d+\.\d+$")
BRACKET_PATTERN = re.compile(r"\[([^\]]*)\]")

# Fields where a value with no evidence behind it is a defect rather than a
# preference. Support_From and Support_Needed are excluded: a director can
# legitimately need nothing, and an empty multi-select is a real answer.
SIGNIFICANT_FIELDS: tuple[str, ...] = (
    "Traffic_Light",
    "Progress_Percent",
    "Key_Success",
    "Key_Challenge",
)

# --- Calculated downstream ---------------------------------------------------
# Trend_vs_Prior_Quarter is derived in the Power BI reporting model. It is not
# captured at submission and setting it is a mistake, so it is absent from
# DraftRow entirely and named here only so the validator can reject it if it
# ever turns up in a payload.

CALCULATED_DOWNSTREAM: tuple[str, ...] = ("Trend_vs_Prior_Quarter",)


def traffic_light_anchors() -> str:
    """The anchors as a prompt-ready block, so prompts cannot drift from code."""
    return "\n".join(f"- {value}: {anchor}" for value, anchor in TRAFFIC_LIGHTS.items())
