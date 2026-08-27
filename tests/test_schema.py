"""Schema-level guarantees.

The one that matters: Trend_vs_Prior_Quarter is calculated downstream in Power
BI. It is not merely "something we should remember not to set" — there is no
field for it, so it cannot be set by accident.
"""

import pytest
from pydantic import ValidationError

from app import vocab
from app.schema import DraftRow, FieldProposal, StatusAssessment


def make_row(**overrides) -> DraftRow:
    defaults = dict(
        Objective_ID="OBJ-TEST-01",
        Quarter="2026-Q3",
        Traffic_Light=FieldProposal(value="Amber"),
        Progress_Percent=FieldProposal(value=30),
        Key_Success=FieldProposal(value="A thing happened."),
        Key_Challenge=FieldProposal(value="A different thing did not."),
        Support_Needed=FieldProposal(value=None, needs_director_input=True),
        Support_From=FieldProposal(value=[]),
    )
    return DraftRow(**{**defaults, **overrides})


def test_trend_field_absent_from_model():
    assert "Trend_vs_Prior_Quarter" not in DraftRow.model_fields


def test_trend_field_absent_from_serialised_output():
    dumped = make_row().model_dump()
    assert "Trend_vs_Prior_Quarter" not in dumped
    assert "Trend_vs_Prior_Quarter" not in make_row().model_dump_json()


def test_trend_field_cannot_be_smuggled_in():
    """Passing it should not create it, whatever pydantic's extra policy is."""
    row = DraftRow(
        Objective_ID="OBJ-TEST-01",
        Quarter="2026-Q3",
        Traffic_Light=FieldProposal(value="Amber"),
        Progress_Percent=FieldProposal(value=30),
        Key_Success=FieldProposal(value="x"),
        Key_Challenge=FieldProposal(value="y"),
        Support_Needed=FieldProposal(value=None),
        Support_From=FieldProposal(value=[]),
        Trend_vs_Prior_Quarter="Deteriorated",
    )
    assert "Trend_vs_Prior_Quarter" not in row.model_dump()


def test_source_defaults_to_substrate_drafted():
    assert make_row().Source == vocab.SUBSTRATE_DRAFTED


def test_row_is_not_submitted_by_default():
    assert make_row().submitted is False
    assert make_row().model_dump()["submitted"] is False


def test_proposals_covers_every_director_editable_field():
    assert set(make_row().proposals()) == {
        "Traffic_Light",
        "Progress_Percent",
        "Key_Success",
        "Key_Challenge",
        "Support_Needed",
        "Support_From",
    }


def test_status_assessment_rejects_out_of_range_progress():
    with pytest.raises(ValidationError):
        StatusAssessment(
            traffic_light="Amber",
            traffic_light_rationale="r",
            traffic_light_claim_ids=["C1"],
            progress_percent=140,
            progress_rationale="r",
            progress_claim_ids=["C1"],
        )


def test_status_assessment_rejects_traffic_light_outside_vocabulary():
    with pytest.raises(ValidationError):
        StatusAssessment(
            traffic_light="Orange",
            traffic_light_rationale="r",
            traffic_light_claim_ids=["C1"],
            progress_percent=30,
            progress_rationale="r",
            progress_claim_ids=["C1"],
        )


# --- shapes a model actually returns -----------------------------------------
# The nested {text, claim_ids} object is the right shape, and it is also the
# one most reliably flattened to a bare string — with the wording entirely
# correct. These cases come from a real run that crashed on exactly that.


def test_narrative_field_accepts_a_bare_string():
    from app.schema import NarrativeField

    field = NarrativeField.model_validate("The agreement was signed on 30 June.")
    assert field.text == "The agreement was signed on 30 June."
    assert field.claim_ids == []
    assert field.needs_director_input is False


def test_narrative_field_accepts_null_as_abstention():
    from app.schema import NarrativeField

    field = NarrativeField.model_validate(None)
    assert field.text is None
    assert field.needs_director_input is True


def test_narrative_field_still_accepts_the_proper_object():
    from app.schema import NarrativeField

    field = NarrativeField.model_validate({"text": "A value.", "claim_ids": ["E3.1"]})
    assert field.claim_ids == ["E3.1"]


def test_narrative_set_survives_a_fully_flattened_response():
    """The exact shape that crashed a real run."""
    from app.schema import NarrativeSet

    narratives = NarrativeSet.model_validate(
        {
            "key_success": "One agreement signed, covering two of five categories.",
            "key_challenge": "Two of three agreements will not conclude this year.",
            "support_needed": "Partnerships and Legal Office to assign a drafter.",
            "support_from": "Finance",
        }
    )
    assert narratives.key_success.text.startswith("One agreement")
    assert narratives.support_from == ["Finance"], "a single choice may arrive unwrapped"


def test_narrative_set_survives_a_partial_response():
    """The model sometimes returns only one of three narrative fields."""
    from app.schema import NarrativeSet

    narratives = NarrativeSet.model_validate(
        {"key_success": "Categories struck at signature."}
    )
    assert narratives.key_success.text.startswith("Categories")
    assert narratives.key_challenge.text == ""
    assert narratives.key_challenge.claim_ids == []
    assert narratives.support_needed.text is None
    assert narratives.support_needed.needs_director_input is True


def test_support_from_handles_none_and_empty():
    from app.schema import NarrativeSet

    for value in (None, "", []):
        narratives = NarrativeSet.model_validate(
            {
                "key_success": "a",
                "key_challenge": "b",
                "support_needed": None,
                "support_from": value,
            }
        )
        assert narratives.support_from == []


# --- conflicts and gaps are different things ---------------------------------


def test_a_conflict_superseding_nothing_is_accepted_then_reclassified():
    """A conflict with nothing on the losing side is a gap wearing a disguise.

    The schema accepts it rather than failing the whole tool call — the
    reconcile node turns it into a gap, which is tested in test_graph.
    """
    from app.schema import Conflict

    conflict = Conflict(
        topic="unresolved thing",
        winning_claim_id="E1.1",
        superseded_claim_ids=[],
        rule_applied="other",
        note="nothing later addresses this",
    )
    assert conflict.superseded_claim_ids == []


def test_a_gap_records_what_raised_it_and_what_it_leaves_unverified():
    from app.schema import Gap

    gap = Gap(
        topic="Whether the thing happened",
        raised_by_claim_ids=["E5.7"],
        note="An expectation with nothing later confirming or denying it.",
        bears_on="first data exchange",
    )
    assert gap.raised_by_claim_ids == ["E5.7"]
    assert gap.bears_on


def test_reconciliation_carries_both_and_defaults_to_neither():
    from app.schema import Reconciliation

    result = Reconciliation(reconciled_position="As stated.")
    assert result.conflicts == [] and result.gaps == []
