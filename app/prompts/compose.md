You are writing the narrative fields of a director's quarterly update. The
director will read every word and change what they disagree with, so write what
the evidence supports and nothing beyond it.

## The objective

$objective

**Success measure:** $success_measure

## What the evidence supports

$reconciled_position

## The proposed status

Traffic light: $traffic_light — $traffic_light_rationale

Progress: $progress_percent% — $progress_rationale

## Conflicts found in the evidence

$conflicts

## Unresolved by the evidence

$gaps

## The claims

$claims

$repair_note

## What to produce

Three narrative fields and one choice field. **Every narrative field is limited
to $max_chars characters** — that is the SharePoint column width, not a style
preference, and a longer value is rejected. Write tight.

**key_success** — what genuinely went well this quarter. State it at its actual
size. A success that turned out smaller than expected is still a success, but
reporting it at the size it was originally hoped to be is how a report becomes
untrue. If the evidence shows a qualification, the qualification belongs in the
sentence.

**key_challenge** — the thing most likely to stop this objective being met. One
challenge, the biggest, not a list.

**support_needed** — what the director needs from elsewhere in the institute.
If the evidence does not show a specific, nameable need, set `text` to null and
`needs_director_input` to true. Do not write "none at present" and do not
invent a plausible-sounding ask. An empty field the director fills in is
correct; a fabricated one is not.

**support_from** — zero or more of: ILT, BDU, Finance, HR, IDDT,
Communications, Other. Name the function when the evidence points at one.
When no specific function fits, return `["Other"]` — never leave the list empty.

## Write for one fast reading

These fields are skim-read in a steering meeting by someone who will not go
back over a sentence. Two habits cause most misreadings:

**Do not put a colon after a quantity unless what follows is the thing you
counted.** "Covers two of five categories: A, B and C were struck" invites the
reader to take A, B and C as the two. If the clause after the colon is not an
enumeration of the number before it, use a full stop and start again.

**Put the qualification in the same clause as the claim.** A success stated
plainly in one sentence and qualified in the next will be quoted without the
second sentence.

## The shape

Each of the three narrative fields is an **object, not a string**. The wording
alone is not enough — a value with no claim ids attached cannot be shown to the
director with its evidence, and it will be sent back:

    "key_success": {
      "text": "the sentence, 200 characters or fewer, or null",
      "claim_ids": ["E3.1", "E4.2"],
      "needs_director_input": false
    }

`support_from` is a list of strings, even when it holds one value, and
`["Other"]` when the evidence points at no particular function.

Cite only claim ids that appear above. Write in the register of the previous
quarter's update: plain, declarative, no marketing.
