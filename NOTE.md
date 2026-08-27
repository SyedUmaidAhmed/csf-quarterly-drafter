# One page

## 1. What I would do next to make this genuinely used

Nothing here is adopted until a real director submits a real update from it, so
the next step is two directors, two objectives each, for one quarter — enough
to see a pattern, few enough to sit with each of them while they use it.

Three changes I would expect to make:

**Take it to them.** A tool you have to remember to visit loses to doing it
from memory at the deadline. The draft should arrive in Outlook when the
submission window opens, with the review link in it.

**Make the first screen the disagreements, not the fields.** The draft is
worth less to a director than the two sentences telling them their own
programme moved. That is the thing they cannot get from anywhere else, and in
the supplied evidence it includes a claim in a report already sent to a donor
that is no longer true.

**Find out what an unhelpful draft looks like.** I know what this does on one
objective whose evidence is unusually rich in contradictions. I do not know
what it does on a quiet objective where nothing happened, and that is probably
the more common case.

The risk I would watch for is not rejection. It is a director accepting the
draft without reading it, which converts a reporting problem into an
accountability problem. Anything that makes accepting easier than checking is
a change in the wrong direction, and I would rather the tool stayed slightly
effortful than became a button.

## 2. What I would measure

| | |
|---|---|
| **Adoption** | Share of updates submitted with `Source = Substrate-Drafted`. The only measure that cannot be argued with. |
| **Editing effort** | Keystroke-weighted edit distance per field. Surface-similarity metrics correlate poorly with the effort a human actually spends; keystroke distance tracks it. |
| **Time to submit** | Against each director's own baseline, not a global average. |
| **Citation click-through** | Whether directors open the evidence at all. If nobody does, the citations are decoration and the trust argument is wrong. |
| **Reversal rate** | Fields changed and then changed back after opening the source. High reversal means the draft was right and the presentation was not. |
| **Abstention accuracy** | How often a field left empty gets filled, versus how often a filled field gets emptied. The second is the expensive error. |

Calibration matters here: published acceptance rates for AI-generated
suggestions in production review workflows run in the single digits to low
teens. A first pilot at 20% field-level acceptance is a good result, and
promising more would set the pilot up to be judged a failure.

The measure I would refuse is director satisfaction on its own. It moves with
how the quarter went.

## 3. What I would need

**From ILRI:** one director who will opt in and be interrupted while they use
it; the connector pointed at their real material; the objective and success
measure as actually recorded, including where those are stale; and a decision
on who owns the controlled vocabularies when someone wants to add a value. The
last one sounds administrative and is not — the CSF design says drift degrades
everything downstream, and this workflow validates against those lists on every
run.

**From the Corporate AI Architecture engagement:** model and provider routing;
identity, consent and the record of who granted read access to what;
observability and shared evaluation infrastructure; and the contract for
staging a row into SharePoint. That last one is the joint design item — this
workflow writes a file today because the write path is not mine to invent, and
whatever the contract turns out to be, the human gate has to survive it.

What I would *not* ask for is a platform before there is a workflow worth
platforming.

## 4. How corrections should improve future drafts — and what must never be learnt

Every correction is logged with the proposed value, the director's value, and
the claim ids that were on screen when they made it. That last part is the
useful part: a change made after opening the evidence means something
different from one made without.

**Safe to learn, with a human approving the change:** house style, length,
register, the shape of a well-formed `Key_Challenge`, and recurring
`Support_From` routing where the same function keeps being named.

**Must never be learnt automatically:** traffic-light calibration, progress
figures, or anything that shifts how the system characterises status.

The reason is directional. Corrections to a status are not random noise around
a true value — they skew optimistic, because a director editing their own
report is the person with the most reason to soften it. A system that learns
from the aggregate of those edits learns to pre-soften, each quarter starting
from a slightly kinder position than the last. No individual step looks wrong,
nothing alerts, and the institute ends up with a reporting layer that has
quietly learnt to tell it what it wants to hear. That is precisely the failure
the CSF framework exists to prevent, so the system must not be able to teach
itself into it — not as a policy, but structurally.

The safe version of the same idea: use corrections as **evaluation** rather
than training. A field the directors keep correcting in the same direction is a
prompt to review, made by a person who can ask why.

## 5. What I would challenge in the design

**`Progress_Percent` is doing two incompatible jobs.** Activity completed and
success-measure attainment are different numbers, and this objective is the
case that separates them: substantial work has been done while the measure has
receded. Reported as one figure, either reading misleads. I would split it, or
define which one it is and say so in the field description.

**There is nowhere to say the success measure is now wrong.** The most
consequential thing in the supplied evidence is a question — whether the
objective should be re-baselined — and the schema has no way to carry it. It
will be resolved in a meeting, in prose, and the structured record will show a
quarter that failed rather than a target that changed. Re-baselining is a real
workflow with a real approval path and it has no home in the data model. This
matters more than anything else on this page: a reporting system that cannot
represent a changed target will accumulate objectives that are failing on
paper and settled in practice, and directors will learn that the structured
row is not where the truth goes.

**"AI services never write to the system of record" needs one clarification.**
Staging *is* a write, to somewhere. The principle is about who is accountable
for the content, not about which system holds the bytes, and the staging
contract should say so explicitly before someone implements a staging table
that a Power Automate flow then promotes on a schedule.

**One thing I would not change.** Keeping the 2026 design small and replaceable
is right, and the fact that this workflow could be rebuilt on a different stack
in a week is a feature of that decision, not an accident of mine.
