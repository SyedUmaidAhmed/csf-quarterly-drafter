"""Confidence is computed from the evidence, not self-reported.

A model's own confidence came out identical on every field, including one
resting on three documents and one resting on nothing. These rules are about
citation structure, so they behave the same on evidence nobody has seen.
"""

from app.provenance import assess, superseded_claim_ids
from app.schema import Citation, Claim, Conflict


def claim(cid, doc):
    return Claim(
        claim_id=cid,
        doc_id=doc,
        text="something",
        citations=[Citation(doc_id=doc, start_block=0, end_block=1, cited_text="x")],
    )


CLAIMS = {
    "E1.1": claim("E1.1", "E1"),
    "E2.1": claim("E2.1", "E2"),
    "E3.1": claim("E3.1", "E3"),
    "E1.2": claim("E1.2", "E1"),
}


def conflict(winner, superseded):
    return Conflict(
        topic="t",
        winning_claim_id=winner,
        superseded_claim_ids=superseded,
        rule_applied="later_supersedes_earlier",
        note="n",
    )


def test_three_documents_is_high():
    level, count, why = assess(["E1.1", "E2.1", "E3.1"], CLAIMS, [])
    assert level == "high"
    assert count == 3
    assert "3 independent documents" in why


def test_two_documents_is_high():
    level, count, why = assess(["E1.1", "E2.1"], CLAIMS, [])
    assert (level, count) == ("high", 2)
    assert "two independent" in why


def test_two_claims_from_one_document_is_only_one_source():
    level, count, why = assess(["E1.1", "E1.2"], CLAIMS, [])
    assert (level, count) == ("medium", 1)
    assert "a single source" in why


def test_no_evidence_is_low():
    level, count, why = assess([], CLAIMS, [])
    assert (level, count) == ("low", 0)
    assert "no evidence" in why


def test_abstention_is_low_and_says_so():
    level, _, why = assess(["E1.1", "E2.1"], CLAIMS, [], abstained=True)
    assert level == "low"
    assert "no value proposed" in why


def test_resting_on_superseded_evidence_is_low_however_many_sources():
    conflicts = [conflict("E3.1", ["E1.1"])]
    level, count, why = assess(["E1.1", "E2.1", "E3.1"], CLAIMS, conflicts)
    assert level == "low", "corroboration cannot rescue an overturned claim"
    assert count == 0
    assert "E1.1" in why and "superseded" in why


def test_unknown_claim_ids_do_not_count_as_sources():
    level, count, _ = assess(["E9.9"], CLAIMS, [])
    assert (level, count) == ("low", 0)


def test_superseded_ids_are_collected_across_conflicts():
    conflicts = [conflict("E3.1", ["E1.1"]), conflict("E2.1", ["E1.2"])]
    assert superseded_claim_ids(conflicts) == {"E1.1", "E1.2"}


def test_confidence_differentiates_across_fields():
    """The failure this replaces: the same badge on every field."""
    levels = {
        assess(["E1.1", "E2.1", "E3.1"], CLAIMS, [])[0],
        assess(["E1.1"], CLAIMS, [])[0],
        assess([], CLAIMS, [])[0],
    }
    assert len(levels) == 3, "three different evidence situations, three answers"
