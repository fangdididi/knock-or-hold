# Routing rules (reference)

Apply in this order. The helper script implements exactly these rules.

## 1. Effective cost

| cost_of_delay | points |
|---|---|
| none | 0 |
| low | 1 |
| medium | 2 |
| high | 3 |
| severe | 4 |

- `source: untrusted` and not `verified`: capped at 1 (low).
- `source: unknown` and not `verified`: capped at 2 (medium).
- `owner` and `known`: as written.

## 2. Deadline pressure

| time left until `deadline` | points |
|---|---|
| overdue or <= 30 min | 3 |
| <= 4 h | 2 |
| <= 24 h | 1 |
| more, or no deadline | 0 |

## 3. Waiting

`nobody` 0, `person` 1, `many` 2.

**Score = cost + pressure + waiting** (0-9).

**Urgent** when any of: score >= `knock_threshold` (default 6); cost is severe; deadline is before the next brief and cost >= medium.

## 4. First route

- `act`: SILENT only if all hold: class in `standing_permissions`, `reversible: true`, `external: false`, `undo` written, source not untrusted. Otherwise ASK when urgent, BRIEF_ASK when not.
- `fyi` with cost 0, pressure 0 and nobody waiting: DROP.
- everything else: KNOCK when urgent, BRIEF when not.

## 5. Timing for KNOCK and ASK

- Inside quiet hours: wait until they end, unless the item is severe, its category is in `quiet_bypass`, and it has no deadline or its deadline is before the quiet hours end.
- Inside a focus block (outside quiet hours): wait until the block ends, unless severe or the deadline is no later than 15 minutes after the block ends.

## 6. Budget

- Items with the same send time form one bundle = one knock.
- Each local date has `knock_budget_per_day` knocks; today starts at `knocks_sent_today`.
- A bundle on a date whose budget is spent keeps only severe items (overdraft). Others become BRIEF (or BRIEF_ASK for actions) in the next brief after that time.

## 7. Words

- KNOCK and ASK need `why_now`.
- ASK, BRIEF_ASK and decision KNOCKs need 2-3 chips; each chip 1-3 words (CJK: about 4 characters per word) and <= 24 characters; no duplicates.
- Approvals need a chip with `safe: true` and `default_if_silent` set to a safe chip.
- Warnings: shouting words (URGENT, ASAP, !!, 紧急), question-only summary, summary > 160 characters.

## 8. Feedback

| feedback | meaning | effect in calibrate |
|---|---|---|
| needed_me | the knock was right | counts toward precision |
| could_wait | the knock could have been in the brief | lowers precision |
| too_late | a non-knock should have been a knock | suggest threshold - 1 |
| wrong_silent | a silent action was unwelcome | suggest removing that class |
