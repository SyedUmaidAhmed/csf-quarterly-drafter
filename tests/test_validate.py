from app import vocab
from app.schema import DraftRow, FieldProposal
from app.validate import advice, blocking, errors, repairable, validate_row


def make_row(**overrides) -> DraftRow:
    defaults = dict(
        Objective_ID="OBJ-TEST-01",
        Quarter="2026-Q3",
        Traffic_Light=FieldProposal(value="Amber", claim_ids=["C1"]),
        Progress_Percent=FieldProposal(value=30, claim_ids=["C1"]),
        Key_Success=FieldProposal(value="A thing happened.", claim_ids=["C1"]),
        Key_Challenge=FieldProposal(value="Another did not.", claim_ids=["C2"]),
        Support_Needed=FieldProposal(value=None, needs_director_input=True),
        Support_From=FieldProposal(value=["Finance"]),
    )
    return DraftRow(**{**defaults, **overrides})


def test_a_clean_row_passes():
    assert validate_row(make_row(), known_claim_ids={"C1", "C2"}) == []


def test_traffic_light_outside_vocabulary_is_rejected():
    issues = validate_row(make_row(Traffic_Light=FieldProposal(value="Orange", claim_ids=["C1"])))
    assert any(i.field == "Traffic_Light" for i in issues)


def test_progress_out_of_range_is_rejected():
    for bad in (-1, 101, 1000):
        issues = validate_row(make_row(Progress_Percent=FieldProposal(value=bad, claim_ids=["C1"])))
        assert any(i.field == "Progress_Percent" for i in issues), bad


def test_bool_progress_is_normalised_at_the_model_boundary():
    """pydantic coerces True to 1, so the validator never sees a bool here."""
    assert FieldProposal(value=True).value == 1


def test_validator_still_rejects_a_non_integer_progress():
    """Belt and braces for a proposal built without validation."""
    row = make_row()
    row.Progress_Percent = FieldProposal.model_construct(value="thirty", claim_ids=["C1"])
    issues = validate_row(row)
    assert any(i.field == "Progress_Percent" for i in issues)


def test_a_narrative_wider_than_the_column_is_advice_not_an_error():
    """The 200-character limit is the SharePoint column, not a rule of ours.

    A director may well want to submit their own longer wording and trim it.
    Telling them the column is narrow is useful; refusing the row is not.
    """
    long = "x" * (vocab.NARRATIVE_MAX_CHARS + 1)
    issues = validate_row(make_row(Key_Success=FieldProposal(value=long, claim_ids=["C1"])))

    assert any(i.field == "Key_Success" for i in issues)
    assert advice(issues), "it should be surfaced"
    assert not errors(issues), "and it should not block the row"
    assert not repairable(issues), "nor cost a retry"


def test_narrative_at_exactly_the_limit_passes():
    exact = "x" * vocab.NARRATIVE_MAX_CHARS
    issues = validate_row(
        make_row(Key_Success=FieldProposal(value=exact, claim_ids=["C1"])),
        known_claim_ids={"C1", "C2"},
    )
    assert issues == []


def test_support_from_outside_vocabulary_is_rejected():
    issues = validate_row(make_row(Support_From=FieldProposal(value=["Legal"])))
    assert any(i.field == "Support_From" for i in issues)


def test_wrong_source_is_a_blocking_issue():
    issues = validate_row(make_row(Source="Director"))
    assert any(i.field == "Source" for i in issues)
    assert blocking(issues), "the Source value is not something to retry into correctness"


def test_submitted_row_is_blocked():
    issues = validate_row(make_row(submitted=True))
    assert any(i.field == "submitted" for i in issues)
    assert blocking(issues)


def test_malformed_quarter_is_blocking():
    issues = validate_row(make_row(Quarter="Q3-2026"))
    assert any(i.field == "Quarter" for i in issues)
    assert blocking(issues)


def test_unknown_claim_ids_are_rejected():
    issues = validate_row(
        make_row(Traffic_Light=FieldProposal(value="Amber", claim_ids=["C99"])),
        known_claim_ids={"C1", "C2"},
    )
    assert any("C99" in i.message for i in issues)


def test_significant_claim_without_evidence_is_rejected():
    issues = validate_row(
        make_row(Traffic_Light=FieldProposal(value="Green", claim_ids=[])),
        known_claim_ids={"C1", "C2"},
    )
    assert any(i.field == "Traffic_Light" and "no supporting evidence" in i.message for i in issues)


def test_abstention_is_not_an_error():
    """A field the evidence cannot support should be left empty, not invented."""
    issues = validate_row(
        make_row(Traffic_Light=FieldProposal(value=None, needs_director_input=True)),
        known_claim_ids={"C1", "C2"},
    )
    assert not any(i.field == "Traffic_Light" for i in issues)


def test_missing_value_without_abstention_flag_is_an_error():
    issues = validate_row(make_row(Progress_Percent=FieldProposal(value=None)))
    assert any(i.field == "Progress_Percent" for i in issues)


# --- prose discipline --------------------------------------------------------
# General rules about how a rationale is written, not about any one objective.


def test_an_overlong_rationale_is_rejected_and_repairable():
    row = make_row(
        Traffic_Light=FieldProposal(value="Amber", claim_ids=["C1"], rationale="x" * 400)
    )
    issues = validate_row(row, known_claim_ids={"C1", "C2"})
    assert any(i.field == "Traffic_Light" and "rationale" in i.message for i in issues)
    assert repairable(issues)


def test_a_long_reasoning_is_fine_that_is_what_it_is_for():
    row = make_row(
        Traffic_Light=FieldProposal(
            value="Amber", claim_ids=["C1"], rationale="Short.", reasoning="x" * 1200
        )
    )
    assert validate_row(row, known_claim_ids={"C1", "C2"}) == []


def test_reasoning_has_no_upper_bound():
    """It sits behind a disclosure. Cutting a good argument to hit a number
    invented by the person who wrote the validator helps nobody."""
    row = make_row(
        Traffic_Light=FieldProposal(value="Amber", claim_ids=["C1"], reasoning="x" * 20000)
    )
    assert validate_row(row, known_claim_ids={"C1", "C2"}) == []


def test_a_bracket_holding_prose_is_rejected():
    """[prior submission context] looks like a citation and leads nowhere."""
    row = make_row(
        Traffic_Light=FieldProposal(
            value="Amber",
            claim_ids=["C1"],
            rationale="Down from last quarter [prior submission context].",
        )
    )
    issues = validate_row(row, known_claim_ids={"C1", "C2"})
    assert any("looks like a citation" in i.message for i in issues)


def test_a_bracket_holding_an_unknown_claim_id_is_rejected():
    row = make_row(
        Traffic_Light=FieldProposal(value="Amber", claim_ids=["C1"], rationale="Because [E9.9].")
    )
    issues = validate_row(row, known_claim_ids={"C1", "C2"})
    assert any("looks like a citation" in i.message for i in issues)


def test_brackets_holding_real_claim_ids_pass():
    row = make_row(
        Traffic_Light=FieldProposal(
            value="Amber",
            claim_ids=["E1.1"],
            rationale="Because of this [E1.1] and that [E1.1, E2.1].",
        )
    )
    assert validate_row(row, known_claim_ids={"C1", "C2", "E1.1", "E2.1"}) == []


def test_brackets_are_checked_in_reasoning_too():
    row = make_row(
        Traffic_Light=FieldProposal(
            value="Amber", claim_ids=["C1"], reasoning="Set out at length [see above]."
        )
    )
    issues = validate_row(row, known_claim_ids={"C1", "C2"})
    assert any("looks like a citation" in i.message for i in issues)


def test_a_figure_argued_for_must_be_the_figure_submitted():
    """Deriving 13% and submitting 15 leaves nobody able to tell which is wrong."""
    row = make_row(
        Progress_Percent=FieldProposal(
            value=15, claim_ids=["C1"], reasoning="Two fifths of one third, around 13%."
        )
    )
    issues = validate_row(row, known_claim_ids={"C1", "C2"})
    assert any(i.field == "Progress_Percent" and "argues for" in i.message for i in issues)


def test_a_rationale_stating_its_own_figure_passes():
    row = make_row(
        Progress_Percent=FieldProposal(
            value=15,
            claim_ids=["C1"],
            rationale="15% of the measure, down from 45% last quarter.",
        )
    )
    assert validate_row(row, known_claim_ids={"C1", "C2"}) == []


def test_prose_with_no_percentages_is_not_second_guessed():
    row = make_row(
        Progress_Percent=FieldProposal(
            value=15, claim_ids=["C1"], rationale="One of three agreements, partially scoped."
        )
    )
    assert validate_row(row, known_claim_ids={"C1", "C2"}) == []
