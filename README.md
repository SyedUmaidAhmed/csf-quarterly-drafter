# CSF quarterly update drafter

Prepares a director's quarterly CSF update from their own Microsoft 365
material, and puts it in front of them to check and change.

It proposes. It does not decide, and it does not submit. There is no code path
in this project that writes to a system of record.

---

## Running it

Needs Python 3.10 or later and an Anthropic API key.

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

```bash
cp .env.example .env    # then add your ANTHROPIC_API_KEY
```

Headless, prints the proposed row and what it was drawn from:

```bash
.venv/bin/python -m app.cli --data-dir data --quarter 2026-Q3
```

The review interface, which is the part a director would use:

```bash
.venv/bin/uvicorn app.main:app
```

Then open <http://127.0.0.1:8000>.

Tests run without a key:

```bash
.venv/bin/python -m pytest
```

There is no offline or demo mode. A stubbed run would let the interface be
demonstrated without a model ever being called, and a canned draft that looks
like a real one is a worse failure than not starting.

---

## Putting evidence in

The workspace at `/` is a numbered sequence: connect a model, check the
objective, add the evidence, draft it. Evidence can be pasted in, uploaded,
edited in place or removed, and the objective — including the success measure
the traffic light is judged against — is editable there too.

Everything is stored as plain markdown in the data folder, so dropping files in
by hand works identically. The interface is a convenience over the same
contract, not a replacement for it.

## Running it against different evidence

`data/` is the whole input surface:

```
data/
  objective.md        one Level2_Objectives row, as a | Field | Value | table
  prior_update.md     the last submitted Quarterly_Updates row (optional)
  evidence/*.md       any number of documents, any names
```

Point it somewhere else and it runs unchanged:

```bash
.venv/bin/python -m app.cli --data-dir /path/to/other-evidence
```

Nothing about the supplied pack is hard-coded — not the filenames, not the
people, not the countries, not the subject matter. Documents are ordered by the
date in their heading, and undated ones sort last. Ordering matters, because
one of the reconciliation rules turns on which document came later.

In production this material arrives through the approved Microsoft 365
connectors. That layer is assumed to exist; this project does not build it.

---

## How it works

For a step-by-step trace — which prompt fires, what is sent to the model, what
shape comes back, and a state diagram — see [WALKTHROUGH.md](WALKTHROUGH.md).


```
load ─▶ read each document separately, in parallel
          │
          ▼
      reconcile ─▶ assess ─▶ compose ─▶ validate
                      ▲         ▲          │
                      └─ repair ┴─ repair ─┤  back to whichever pass produced
                        (status)(narrative)│  the field that failed
                                           ▼
                                        REVIEW  ← the graph stops here
                                           │
                                           ▼
                                 apply corrections ─▶ stage
```

Progress is streamed as server-sent events while this runs, so the minute it
takes shows the sequence rather than a blank page.

**Format is settled by a model, not by rules about em dashes.** The first
stage asks what each document is, when it was written, and where its parts
divide — returning *line ranges*, never text. The lines are then sliced from
the file, so the model exercises judgment about boundaries while every word a
director reads still comes verbatim from the document. Cached by content hash,
so an unchanged document costs nothing on a rerun and adding one document to a
folder of ten costs one call.

Markdown-shaped heuristics remain as a fallback for when there is no model and
no cache — a test, or the CLI without a key. They are right about markdown,
which is a grammar. They were also, before this stage existed, quietly assuming
that dates read "14 May 2026" and chat lines start `**Name** · 11:02`. Those are
facts about one folder, not about evidence in general.

### What stays deterministic, on purpose

Not everything should be a model's call:

| Deterministic | Why |
|---|---|
| The path check on uploaded and edited filenames | A security control. A model deciding whether a path escapes the evidence folder is a vulnerability, not a feature. |
| The controlled vocabularies | They *are* the schema. Asking a model whether a value is valid is how drift gets in. |
| Claim-id and bracket checking | Validates ids this code generated, in a convention this code defines. Not user data. |
| Character limits and the integer range | Arithmetic. |

**Each document is read on its own.** A reader shown all five at once tends to
harmonise them into one tidy account, and the disagreements between documents
are the substance of this problem rather than noise. Reconciliation is a
separate pass with rules written down.

**Citations come from the API, not from the model.** Evidence is sent as
Anthropic *custom content documents* with citations enabled, so each citation
is a block index the API resolved against what we sent. A citation cannot point
at a sentence nobody wrote. Asking a model to quote and then checking the quote
only catches fabrication after the fact.

Custom content rather than plain text because plain text is auto-chunked into
sentences, which cuts `| Field | Value |` rows and `**Name** · 11:03` chat
lines in half. We split documents into blocks ourselves — one per table row,
chat message, list item, blockquote paragraph — so a citation lands on exactly
one thing and the review page can highlight it.

**Reading and structuring are separate passes** because the API rejects
citations and structured outputs in the same request. The reading pass has
documents attached and returns cited prose; the reasoning passes have no
documents and return validated objects.

**The graph stops at a real `interrupt()`.** Not a UI convention — the workflow
cannot proceed past review without a human resuming it. The state is
checkpointed to SQLite, so a director can close the tab and come back.

**Conflicts and gaps are different things.** A conflict is two accounts that
cannot both be true. A gap is one account and then silence — an expectation
nobody confirms, a question with no recorded answer, a component of the success
measure nothing reports on. Silence is neither success nor failure, so a gap is
surfaced and left uncredited rather than being scored either way. A "conflict"
that supersedes nothing is reclassified as a gap in code, so the distinction
does not depend on the model observing it.

**Validation is deterministic Python**, not a model checking itself: closed
vocabularies, the 200-character column widths, the integer range, every cited
claim id resolving, every square bracket in prose resolving to a real claim,
and the submitted progress figure appearing in the reasoning that argues for
it. A repairable problem goes back **to the pass that produced it** — a bad
traffic-light rationale returns to assess, a bad narrative to compose — because
sending everything to the composer means the fields it does not produce can
never be repaired. Twice, then it goes to the director rather than being
retried until it disappears.

**Confidence is computed, not self-reported.** It comes from how many distinct
documents stand behind a value and whether any of them lost a reconciliation.
A model's own confidence came out identical on every field, including one
resting on three documents and one resting on none, and a badge that never
varies is worse than no badge because it looks like information.

### Two schema details worth knowing

`Source` is `Substrate-Drafted`, the value the CSF design defines for a row
originating from the Personal Layer.

`Trend_vs_Prior_Quarter` is calculated downstream in Power BI. It is not merely
left unset — there is no such field on the model, so it cannot be set by
accident, and a test asserts it never appears in output.

---

## What the director sees

- **What changed since last quarter** — the prior traffic light and figure
  against the proposal, at the top, because it is the first thing anyone asks.
- **Where the evidence disagrees with itself** — what was claimed, what
  superseded it, and which rule was applied, above the draft rather than
  buried in it.
- **Where the evidence goes silent** — a separate panel from the conflicts,
  each entry saying what is unresolved and what would settle it.
- **A citation on every significant field**, clickable through to the source
  with the cited block highlighted, and a confidence badge that says why
  ("three independent documents agree", "a single source").
- **A short rationale, with the full argument behind a disclosure** — the first
  is read while scanning before a deadline, the second when someone wants to
  disagree.
- **Empty fields where the evidence supports nothing.** A field the director
  fills in is correct; a plausible sentence nobody said is not.
- **Every value editable**, including the traffic light. Edits are recorded
  with the evidence that was on screen at the time.

Nothing is written anywhere until the director stages the row, and staging
writes a file for them to submit themselves.

---

## Layout

```
app/
  vocab.py        controlled vocabularies, transcribed from the CSF extract §3
  schema.py       evidence / findings / draft, as three separate layers
  evidence.py     the loader and the block splitter
  citations.py    document blocks out, resolved citations back
  provenance.py   confidence derived from citation structure
  validate.py     deterministic schema checks
  store.py        adding, editing and removing evidence from the interface
  progress.py     the run registry and the event stream
  llm.py          the two model calls this workflow makes
  prompts/*.md    prompts as files, so they can be edited without touching code
  graph/          state, nodes, wiring
  main.py         FastAPI routes
  cli.py          headless run
tests/            177 tests, no API key needed
tests/test_live.py  one real run: pytest -m live
```

The live test asserts that the workflow surfaces the contradictions and does
not inherit the previous quarter's position. It deliberately does **not** assert
a particular traffic light — that is the model's judgment and the director's to
check, and pinning it would only test that the model agrees with whoever wrote
the test.

---

## What this does not do

No Microsoft Graph or SharePoint integration; no authentication; no model
routing or provider abstraction; no observability beyond a run log; no
submission path; one objective at a time. Several of those belong to the
corporate AI architecture engagement rather than here, and building them inside
this workflow would duplicate it.
