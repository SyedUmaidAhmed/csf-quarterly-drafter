# Screen recording — running order

Seven minutes maximum, one take, voice over a screen recording. Not part of the
artifact; delete before packaging if you would rather it were not.

**Say your name and who you are at the start.** The written work is assessed
with names masked, so the recording is where you identify yourself.

---

## Before you hit record

- `export ANTHROPIC_API_KEY=...`
- A terminal, and a browser with a completed run already open in a second tab.
  **Do a real run beforehand** — do not spend ninety seconds of a seven-minute
  recording watching a spinner.
- Have the second evidence folder ready for the rerun.

---

## 0:00 — Open on the CLI (45 seconds)

```bash
python -m app.cli --data-dir data --quarter 2026-Q3
```

Let it run while you say what it is: evidence folder in, proposed row out,
stops before anything is submitted. This establishes rerunnability in the first
minute rather than claiming it in the last.

## 0:45 — What I built, and why (1:30)

Switch to the completed run in the browser. Scroll top to bottom once, slowly.

The argument, in this order:

- The hard part is not summarising five documents. It is that they contradict
  each other, and the most recent account is the one that matters.
- So each document is read separately — a reader shown all five at once
  harmonises them, and the disagreement is the information.
- Reconciliation is its own pass with two rules written down: later supersedes
  earlier, and a participant's own report supersedes an outbound document.
- Point at the conflicts panel. Read one aloud. Note that one of the superseded
  claims is in a report that has already gone to a donor.
- Point at the traffic light. Nowhere in the evidence does anyone say what it
  should be, and the previous quarter's value is on screen deliberately so the
  director can see it was not carried forward.

Click one evidence chip. Land on the highlighted block.

> "That citation is a block index the API resolved against exactly what I sent
> it. The model did not write that quote and cannot point at a sentence that
> is not in the document."

## 2:15 — What I deliberately did not build (1:00)

- No Graph or SharePoint integration — the brief says treat it as existing, and
  it does exist.
- No model routing, no identity, no observability infrastructure. That is the
  corporate architecture engagement, and rebuilding it inside every workflow is
  how an institute ends up with six of them.
- **No submit button.** Not deferred — absent. There is no code path that
  writes to a system of record.
- No offline mode. A canned draft that looks like a real one is worse than not
  starting.
- Empty fields where the evidence supports nothing. Show `Support_Needed`.
  > "The previous quarter said 'none at present'. Nothing in this quarter's
  > evidence says that, so the workflow leaves it for the director rather than
  > writing a sentence nobody said."

## 3:15 — Biggest adoption risk (1:15)

The risk is not that they refuse to use it. It is that they accept it without
reading it, which turns a reporting problem into an accountability problem.

What follows from that:

- Citations on everything significant, one click from the sentence.
- The prior quarter shown alongside, so an unchanged answer is visibly a
  choice.
- Empty fields rather than plausible ones.
- Corrections logged with the evidence that was on screen — a correction made
  after opening the source means something different from one made without.

Then the honest version: a director burned once by a wrong traffic light stops
trusting the tool permanently, and no amount of citation design recovers that.
Which is why the first pilot is two directors sitting next to me, not a rollout.

Demonstrate an edit. Change something, show the badge and the correction.

## 4:30 — Reusable versus corporate (1:00)

**Reusable across ILRI workflows:** the evidence loader and block splitter; the
read-with-citations-then-structure pattern, which is forced by the API and
turns out to be the right shape anyway; validating against controlled
vocabularies as deterministic code rather than trusting the model; the
correction log; and the habit of omitting a calculated field from the model
entirely so it cannot be set by accident.

**Belongs to corporate:** model routing, identity and consent, observability,
connectors, and the staging write contract — which is a joint design item, not
something either side settles alone.

Mention that `llm.py` is two methods wide precisely so that boundary is a file,
not a refactor.

## 5:30 — How I used AI (45 seconds)

Say what you actually did. Be specific and unembarrassed. Cover:

- What you used it for — reading the pack, drafting code, writing tests, the
  prompts themselves.
- **One thing you rejected.** The strongest thing you can say. For instance:
  the first design had the model emit verbatim quotes that were then checked in
  Python; reading the Citations API documentation showed that the API returns
  guaranteed-valid pointers, which makes fabrication impossible rather than
  detectable — so that design went in the bin.
- What you verified yourself: that the citations resolve to real blocks, that
  the interrupt actually stops the graph, that corrections are not double-logged
  when the node restarts on resume.

## 6:15 — The rerun (45 seconds)

```bash
python -m app.cli --data-dir /path/to/other-evidence --quarter 2026-Q4
```

Different folder, different quarter, no code change, different answer. Say
plainly that this is the state it is in for the final session.

Stop there. Do not summarise.

---

## Notes

- Do not apologise for the styling. It is plain on purpose and the brief says
  visual polish is not assessed.
- If something breaks live, say what you think it is and carry on. One take is
  expected.
- If you go over seven minutes, cut section 4:30 down to two sentences. It is
  the one an assessor can also read in the note.
