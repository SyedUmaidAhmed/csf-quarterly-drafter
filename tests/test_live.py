"""One end-to-end run against the real evidence and the real model.

    pytest -m live

Skipped without ANTHROPIC_API_KEY, so `pytest` on a clean checkout stays green
and fast.

These assertions are deliberately about **process, not answer**. They check
that the workflow surfaces the disagreements in the evidence and refuses to
inherit last quarter's position — not that it lands on a particular traffic
light. Pinning the expected answer would be marking my own homework: I would be
testing that the model agrees with what I concluded when I read the pack, which
tells nobody anything about whether the reasoning is sound.

The one exception is the schema, which is not a matter of judgment.
"""

from __future__ import annotations

import os

import pytest

from app.config import Settings
from app.graph.build import build_graph
from app.inputs import load_inputs
from app.llm import AnthropicClient
from app.validate import validate_row

pytestmark = [
    pytest.mark.live,
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not os.environ.get("ANTHROPIC_API_KEY"), reason="needs ANTHROPIC_API_KEY"
    ),
]


@pytest.fixture(scope="module")
def settings(tmp_path_factory):
    return Settings(runs_dir=tmp_path_factory.mktemp("runs"), quarter="2026-Q3")


@pytest.fixture(scope="module")
async def result(settings):
    """One real run, shared across the assertions below. Costs a few cents."""
    graph = build_graph(AnthropicClient(settings), settings)
    return await graph.ainvoke(
        load_inputs(settings), config={"configurable": {"thread_id": "live-test"}}
    )


# --- the schema is not a matter of judgment ---------------------------------


async def test_row_is_schema_valid(result):
    known = {claim.claim_id for claim in result["claims"]}
    assert validate_row(result["row"], known_claim_ids=known) == []


async def test_source_and_omitted_field(result):
    dumped = result["row"].model_dump()
    assert dumped["Source"] == "Substrate-Drafted"
    assert dumped["submitted"] is False
    assert "Trend_vs_Prior_Quarter" not in dumped


async def test_narratives_respect_the_column_width(result):
    for field in ("Key_Success", "Key_Challenge", "Support_Needed"):
        value = getattr(result["row"], field).value
        assert value is None or len(value) <= 200, field


# --- every document was read and cited --------------------------------------


async def test_every_document_produced_cited_claims(result):
    by_doc = {doc.doc_id: 0 for doc in result["docs"]}
    for claim in result["claims"]:
        if claim.citations:
            by_doc[claim.doc_id] = by_doc.get(claim.doc_id, 0) + 1
    assert all(count > 0 for count in by_doc.values()), by_doc


async def test_citations_point_at_real_blocks(result):
    """The API guarantees this; the test proves our block bookkeeping matches."""
    docs = {doc.doc_id: doc for doc in result["docs"]}
    for claim in result["claims"]:
        for citation in claim.citations:
            for index in citation.block_indices:
                docs[citation.doc_id].block(index)  # raises if out of range
            assert citation.cited_text


async def test_significant_fields_carry_evidence(result):
    for field in ("Traffic_Light", "Progress_Percent", "Key_Success", "Key_Challenge"):
        proposal = getattr(result["row"], field)
        if proposal.value is not None:
            assert proposal.claim_ids, f"{field} was proposed with no evidence"


# --- the workflow reasoned rather than copied -------------------------------


async def test_the_disagreements_in_the_evidence_are_surfaced(result):
    """The pack contains genuine contradictions. Finding none means not reading.

    Which conflicts are found, and how they are resolved, is the model's
    judgment and the director's to check. That at least one is found is not a
    judgment call — the evidence plainly disagrees with itself.
    """
    assert result["conflicts"], "no conflicts found in evidence that contradicts itself"


async def test_conflicts_name_both_sides_and_a_rule(result):
    known = {claim.claim_id for claim in result["claims"]}
    for conflict in result["conflicts"]:
        assert conflict.winning_claim_id in known
        assert conflict.superseded_claim_ids, f"{conflict.topic} supersedes nothing"
        assert set(conflict.superseded_claim_ids) <= known
        assert conflict.note.strip()


async def test_the_reconciled_position_cites_its_claims(result):
    position = result["reconciled_position"]
    assert position.strip()
    assert any(claim.claim_id in position for claim in result["claims"])


async def test_the_rationale_argues_rather_than_asserts(result):
    """A director has to be able to disagree from the rationale alone."""
    rationale = result["row"].Traffic_Light.rationale
    assert len(rationale) > 40, rationale


async def test_the_prior_quarter_was_not_simply_copied(result):
    """The prior row said Green at 45%.

    Reaching the identical pair from evidence that has moved on would suggest
    the previous answer was carried forward rather than re-derived. Either
    value alone is defensible; both together, unchanged, is the failure mode
    this workflow exists to prevent.
    """
    row = result["row"]
    assert not (row.Traffic_Light.value == "Green" and row.Progress_Percent.value == 45)
