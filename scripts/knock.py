#!/usr/bin/env python3
"""knock-or-hold: decide when and how a proactive agent reaches its human.

Standard library only. Commands:

  init DIR                         write a starter card.json and moments.json
  route CARD MOMENTS [--now ISO] [--out PLAN] [--ledger FILE] [--json]
                                   score every candidate moment and pick a route
  check PLAN                       re-verify the hard rules on a (possibly edited) plan
  render PLAN                      print the knock message(s) and the next brief
  lint MOMENTS                     check chips and wording only
  calibrate LEDGER [--threshold N] learn from the human's feedback

Exit codes: 0 ok, 1 rule or lint errors, 2 invalid input.
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

ROUTES = ("KNOCK", "ASK", "BRIEF", "BRIEF_ASK", "SILENT", "DROP")
KNOCKING = ("KNOCK", "ASK")
KINDS = ("fyi", "alert", "decision", "act")
COST = {"none": 0, "low": 1, "medium": 2, "high": 3, "severe": 4}
WAIT = {"nobody": 0, "person": 1, "many": 2}
SOURCES = ("owner", "known", "unknown", "untrusted")
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
SHOUTING = re.compile(r"\b(URGENT|ASAP|IMMEDIATELY|ACT NOW)\b|!!|紧急|十万火急|立刻马上")
MAX_CHIP_CHARS = 24
FOCUS_MARGIN_MIN = 15  # a deadline this close after a focus block counts as inside it


class InputError(Exception):
    pass


# ---------------------------------------------------------------- time helpers

def parse_hhmm(text):
    m = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", str(text or "").strip())
    if not m:
        raise InputError("bad time %r, expected HH:MM" % (text,))
    return int(m.group(1)) * 60 + int(m.group(2))


def in_window(minute, start, end):
    """True when minute-of-day lies in [start, end); windows may wrap midnight."""
    if start == end:
        return False
    if start < end:
        return start <= minute < end
    return minute >= start or minute < end


def minute_of(dt):
    return dt.hour * 60 + dt.minute


def at_minute(day_dt, minute):
    return day_dt.replace(hour=minute // 60, minute=minute % 60, second=0, microsecond=0)


def window_end(now, start, end):
    """Next moment the window [start, end) closes, for a 'now' inside it."""
    candidate = at_minute(now, end)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def get_zone(name):
    if not name:
        return timezone.utc
    if ZoneInfo is None:
        raise InputError("zoneinfo unavailable; use timezone 'UTC'")
    try:
        return ZoneInfo(name)
    except Exception:
        raise InputError("unknown timezone %r" % (name,))


def parse_dt(text, zone):
    if text in (None, ""):
        return None
    try:
        dt = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        raise InputError("bad datetime %r" % (text,))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=zone)
    return dt.astimezone(zone)


def expand_days(spec):
    if not spec:
        return set(DAYS)
    out = set()
    for part in str(spec).lower().replace(" ", "").split(","):
        if "-" in part:
            a, b = part.split("-", 1)
            if a not in DAYS or b not in DAYS:
                raise InputError("bad day range %r" % (part,))
            i, j = DAYS.index(a), DAYS.index(b)
            idx = list(range(i, j + 1)) if i <= j else list(range(i, 7)) + list(range(0, j + 1))
            out.update(DAYS[k] for k in idx)
        else:
            if part not in DAYS:
                raise InputError("bad day %r" % (part,))
            out.add(part)
    return out


# ---------------------------------------------------------------- card

class Card(object):
    def __init__(self, raw):
        if not isinstance(raw, dict):
            raise InputError("card must be a JSON object")
        self.raw = raw
        self.owner = raw.get("owner", "you")
        self.zone = get_zone(raw.get("timezone", "UTC"))
        q = raw.get("quiet_hours") or {}
        self.quiet = (parse_hhmm(q["start"]), parse_hhmm(q["end"])) if q else None
        self.focus = []
        for b in raw.get("focus_blocks", []) or []:
            self.focus.append((expand_days(b.get("days")), parse_hhmm(b["start"]), parse_hhmm(b["end"])))
        self.briefs = sorted(parse_hhmm(t) for t in (raw.get("brief_times") or ["08:00"]))
        self.budget = int(raw.get("knock_budget_per_day", 3))
        self.sent_today = int(raw.get("knocks_sent_today", 0))
        self.threshold = int(raw.get("knock_threshold", 6))
        self.permissions = set(raw.get("standing_permissions", []) or [])
        self.bypass = set(raw.get("quiet_bypass", []) or [])
        if self.budget < 0 or self.threshold < 0:
            raise InputError("budget and threshold must be >= 0")

    def in_quiet(self, dt):
        return bool(self.quiet) and in_window(minute_of(dt), *self.quiet)

    def quiet_end(self, dt):
        return window_end(dt, *self.quiet)

    def focus_end(self, dt):
        day = DAYS[dt.weekday()]
        for days, s, e in self.focus:
            if day in days and in_window(minute_of(dt), s, e):
                return window_end(dt, s, e)
        return None

    def next_brief(self, dt):
        for offset in range(0, 8):
            day = dt + timedelta(days=offset)
            for m in self.briefs:
                slot = at_minute(day, m)
                if slot > dt and not self.in_quiet(slot):
                    return slot
        raise InputError("no brief time falls outside quiet hours")


# ---------------------------------------------------------------- scoring

def pressure(deadline, now):
    if deadline is None:
        return 0
    minutes = (deadline - now).total_seconds() / 60.0
    if minutes <= 30:
        return 3
    if minutes <= 240:
        return 2
    if minutes <= 1440:
        return 1
    return 0


def effective_cost(m, notes):
    claimed = m.get("cost_of_delay", "low")
    if claimed not in COST:
        raise InputError("%s: cost_of_delay must be one of %s" % (m.get("id"), "/".join(COST)))
    cost = COST[claimed]
    source = m.get("source", "known")
    if m.get("verified"):
        return cost
    if source == "untrusted" and cost > 1:
        notes.append("untrusted source cannot raise its own urgency: cost capped at low")
        return 1
    if source == "unknown" and cost > 2:
        notes.append("unverified sender: cost capped at medium until checked")
        return 2
    return cost


def validate_moment(m):
    if not isinstance(m, dict):
        raise InputError("each moment must be an object")
    mid = m.get("id")
    if not mid:
        raise InputError("moment without id")
    if m.get("kind") not in KINDS:
        raise InputError("%s: kind must be one of %s" % (mid, "/".join(KINDS)))
    if m.get("source", "known") not in SOURCES:
        raise InputError("%s: source must be one of %s" % (mid, "/".join(SOURCES)))
    if m.get("waiting", "nobody") not in WAIT:
        raise InputError("%s: waiting must be one of %s" % (mid, "/".join(WAIT)))
    if not str(m.get("summary", "")).strip():
        raise InputError("%s: summary is required" % mid)
    if m["kind"] == "act":
        a = m.get("action")
        if not isinstance(a, dict) or not str(a.get("label", "")).strip():
            raise InputError("%s: kind act needs action.label (the exact action)" % mid)


def silent_allowed(m, card):
    """Return (ok, reason) for acting without telling the human first."""
    a = m.get("action") or {}
    if m.get("source") == "untrusted":
        return False, "requested by untrusted content"
    if not a.get("reversible"):
        return False, "not reversible"
    if a.get("external"):
        return False, "visible to other people"
    if a.get("class") not in card.permissions:
        return False, "no standing permission for class %r" % (a.get("class"),)
    if not str(a.get("undo", "")).strip():
        return False, "no undo path written"
    return True, "standing permission %r, reversible, private" % a.get("class")


def first_pass(m, card, now, brief_at):
    validate_moment(m)
    notes = []
    deadline = parse_dt(m.get("deadline"), card.zone)
    cost = effective_cost(m, notes)
    p = pressure(deadline, now)
    w = WAIT[m.get("waiting", "nobody")]
    score = cost + p + w
    severe = cost == 4
    beats_brief = deadline is not None and deadline < brief_at
    urgent = score >= card.threshold or severe or (beats_brief and cost >= 2)
    kind = m["kind"]
    if kind == "act":
        ok, why = silent_allowed(m, card)
        if ok:
            route = "SILENT"
            notes.append(why)
        else:
            notes.append("needs approval: " + why)
            route = "ASK" if urgent else "BRIEF_ASK"
    elif kind == "fyi" and cost == 0 and p == 0 and w == 0:
        route = "DROP"
        notes.append("no cost, no deadline, nobody waiting")
    else:
        route = "KNOCK" if urgent else "BRIEF"
    if route in ("BRIEF", "BRIEF_ASK") and beats_brief:
        notes.append("deadline passes before the next brief (accepted: low cost)")
    return {
        "id": m["id"], "route": route, "score": score, "cost": cost, "pressure": p,
        "waiting": w, "severe": severe, "deadline": deadline.isoformat() if deadline else None,
        "notes": notes, "moment": m,
    }


def route_all(card, moments, now):
    if not isinstance(moments, list):
        raise InputError("moments must be a JSON list")
    ids = [m.get("id") for m in moments if isinstance(m, dict)]
    if len(ids) != len(set(ids)):
        raise InputError("duplicate moment ids")
    brief_at = card.next_brief(now)
    items = [first_pass(m, card, now, brief_at) for m in moments]

    # timing for knocking items: quiet hours, then focus blocks
    for it in items:
        if it["route"] not in KNOCKING:
            continue
        m = it["moment"]
        dl = parse_dt(it["deadline"], card.zone)
        at = now
        if card.in_quiet(now):
            q_end = card.quiet_end(now)
            if it["severe"] and m.get("category") in card.bypass and (dl is None or dl <= q_end):
                it["notes"].append("QUIET_BYPASS: severe, category %r on bypass list" % m.get("category"))
            else:
                at = q_end
                it["notes"].append("held for quiet hours until %s" % q_end.strftime("%H:%M"))
                if dl is not None and dl < q_end:
                    it["notes"].append("deadline falls inside quiet hours; owner's quiet wins")
        else:
            f_end = card.focus_end(now)
            if f_end is not None:
                if it["severe"] or (dl is not None and dl <= f_end + timedelta(minutes=FOCUS_MARGIN_MIN)):
                    it["notes"].append("breaks focus block: cannot wait until %s" % f_end.strftime("%H:%M"))
                else:
                    at = f_end
                    it["notes"].append("held for focus block until %s" % f_end.strftime("%H:%M"))
        it["at"] = at

    # budget: one send per distinct time; per local date counter
    counter = {now.date(): card.sent_today}
    sends = []
    times = sorted(set(it["at"] for it in items if it["route"] in KNOCKING))
    for t in times:
        bundle = [it for it in items if it["route"] in KNOCKING and it["at"] == t]
        used = counter.get(t.date(), 0)
        if used >= card.budget:
            keep = [it for it in bundle if it["severe"]]
            for it in bundle:
                if it in keep:
                    it["notes"].append("OVERDRAFT: budget spent, severe item goes anyway")
                else:
                    it["route"] = "BRIEF_ASK" if it["route"] == "ASK" else "BRIEF"
                    it["notes"].append("budget spent (%d/%d); moved to brief" % (used, card.budget))
            bundle = keep
        if bundle:
            counter[t.date()] = used + 1
            sends.append({"at": t.isoformat(), "ids": [it["id"] for it in bundle],
                          "overdraft": used >= card.budget})
    for it in items:
        if it["route"] in ("BRIEF", "BRIEF_ASK"):
            base = it.get("at", now)
            it["at"] = card.next_brief(base) if base != now else brief_at
        elif it["route"] in ("SILENT", "DROP"):
            it["at"] = now
        it["at"] = it["at"].isoformat()
    lint = []
    for it in items:
        lint.extend(lint_moment(it["moment"], it["route"]))
    return {
        "card": card.raw, "now": now.isoformat(), "next_brief": brief_at.isoformat(),
        "sends": sends, "items": items, "lint": lint,
    }


# ---------------------------------------------------------------- lint

def chip_words(label):
    label = label.strip()
    if re.search(r"[\u3400-\u9fff]", label) and " " not in label:
        return max(1, (len(label) + 3) // 4)  # CJK: about four characters per word slot
    return len(label.split())


def lint_moment(m, route):
    """Return list of (id, level, message) dicts."""
    out = []
    mid = m.get("id")

    def add(level, msg):
        out.append({"id": mid, "level": level, "message": msg})

    needs_chips = route in ("ASK", "BRIEF_ASK") or (route == "KNOCK" and m.get("kind") == "decision")
    chips = m.get("chips") or []
    if needs_chips and not chips:
        add("error", "route %s needs 2-3 one-tap chips" % route)
    if chips:
        if not 2 <= len(chips) <= 3:
            add("error", "use 2-3 chips, found %d" % len(chips))
        seen = set()
        for c in chips:
            label = str((c or {}).get("label", "")).strip()
            if not label:
                add("error", "blank chip label")
                continue
            if len(label) > MAX_CHIP_CHARS:
                add("error", "chip %r longer than %d characters" % (label, MAX_CHIP_CHARS))
            if chip_words(label) > 3:
                add("error", "chip %r longer than 3 words" % label)
            if label.lower() in seen:
                add("error", "duplicate chip %r" % label)
            seen.add(label.lower())
        labels = [str((c or {}).get("label", "")).strip() for c in chips]
        safe = [str(c.get("label", "")).strip() for c in chips if isinstance(c, dict) and c.get("safe")]
        default = str(m.get("default_if_silent", "")).strip()
        if route in ("ASK", "BRIEF_ASK"):
            if not safe:
                add("error", "approval needs one chip marked safe (hold / no / not now)")
            if not default:
                add("error", "approval needs default_if_silent")
            elif default not in safe:
                add("error", "silence must never approve: default_if_silent must be a safe chip")
        if default and default not in labels:
            add("error", "default_if_silent %r is not one of the chips" % default)
    if route in KNOCKING and not str(m.get("why_now", "")).strip():
        add("error", "a knock must say why now (why_now)")
    text = " ".join(str(m.get(k, "")) for k in ("summary", "why_now"))
    if SHOUTING.search(text):
        add("warning", "shouting words; state the deadline and the cost instead")
    summary = str(m.get("summary", "")).strip()
    if summary.endswith(("?", "？")) and len(re.findall(r"[.!?。！？]", summary)) <= 1:
        add("warning", "summary is only a question; lead with what happened")
    if len(summary) > 160:
        add("warning", "summary longer than 160 characters; a knock is read on a lock screen")
    return out


# ---------------------------------------------------------------- check

def check_plan(plan):
    errors = []
    try:
        card = Card(plan["card"])
        items = plan["items"]
    except (KeyError, TypeError):
        raise InputError("plan must contain card and items")
    by_date = {}
    for s in plan.get("sends", []):
        t = parse_dt(s["at"], card.zone)
        by_date.setdefault(t.date(), []).append(s)
    now = parse_dt(plan.get("now"), card.zone)
    for d, sends in by_date.items():
        base = card.sent_today if now and d == now.date() else 0
        if base + len(sends) > card.budget:
            extra = sends[max(0, card.budget - base):]
            for s in extra:
                bad = [i for i in s["ids"] if not any(it["id"] == i and it.get("severe") for it in items)]
                if bad:
                    errors.append("over budget on %s with non-severe items %s" % (d, ",".join(bad)))
    for it in items:
        m = it.get("moment", {})
        r = it.get("route")
        mid = it.get("id")
        if r not in ROUTES:
            errors.append("%s: unknown route %r" % (mid, r))
            continue
        if r == "SILENT":
            ok, why = silent_allowed(m, card)
            if not ok:
                errors.append("%s: SILENT not allowed (%s)" % (mid, why))
        if r in KNOCKING:
            at = parse_dt(it.get("at"), card.zone)
            if at is not None and card.in_quiet(at):
                if not (it.get("severe") and m.get("category") in card.bypass):
                    errors.append("%s: knocks inside quiet hours without a valid bypass" % mid)
        if m.get("kind") == "act" and r in ("KNOCK", "BRIEF"):
            errors.append("%s: an action must be ASK, BRIEF_ASK or SILENT" % mid)
        if r == "DROP" and m.get("kind") != "fyi":
            errors.append("%s: only fyi moments may be dropped" % mid)
        for f in lint_moment(m, r):
            if f["level"] == "error":
                errors.append("%s: %s" % (mid, f["message"]))
    return errors


# ---------------------------------------------------------------- render

def chip_line(m):
    chips = m.get("chips") or []
    if not chips:
        return ""
    labels = ["[%s]" % c.get("label", "") for c in chips]
    default = m.get("default_if_silent")
    tail = "  (no answer = %s)" % default if default else ""
    return " ".join(labels) + tail


def render(plan):
    card = Card(plan["card"])
    items = {it["id"]: it for it in plan["items"]}
    lines = []
    for s in plan.get("sends", []):
        at = parse_dt(s["at"], card.zone)
        flag = " (overdraft)" if s.get("overdraft") else ""
        lines.append("## Knock at %s%s" % (at.strftime("%a %H:%M"), flag))
        for i in s["ids"]:
            it = items[i]
            m = it["moment"]
            lines.append("- %s" % m["summary"])
            if m.get("why_now"):
                lines.append("  Why now: %s" % m["why_now"])
            if it["route"] == "ASK":
                lines.append("  Exact action: %s" % m["action"]["label"])
            cl = chip_line(m)
            if cl:
                lines.append("  " + cl)
        lines.append("")
    brief = [it for it in plan["items"] if it["route"] in ("BRIEF", "BRIEF_ASK")]
    silent = [it for it in plan["items"] if it["route"] == "SILENT"]
    dropped = [it for it in plan["items"] if it["route"] == "DROP"]
    nb = parse_dt(plan["next_brief"], card.zone)
    lines.append("## Brief for %s, %s" % (card.owner, nb.strftime("%a %H:%M")))
    asks = sorted([it for it in brief if it["route"] == "BRIEF_ASK"], key=lambda x: -x["score"])
    know = sorted([it for it in brief if it["route"] == "BRIEF"], key=lambda x: -x["score"])
    if asks:
        lines.append("Needs your OK:")
        for it in asks:
            m = it["moment"]
            lines.append("- %s -> %s" % (m["summary"], m["action"]["label"]))
            cl = chip_line(m)
            if cl:
                lines.append("  " + cl)
    if know:
        lines.append("Worth knowing:")
        for it in know:
            m = it["moment"]
            tag = "(unverified sender) " if m.get("source") in ("untrusted", "unknown") and not m.get("verified") else ""
            lines.append("- %s%s" % (tag, m["summary"]))
    if silent:
        lines.append("Done quietly (each can be undone):")
        for it in silent:
            a = it["moment"]["action"]
            lines.append("- %s (undo: %s)" % (a["label"], a.get("undo", "")))
    if dropped:
        lines.append("Skipped %d item(s) with no cost and nobody waiting." % len(dropped))
    if not (asks or know or silent or dropped):
        lines.append("Nothing new.")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- calibrate

FEEDBACK = ("needed_me", "could_wait", "too_late", "wrong_silent")


def calibrate(rows, threshold):
    knocks = [r for r in rows if r.get("route") in KNOCKING]
    rated = [r for r in knocks if r.get("feedback") in ("needed_me", "could_wait")]
    needed = sum(1 for r in rated if r["feedback"] == "needed_me")
    misses = [r for r in rows if r.get("route") not in KNOCKING and r.get("feedback") == "too_late"]
    wrong_silent = [r for r in rows if r.get("route") == "SILENT" and r.get("feedback") == "wrong_silent"]
    precision = needed / float(len(rated)) if rated else None
    advice = []
    for r in wrong_silent:
        advice.append("remove standing permission %r (silent action %s was unwelcome)"
                      % (r.get("class"), r.get("id")))
    new_threshold = threshold
    if misses:
        new_threshold = max(0, threshold - 1)
        advice.append("lower knock_threshold to %d: %d item(s) reached you too late" % (new_threshold, len(misses)))
    elif precision is not None and len(rated) >= 5 and precision < 0.6:
        new_threshold = threshold + 1
        advice.append("raise knock_threshold to %d: only %.0f%% of knocks needed you"
                      % (new_threshold, precision * 100))
    if not advice:
        advice.append("keep settings" if rated else "not enough feedback yet; ask after knocks")
    return {
        "knocks": len(knocks), "rated_knocks": len(rated), "needed_me": needed,
        "precision": precision, "too_late": len(misses), "wrong_silent": len(wrong_silent),
        "threshold": threshold, "suggested_threshold": new_threshold, "advice": advice,
    }


def read_ledger(path):
    rows = []
    with open(path, encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                raise InputError("ledger line %d is not JSON" % n)
    return rows


# ---------------------------------------------------------------- starters

STARTER_CARD = {
    "owner": "Lin",
    "timezone": "Asia/Shanghai",
    "quiet_hours": {"start": "22:30", "end": "07:30"},
    "focus_blocks": [{"days": "mon-fri", "start": "09:00", "end": "11:30"}],
    "brief_times": ["08:00", "17:30"],
    "knock_budget_per_day": 3,
    "knocks_sent_today": 0,
    "knock_threshold": 6,
    "standing_permissions": ["archive_newsletter", "label_mail"],
    "quiet_bypass": ["family", "security"],
}

STARTER_MOMENTS = [
    {"id": "m1", "kind": "alert", "category": "money", "source": "known",
     "summary": "Card payment for the studio rent failed.",
     "why_now": "Landlord charges a late fee after 17:00 today.",
     "cost_of_delay": "high", "deadline": "2026-10-28T17:00:00+08:00", "waiting": "person"},
    {"id": "m2", "kind": "act", "category": "mail", "source": "owner",
     "summary": "Six newsletters arrived overnight.",
     "action": {"label": "Archive 6 newsletters", "class": "archive_newsletter",
                "reversible": True, "external": False, "undo": "Move back to inbox"},
     "cost_of_delay": "none", "waiting": "nobody"},
]


def load_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        raise InputError("cannot read %s: %s" % (path, exc))


def write_json(path, data):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def summarize(plan):
    lines = ["now %s  next brief %s" % (plan["now"], plan["next_brief"])]
    for it in plan["items"]:
        lines.append("%-10s %-4s score=%d at=%s  %s" % (
            it["route"], it["id"], it["score"], it["at"][11:16], "; ".join(it["notes"])))
    lines.append("sends: %d" % len(plan["sends"]))
    for f in plan["lint"]:
        lines.append("%s %s: %s" % (f["level"].upper(), f["id"], f["message"]))
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="knock.py", description="When and how should the agent reach its human?")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("init"); p.add_argument("dir")
    p = sub.add_parser("route"); p.add_argument("card"); p.add_argument("moments")
    p.add_argument("--now"); p.add_argument("--out"); p.add_argument("--ledger"); p.add_argument("--json", action="store_true")
    p = sub.add_parser("check"); p.add_argument("plan")
    p = sub.add_parser("render"); p.add_argument("plan")
    p = sub.add_parser("lint"); p.add_argument("moments"); p.add_argument("--route", default="ASK")
    p = sub.add_parser("calibrate"); p.add_argument("ledger"); p.add_argument("--threshold", type=int, default=6)
    args = ap.parse_args(argv)
    try:
        if args.cmd == "init":
            os.makedirs(args.dir, exist_ok=True)
            for name, data in (("card.json", STARTER_CARD), ("moments.json", STARTER_MOMENTS)):
                path = os.path.join(args.dir, name)
                if os.path.exists(path):
                    print("exists, left alone: %s" % path)
                else:
                    write_json(path, data)
                    print("wrote %s" % path)
            return 0
        if args.cmd == "route":
            card = Card(load_json(args.card))
            now = parse_dt(args.now, card.zone) if args.now else datetime.now(card.zone)
            plan = route_all(card, load_json(args.moments), now)
            if args.out:
                write_json(args.out, plan)
            if args.ledger:
                with open(args.ledger, "a", encoding="utf-8") as fh:
                    for it in plan["items"]:
                        a = it["moment"].get("action") or {}
                        fh.write(json.dumps({"id": it["id"], "route": it["route"], "score": it["score"],
                                             "class": a.get("class"), "at": it["at"], "feedback": None},
                                            ensure_ascii=False) + "\n")
            print(json.dumps(plan, ensure_ascii=False, indent=2) if args.json else summarize(plan))
            return 1 if any(f["level"] == "error" for f in plan["lint"]) else 0
        if args.cmd == "check":
            errors = check_plan(load_json(args.plan))
            for e in errors:
                print("FAIL " + e)
            print("PASS" if not errors else "%d rule error(s)" % len(errors))
            return 1 if errors else 0
        if args.cmd == "render":
            sys.stdout.write(render(load_json(args.plan)))
            return 0
        if args.cmd == "lint":
            data = load_json(args.moments)
            found = []
            for m in data if isinstance(data, list) else [data]:
                found.extend(lint_moment(m, args.route))
            for f in found:
                print("%s %s: %s" % (f["level"].upper(), f["id"], f["message"]))
            print("clean" if not found else "%d finding(s)" % len(found))
            return 1 if any(f["level"] == "error" for f in found) else 0
        if args.cmd == "calibrate":
            print(json.dumps(calibrate(read_ledger(args.ledger), args.threshold), indent=2))
            return 0
        ap.print_help()
        return 2
    except InputError as exc:
        print("invalid input: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
