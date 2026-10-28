# knock-or-hold

**English** | [简体中文](README.zh-CN.md)

An agent skill that decides **when and how a proactive agent should reach its human**: knock now, ask for approval, save it for the next brief, do it quietly and log it, or let it go.

Background agents watch mail, calendars, chats and long-running jobs. Each day they find dozens of things they *could* mention. Mention everything and the person stops reading. Mention too little and the failed payment waits until evening. Act too freely and something gets sent, paid or deleted that nobody approved. knock-or-hold replaces that guesswork with written rules, a small scoring model, a daily interruption budget and a feedback loop.

## Why

- **Interruptions cost more than the message.** Getting back into deep work takes time, and a break in the middle of a task hurts more than one at a natural pause. So the skill holds knocks for quiet hours and focus blocks, and bundles them.
- **Noisy alerts train people to ignore alerts.** A small daily budget and a knock threshold keep each knock worth reading. Feedback ("could have waited", "reached me too late") moves the threshold.
- **Unattended actions need approval tiers.** Only small, undoable, private actions the person pre-approved happen silently. Everything that other people will see or that can't be undone is asked first, with the exact action shown, and silence never counts as yes.
- **Outside content is not the boss.** A stranger's "URGENT, verify now" can't buy a knock, and instructions found inside content are reported, never followed.

## Features

- **Knock card**: timezone, quiet hours (can cross midnight), focus blocks by weekday, brief times, daily knock budget, threshold, standing permissions, quiet-hours bypass categories.
- **Moment cards**: kind (`fyi`/`alert`/`decision`/`act`), source trust, cost of delay, deadline, who is waiting, exact action with reversibility, visibility and undo.
- **Six routes** with written reasons: `KNOCK`, `ASK`, `BRIEF`, `BRIEF_ASK`, `SILENT`, `DROP`.
- **Scoring**: cost (0-4) + deadline pressure (0-3) + waiting (0-2); severe items and costly items whose deadline beats the next brief are urgent regardless.
- **Trust caps**: untrusted sources capped at low cost, unknown senders at medium, until verified.
- **Timing**: quiet-hours hold with a severe-only bypass list; focus-block hold with a 15-minute deadline margin.
- **Bundling and budget**: knocks at the same time leave as one message; per-day budget with severe-only overdraft.
- **Reply chips**: 2-3 one-tap choices, at most 3 words / 24 characters (CJK aware), one marked safe, and no answer = the safe choice.
- **Plan check**: re-verifies every hard rule on a hand-edited plan, exit code 1 on any breach.
- **Render**: knock messages plus a brief with "Needs your OK", "Worth knowing", "Done quietly (each can be undone)".
- **Calibrate**: precision of knocks, late items, unwelcome silent actions, and a suggested threshold.
- Standard-library Python 3.9+, no network access, 116 tests.

## Installation

The skill is the folder. Put it where your agent looks for skills:

| agent | location |
|---|---|
| Claude Code | `~/.claude/skills/knock-or-hold/` (or `.claude/skills/` inside a project) |
| Codex CLI | `~/.codex/skills/knock-or-hold/` |
| Cursor | `.cursor/skills/knock-or-hold/` in the project, or reference `SKILL.md` from a rule |
| Other agents (Gemini CLI, OpenClaw, Windsurf, custom) | anywhere; tell the agent to read `SKILL.md` |

```bash
git clone https://github.com/fangdididi/knock-or-hold ~/.claude/skills/knock-or-hold
```

## Usage

Tell the agent something like: "You watch my mail and calendar while I work. Use knock-or-hold: quiet hours 22:30-07:30, focus 9-11:30 on weekdays, at most 3 pings a day, you may archive newsletters without asking." The agent writes the knock card, then routes what it finds. The script also works directly:

```bash
python3 scripts/knock.py init mysetup                       # starter card.json + moments.json
python3 scripts/knock.py route card.json moments.json --out plan.json --ledger ledger.jsonl
python3 scripts/knock.py check plan.json                    # PASS / rule errors, exit code
python3 scripts/knock.py render plan.json                   # knock message(s) + next brief
python3 scripts/knock.py lint moments.json                  # chips and wording only
python3 scripts/knock.py calibrate ledger.jsonl --threshold 6
python3 -m unittest discover -s tests                       # run the tests
```

Exit codes: 0 ok, 1 rule or lint errors, 2 invalid input.

## Example

Wednesday 09:40, the owner is in a focus block until 11:30:

```text
$ python3 scripts/knock.py route examples/card.json examples/moments.json --now 2026-10-28T09:40:00+08:00
KNOCK      m1   score=5 at=11:30  held for focus block until 11:30
SILENT     m2   score=0 at=09:40  standing permission 'archive_newsletter', reversible, private
BRIEF_ASK  m3   score=3 at=17:30  needs approval: not reversible
BRIEF      m4   score=1 at=17:30  untrusted source cannot raise its own urgency: cost capped at low
KNOCK      m5   score=5 at=11:30  held for focus block until 11:30
KNOCK      m6   score=4 at=09:40  breaks focus block: cannot wait until 11:30
DROP       m7   score=0 at=09:40  no cost, no deadline, nobody waiting
...
sends: 2
```

Part of the rendered brief:

```text
## Knock at Wed 11:30
- The card payment for the studio rent failed.
  Why now: The landlord adds a late fee after 17:00 today.
- The kiln technician can come today 14:00 or Friday 10:00.
  Why now: He gives the 14:00 slot away at noon.
  [Today 14:00] [Friday 10:00]

## Brief for Lin, Wed 17:30
Needs your OK:
- There are 212 drafts older than a year. -> Delete 212 drafts older than 2025-10-28
  [Delete them] [Keep them]  (no answer = Keep them)
Done quietly (each can be undone):
- Archive 6 newsletters (undo: Move them back to the inbox)
```

See `examples/` for the full card, moments, plan and render.

## File structure

```text
knock-or-hold/
├── SKILL.md                      # agent instructions (English)
├── SKILL.zh-CN.md                # agent instructions (Chinese)
├── README.md / README.zh-CN.md
├── LICENSE                       # MIT
├── references/
│   ├── routing-rules.md          # exact scoring and routing rules
│   └── routing-rules.zh-CN.md
├── scripts/
│   └── knock.py                  # init / route / check / render / lint / calibrate
├── examples/
│   ├── card.json                 # knock card
│   ├── moments.json              # ten candidate moments
│   ├── plan.json                 # routed plan
│   └── render.md                 # knock messages + brief
└── tests/
    └── test_knock.py             # 116 unittest cases
```

## License

MIT, see [LICENSE](LICENSE).
