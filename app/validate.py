"""Deterministic validation of a proposed row.

No model involved. Everything here is a rule from the CSF extract section 3,
checked in Python, so a schema violation cannot reach the director's screen
regardless of what the model produced.

Issues are split into repairable and non-repairable. Repairable issues (a
narrative over the character limit, say) send the graph back for another
attempt. Non-repairable ones are surfaced to the director rather than retried,
because silently trying again is how a workflow ends up hiding a real problem.
"""

from __future__ import annotations

import re

from . import vocab
from .schema import DraftRow, ValidationIssue


def validate_row(row: DraftRow, known_claim_ids: set[str] | None = None) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    if not vocab.QUARTER_PATTERN.match(row.Quarter):
        issues.append(
            ValidationIssue(
                field="Quarter",
                message=f"{row.Quarter!r} is not YYYY-QN format",
                repairable=False,
            )
        )

    if row.Source != vocab.SUBSTRATE_DRAFTED:
        issues.append(
            ValidationIssue(
                field="Source",
                message=(
                    f"must be {vocab.SUBSTRATE_DRAFTED!r} for a row originating from the "
                    f"Personal Layer, got {row.Source!r}"
                ),
                repairable=False,
            )
        )

    if row.submitted:
        issues.append(
            ValidationIssue(
                field="submitted",
                message="this workflow stages rows; it does not submit them",
                repairable=False,
            )
        )

    _check_traffic_light(row, issues)
    _check_progress(row, issues)
    _check_narratives(row, issues)
    _check_support_from(row, issues)
    _check_calculated_fields(row, issues)
    _check_prose_lengths(row, issues)
    _check_progress_figure_is_stated(row, issues)

    if known_claim_ids is not None:
        _check_claim_ids(row, known_claim_ids, issues)
        _check_bracketed_references(row, known_claim_ids, issues)

    return issues


def _check_prose_lengths(row: DraftRow, issues: list[ValidationIssue]) -> None:
    """A rationale nobody reads is not a rationale.

    The limit is on the short form only. The long form exists so that capping
    the short one costs no substance.
    """
    for name, proposal in row.proposals().items():
        if len(proposal.rationale) > vocab.RATIONALE_MAX_CHARS:
            issues.append(
                ValidationIssue(
                    field=name,
                    message=(
                        f"rationale is {len(proposal.rationale)} characters, limit is "
                        f"{vocab.RATIONALE_MAX_CHARS}. Put the argument in reasoning and "
                        "leave the decisive point here."
                    ),
                )
            )


def _check_bracketed_references(
    row: DraftRow, known_claim_ids: set[str], issues: list[ValidationIssue]
) -> None:
    """Every square bracket in prose must be a citation a director can follow.

    A bracket holding prose — "[prior submission context]", "[see above]" —
    reads exactly like a citation and leads nowhere. Either it resolves or it
    should not be in brackets.
    """
    for name, proposal in row.proposals().items():
        for text in (proposal.rationale, proposal.reasoning):
            for bracket in vocab.BRACKET_PATTERN.findall(text):
                parts = [p.strip() for p in bracket.split(",") if p.strip()]
                if not parts:
                    continue
                unresolvable = [
                    p
                    for p in parts
                    if not vocab.CLAIM_ID_PATTERN.match(p) or p not in known_claim_ids
                ]
                if unresolvable:
                    issues.append(
                        ValidationIssue(
                            field=name,
                            message=(
                                f"[{bracket}] looks like a citation but does not resolve. "
                                "Square brackets must contain claim ids only."
                            ),
                        )
                    )


_PERCENTAGES = re.compile(r"\b(\d{1,3})\s*(?:%|per cent|percent)")


def _check_progress_figure_is_stated(row: DraftRow, issues: list[ValidationIssue]) -> None:
    """The number submitted must be the number argued for.

    Reasoning that derives one figure and submits another leaves a director
    unable to tell which is the mistake.
    """
    value = row.Progress_Percent.value
    if not isinstance(value, int) or isinstance(value, bool):
        return

    prose = f"{row.Progress_Percent.rationale} {row.Progress_Percent.reasoning}".strip()
    if not prose:
        return

    mentioned = {int(m) for m in _PERCENTAGES.findall(prose)}
    if mentioned and value not in mentioned:
        issues.append(
            ValidationIssue(
                field="Progress_Percent",
                message=(
                    f"the figure submitted is {value} but the reasoning argues for "
                    f"{', '.join(str(m) for m in sorted(mentioned))}. State the figure "
                    "you are submitting."
                ),
            )
        )


def _check_traffic_light(row: DraftRow, issues: list[ValidationIssue]) -> None:
    value = row.Traffic_Light.value
    if value is None:
        if not row.Traffic_Light.needs_director_input:
            issues.append(
                ValidationIssue(field="Traffic_Light", message="no value proposed")
            )
        return
    if value not in vocab.TRAFFIC_LIGHTS:
        issues.append(
            ValidationIssue(
                field="Traffic_Light",
                message=(
                    f"{value!r} is outside the closed vocabulary "
                    f"({', '.join(vocab.TRAFFIC_LIGHTS)})"
                ),
            )
        )


def _check_progress(row: DraftRow, issues: list[ValidationIssue]) -> None:
    value = row.Progress_Percent.value
    if value is None:
        if not row.Progress_Percent.needs_director_input:
            issues.append(
                ValidationIssue(field="Progress_Percent", message="no value proposed")
            )
        return
    if isinstance(value, bool) or not isinstance(value, int):
        issues.append(
            ValidationIssue(
                field="Progress_Percent",
                message=f"must be an integer, got {type(value).__name__}",
            )
        )
        return
    if not vocab.PROGRESS_MIN <= value <= vocab.PROGRESS_MAX:
        issues.append(
            ValidationIssue(
                field="Progress_Percent",
                message=f"{value} is outside {vocab.PROGRESS_MIN}–{vocab.PROGRESS_MAX}",
            )
        )


def _check_narratives(row: DraftRow, issues: list[ValidationIssue]) -> None:
    for field in vocab.NARRATIVE_FIELDS:
        proposal = getattr(row, field)
        value = proposal.value
        if value is None:
            continue
        if not isinstance(value, str):
            issues.append(
                ValidationIssue(field=field, message=f"must be text, got {type(value).__name__}")
            )
            continue
        if len(value) > vocab.NARRATIVE_MAX_CHARS:
            issues.append(
                ValidationIssue(
                    field=field,
                    message=(
                        f"{len(value)} characters. The SharePoint column holds "
                        f"{vocab.NARRATIVE_MAX_CHARS}, so this will need trimming "
                        "before it is submitted."
                    ),
                    repairable=False,
                    severity="advice",
                )
            )


def _check_support_from(row: DraftRow, issues: list[ValidationIssue]) -> None:
    value = row.Support_From.value
    if value is None:
        return
    if not isinstance(value, list):
        issues.append(
            ValidationIssue(
                field="Support_From",
                message=f"must be a list of choices, got {type(value).__name__}",
            )
        )
        return
    for choice in value:
        if choice not in vocab.SUPPORT_FROM:
            issues.append(
                ValidationIssue(
                    field="Support_From",
                    message=(
                        f"{choice!r} is outside the vocabulary "
                        f"({', '.join(vocab.SUPPORT_FROM)})"
                    ),
                )
            )


def _check_calculated_fields(row: DraftRow, issues: list[ValidationIssue]) -> None:
    """Reject anything calculated downstream in Power BI.

    DraftRow has no such field, so this only fires if one is smuggled in as an
    extra attribute. Cheap insurance against a future edit reintroducing it.
    """
    for field in vocab.CALCULATED_DOWNSTREAM:
        if field in row.model_dump():
            issues.append(
                ValidationIssue(
                    field=field,
                    message=(
                        f"{field} is calculated downstream in Power BI and must not be submitted"
                    ),
                    repairable=False,
                )
            )


def _check_claim_ids(
    row: DraftRow, known_claim_ids: set[str], issues: list[ValidationIssue]
) -> None:
    """Every citation a director might click has to resolve to a real claim."""
    for name, proposal in row.proposals().items():
        unknown = [cid for cid in proposal.claim_ids if cid not in known_claim_ids]
        if unknown:
            issues.append(
                ValidationIssue(
                    field=name,
                    message=f"cites unknown claim ids: {', '.join(sorted(unknown))}",
                )
            )
        if proposal.value is not None and not proposal.claim_ids and name in vocab.SIGNIFICANT_FIELDS:
            issues.append(
                ValidationIssue(
                    field=name,
                    message="a significant claim with no supporting evidence",
                )
            )


def repairable(issues: list[ValidationIssue]) -> list[ValidationIssue]:
    return [i for i in issues if i.repairable and i.severity == "error"]


def blocking(issues: list[ValidationIssue]) -> list[ValidationIssue]:
    return [i for i in issues if not i.repairable and i.severity == "error"]


def errors(issues: list[ValidationIssue]) -> list[ValidationIssue]:
    """Things that make the row unusable, as opposed to things worth knowing."""
    return [i for i in issues if i.severity == "error"]


def advice(issues: list[ValidationIssue]) -> list[ValidationIssue]:
    return [i for i in issues if i.severity == "advice"]


def format_issues(issues: list[ValidationIssue]) -> str:
    return "\n".join(f"- {issue.field}: {issue.message}" for issue in issues)
