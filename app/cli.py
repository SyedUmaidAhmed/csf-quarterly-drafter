"""Headless run: evidence in, proposed row out.

    python -m app.cli --data-dir data --quarter 2026-Q3

Stops at the review point and prints what would be put in front of the
director. It cannot stage anything, because staging is what happens after a
human has looked — there is no --yes flag and adding one would defeat the
purpose of the workflow.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from pathlib import Path

from .config import MissingAPIKey, Settings
from .graph.build import build_graph
from .inputs import load_inputs
from .llm import AnthropicClient
from .schema import DraftRow

BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli",
        description="Draft a quarterly CSF update from a folder of evidence.",
    )
    parser.add_argument("--data-dir", type=Path, help="folder holding objective.md and evidence/")
    parser.add_argument("--quarter", help="quarter being reported, e.g. 2026-Q3")
    parser.add_argument("--json", action="store_true", help="print the row as JSON and nothing else")
    return parser.parse_args(argv)


async def run(settings: Settings) -> tuple[DraftRow | None, dict]:
    inputs = load_inputs(settings)
    graph = build_graph(AnthropicClient(settings), settings)
    result = await graph.ainvoke(
        inputs,
        config={"configurable": {"thread_id": f"cli-{uuid.uuid4().hex[:8]}"}},
    )
    return result.get("row"), result


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    overrides = {}
    if args.data_dir:
        overrides["data_dir"] = args.data_dir
    if args.quarter:
        overrides["quarter"] = args.quarter
    settings = Settings(**overrides)

    try:
        settings.require_api_key()
    except MissingAPIKey as error:
        print(error, file=sys.stderr)
        return 2

    row, result = asyncio.run(run(settings))
    if row is None:
        print("no row was produced", file=sys.stderr)
        return 1

    if args.json:
        print(row.model_dump_json(indent=2))
    else:
        render(row, result)

    return 1 if result.get("issues") else 0


def render(row: DraftRow, result: dict) -> None:
    docs = {doc.doc_id: doc for doc in result.get("docs", [])}
    claims = {claim.claim_id: claim for claim in result.get("claims", [])}

    print()
    print(f"{BOLD}Proposed Quarterly_Updates row — {row.Objective_ID}, {row.Quarter}{RESET}")
    print(f"{DIM}Source: {row.Source} · not submitted · a proposal for the director{RESET}")
    print()

    for name, proposal in row.proposals().items():
        value = "— (needs director input)" if proposal.needs_director_input else proposal.value
        print(f"  {BOLD}{name}{RESET}: {value}")
        if proposal.rationale:
            print(f"    {DIM}{proposal.rationale}{RESET}")
        if proposal.claim_ids:
            print(f"    {DIM}evidence: {', '.join(proposal.claim_ids)}{RESET}")
    print()

    conflicts = result.get("conflicts", [])
    print(f"{BOLD}Conflicts in the evidence ({len(conflicts)}){RESET}")
    if not conflicts:
        print(f"  {DIM}none found{RESET}")
    for conflict in conflicts:
        superseded = ", ".join(conflict.superseded_claim_ids) or "nothing"
        print(f"  · {conflict.topic}")
        print(f"    {DIM}[{conflict.winning_claim_id}] supersedes {superseded}{RESET}")
        print(f"    {DIM}{conflict.note}{RESET}")
    print()

    print(f"{BOLD}Evidence read{RESET}")
    for doc in result.get("docs", []):
        count = sum(1 for c in claims.values() if c.doc_id == doc.doc_id)
        print(f"  {doc.doc_id}  {doc.date_label:<12} {doc.source_type:<18} {count} claims")
    print()

    cited = [c for c in claims.values() if c.citations]
    print(f"{BOLD}Claims{RESET} {DIM}({len(cited)} cited){RESET}")
    for claim in claims.values():
        where = ", ".join(f"{c.doc_id}#{c.start_block}" for c in claim.citations)
        print(f"  [{claim.claim_id}] {claim.text}")
        print(f"    {DIM}{where}{RESET}")
    print()

    issues = result.get("issues", [])
    if issues:
        print(f"{BOLD}Unresolved validation issues{RESET}")
        for issue in issues:
            print(f"  · {issue.field}: {issue.message}")
        print()
        print(f"{DIM}The director sees these alongside the draft.{RESET}")
    else:
        print(f"{DIM}Row is valid against the CSF schema. Nothing has been submitted.{RESET}")

    if docs:
        print()


if __name__ == "__main__":
    raise SystemExit(main())
