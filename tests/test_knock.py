import copy
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import knock as k  # noqa: E402

TZ = timezone(timedelta(hours=8))


def card(**over):
    raw = {
        "owner": "Lin", "timezone": "UTC",
        "quiet_hours": {"start": "22:00", "end": "07:00"},
        "focus_blocks": [{"days": "mon-fri", "start": "09:00", "end": "11:00"}],
        "brief_times": ["08:00", "17:00"],
        "knock_budget_per_day": 3, "knocks_sent_today": 0, "knock_threshold": 6,
        "standing_permissions": ["archive"], "quiet_bypass": ["security"],
    }
    raw.update(over)
    return k.Card(raw)


def at(h, m=0, day=28):
    # 2026-10-28 is a Wednesday
    return datetime(2026, 10, day, h, m, tzinfo=timezone.utc)


def mom(**over):
    m = {"id": "x", "kind": "alert", "source": "known", "summary": "Something happened.",
         "why_now": "Because.", "cost_of_delay": "low", "waiting": "nobody"}
    m.update(over)
    return m


def act(**over):
    a = {"label": "Archive 3 mails", "class": "archive", "reversible": True,
         "external": False, "undo": "Move back"}
    a.update(over.pop("action", {}))
    m = mom(kind="act", action=a, chips=[{"label": "Do it"}, {"label": "Hold", "safe": True}],
            default_if_silent="Hold")
    m.update(over)
    return m


def one(c, m, now):
    return k.route_all(c, [m], now)["items"][0]


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = k.main(argv)
    return code, out.getvalue(), err.getvalue()


class TimeHelpers(unittest.TestCase):
    def test_parse_hhmm(self):
        self.assertEqual(k.parse_hhmm("07:30"), 450)

    def test_parse_hhmm_single_digit_hour(self):
        self.assertEqual(k.parse_hhmm("7:05"), 425)

    def test_parse_hhmm_rejects(self):
        for bad in ("24:00", "7", "ab:cd", "", None):
            with self.assertRaises(k.InputError):
                k.parse_hhmm(bad)

    def test_window_plain(self):
        self.assertTrue(k.in_window(600, 540, 660))
        self.assertFalse(k.in_window(660, 540, 660))

    def test_window_wraps_midnight(self):
        self.assertTrue(k.in_window(23 * 60, 22 * 60, 7 * 60))
        self.assertTrue(k.in_window(60, 22 * 60, 7 * 60))
        self.assertFalse(k.in_window(12 * 60, 22 * 60, 7 * 60))

    def test_empty_window(self):
        self.assertFalse(k.in_window(100, 100, 100))

    def test_window_end_next_day(self):
        end = k.window_end(at(23), 22 * 60, 7 * 60)
        self.assertEqual(end, at(7, day=29))

    def test_window_end_same_day(self):
        self.assertEqual(k.window_end(at(2), 22 * 60, 7 * 60), at(7))

    def test_expand_days_range(self):
        self.assertEqual(k.expand_days("mon-wed"), {"mon", "tue", "wed"})

    def test_expand_days_wrap(self):
        self.assertEqual(k.expand_days("sat-mon"), {"sat", "sun", "mon"})

    def test_expand_days_list_and_default(self):
        self.assertEqual(k.expand_days("mon,fri"), {"mon", "fri"})
        self.assertEqual(len(k.expand_days(None)), 7)

    def test_expand_days_bad(self):
        with self.assertRaises(k.InputError):
            k.expand_days("funday")

    def test_parse_dt_naive_gets_zone(self):
        dt = k.parse_dt("2026-10-28T10:00:00", timezone.utc)
        self.assertEqual(dt.tzinfo, timezone.utc)

    def test_parse_dt_converts_zone(self):
        dt = k.parse_dt("2026-10-28T18:00:00+08:00", timezone.utc)
        self.assertEqual(dt.hour, 10)

    def test_parse_dt_empty_and_bad(self):
        self.assertIsNone(k.parse_dt("", timezone.utc))
        with self.assertRaises(k.InputError):
            k.parse_dt("tomorrow", timezone.utc)

    def test_named_timezone(self):
        c = card(timezone="Asia/Shanghai")
        self.assertEqual(c.zone.utcoffset(datetime(2026, 10, 28)), timedelta(hours=8))

    def test_unknown_timezone(self):
        with self.assertRaises(k.InputError):
            card(timezone="Mars/Olympus")


class CardTests(unittest.TestCase):
    def test_quiet(self):
        c = card()
        self.assertTrue(c.in_quiet(at(23)))
        self.assertFalse(c.in_quiet(at(12)))

    def test_no_quiet_hours(self):
        self.assertFalse(card(quiet_hours=None).in_quiet(at(3)))

    def test_focus_weekday(self):
        self.assertEqual(card().focus_end(at(10)), at(11))

    def test_focus_not_on_weekend(self):
        self.assertIsNone(card().focus_end(at(10, day=31)))  # Saturday

    def test_next_brief_today(self):
        self.assertEqual(card().next_brief(at(9)), at(17))

    def test_next_brief_tomorrow(self):
        self.assertEqual(card().next_brief(at(18)), at(8, day=29))

    def test_next_brief_skips_quiet_slot(self):
        c = card(brief_times=["06:00", "12:00"])
        self.assertEqual(c.next_brief(at(13)), at(12, day=29))

    def test_next_brief_all_quiet(self):
        c = card(brief_times=["23:00"])
        with self.assertRaises(k.InputError):
            c.next_brief(at(12))

    def test_negative_budget(self):
        with self.assertRaises(k.InputError):
            card(knock_budget_per_day=-1)

    def test_card_not_object(self):
        with self.assertRaises(k.InputError):
            k.Card([])


class Scoring(unittest.TestCase):
    def test_pressure_bands(self):
        now = at(12)
        self.assertEqual(k.pressure(None, now), 0)
        self.assertEqual(k.pressure(now + timedelta(minutes=20), now), 3)
        self.assertEqual(k.pressure(now - timedelta(hours=1), now), 3)
        self.assertEqual(k.pressure(now + timedelta(hours=3), now), 2)
        self.assertEqual(k.pressure(now + timedelta(hours=20), now), 1)
        self.assertEqual(k.pressure(now + timedelta(days=3), now), 0)

    def test_untrusted_capped(self):
        notes = []
        self.assertEqual(k.effective_cost(mom(source="untrusted", cost_of_delay="severe"), notes), 1)
        self.assertTrue(notes)

    def test_unknown_capped_at_medium(self):
        self.assertEqual(k.effective_cost(mom(source="unknown", cost_of_delay="high"), []), 2)

    def test_verified_not_capped(self):
        self.assertEqual(k.effective_cost(mom(source="untrusted", verified=True, cost_of_delay="high"), []), 3)

    def test_bad_cost(self):
        with self.assertRaises(k.InputError):
            k.effective_cost(mom(cost_of_delay="huge"), [])

    def test_score_sum(self):
        it = one(card(), mom(cost_of_delay="medium", waiting="many"), at(12))
        self.assertEqual(it["score"], 4)


class Validation(unittest.TestCase):
    def test_missing_id(self):
        with self.assertRaises(k.InputError):
            k.validate_moment({"kind": "fyi", "summary": "a"})

    def test_bad_kind(self):
        with self.assertRaises(k.InputError):
            k.validate_moment(mom(kind="memo"))

    def test_bad_source_and_waiting(self):
        with self.assertRaises(k.InputError):
            k.validate_moment(mom(source="rumour"))
        with self.assertRaises(k.InputError):
            k.validate_moment(mom(waiting="everyone"))

    def test_empty_summary(self):
        with self.assertRaises(k.InputError):
            k.validate_moment(mom(summary="  "))

    def test_act_needs_label(self):
        with self.assertRaises(k.InputError):
            k.validate_moment(mom(kind="act", action={"class": "archive"}))

    def test_duplicate_ids(self):
        with self.assertRaises(k.InputError):
            k.route_all(card(), [mom(), mom()], at(12))

    def test_moments_must_be_list(self):
        with self.assertRaises(k.InputError):
            k.route_all(card(), {"id": "x"}, at(12))


class Routing(unittest.TestCase):
    def test_silent_when_permitted(self):
        self.assertEqual(one(card(), act(), at(12))["route"], "SILENT")

    def test_not_silent_without_permission(self):
        it = one(card(), act(action={"class": "delete"}), at(12))
        self.assertEqual(it["route"], "BRIEF_ASK")

    def test_not_silent_when_irreversible(self):
        self.assertEqual(one(card(), act(action={"reversible": False}), at(12))["route"], "BRIEF_ASK")

    def test_not_silent_when_external(self):
        self.assertEqual(one(card(), act(action={"external": True}), at(12))["route"], "BRIEF_ASK")

    def test_not_silent_without_undo(self):
        self.assertEqual(one(card(), act(action={"undo": ""}), at(12))["route"], "BRIEF_ASK")

    def test_not_silent_for_untrusted_request(self):
        self.assertEqual(one(card(), act(source="untrusted"), at(12))["route"], "BRIEF_ASK")

    def test_urgent_action_asks_now(self):
        m = act(action={"external": True}, cost_of_delay="high",
                deadline=(at(12) + timedelta(hours=1)).isoformat(), waiting="person")
        self.assertEqual(one(card(), m, at(12))["route"], "ASK")

    def test_drop_only_empty_fyi(self):
        self.assertEqual(one(card(), mom(kind="fyi", cost_of_delay="none"), at(12))["route"], "DROP")

    def test_fyi_low_goes_to_brief(self):
        self.assertEqual(one(card(), mom(kind="fyi"), at(12))["route"], "BRIEF")

    def test_alert_none_cost_not_dropped(self):
        self.assertEqual(one(card(), mom(cost_of_delay="none"), at(12))["route"], "BRIEF")

    def test_threshold_knock(self):
        m = mom(cost_of_delay="high", waiting="many", deadline=(at(12) + timedelta(hours=20)).isoformat())
        self.assertEqual(one(card(), m, at(12))["route"], "KNOCK")

    def test_below_threshold_brief(self):
        m = mom(cost_of_delay="medium", waiting="person")
        self.assertEqual(one(card(), m, at(12))["route"], "BRIEF")

    def test_severe_always_knocks(self):
        self.assertEqual(one(card(), mom(cost_of_delay="severe"), at(12))["route"], "KNOCK")

    def test_deadline_before_brief_knocks_when_costly(self):
        m = mom(cost_of_delay="medium", deadline=at(15).isoformat())
        self.assertEqual(one(card(), m, at(12))["route"], "KNOCK")

    def test_deadline_before_brief_low_cost_noted(self):
        it = one(card(), mom(cost_of_delay="low", deadline=at(15).isoformat()), at(12))
        self.assertEqual(it["route"], "BRIEF")
        self.assertTrue(any("deadline passes" in n for n in it["notes"]))

    def test_brief_at_next_brief(self):
        self.assertEqual(one(card(), mom(), at(12))["at"], at(17).isoformat())


class Timing(unittest.TestCase):
    def test_quiet_holds_knock(self):
        it = one(card(), mom(cost_of_delay="severe", category="work"), at(23))
        self.assertEqual(it["at"], at(7, day=29).isoformat())

    def test_quiet_bypass_for_listed_category(self):
        it = one(card(), mom(cost_of_delay="severe", category="security"), at(23))
        self.assertEqual(it["at"], at(23).isoformat())
        self.assertTrue(any("QUIET_BYPASS" in n for n in it["notes"]))

    def test_quiet_bypass_needs_severe(self):
        m = mom(cost_of_delay="high", category="security", waiting="many",
                deadline=(at(23) + timedelta(minutes=10)).isoformat())
        it = one(card(), m, at(23))
        self.assertEqual(it["at"], at(7, day=29).isoformat())
        self.assertTrue(any("quiet wins" in n for n in it["notes"]))

    def test_focus_holds_until_end(self):
        m = mom(cost_of_delay="high", waiting="many", deadline=at(16).isoformat())
        it = one(card(), m, at(10))
        self.assertEqual(it["at"], at(11).isoformat())

    def test_focus_broken_when_deadline_inside(self):
        m = mom(cost_of_delay="high", waiting="many", deadline=at(10, 40).isoformat())
        self.assertEqual(one(card(), m, at(10))["at"], at(10).isoformat())

    def test_focus_margin(self):
        m = mom(cost_of_delay="high", waiting="many", deadline=at(11, 10).isoformat())
        self.assertEqual(one(card(), m, at(10))["at"], at(10).isoformat())

    def test_focus_broken_by_severe(self):
        self.assertEqual(one(card(), mom(cost_of_delay="severe"), at(10))["at"], at(10).isoformat())


class Budget(unittest.TestCase):
    def test_bundle_counts_once(self):
        ms = [mom(id="a", cost_of_delay="severe"), mom(id="b", cost_of_delay="severe")]
        plan = k.route_all(card(), ms, at(12))
        self.assertEqual(len(plan["sends"]), 1)
        self.assertEqual(plan["sends"][0]["ids"], ["a", "b"])

    def test_spent_budget_moves_to_brief(self):
        m = mom(cost_of_delay="high", waiting="many", deadline=at(20).isoformat())
        it = one(card(knocks_sent_today=3), m, at(12))
        self.assertEqual(it["route"], "BRIEF")

    def test_spent_budget_ask_becomes_brief_ask(self):
        m = act(action={"external": True}, cost_of_delay="high", waiting="many",
                deadline=at(13).isoformat())
        self.assertEqual(one(card(knocks_sent_today=3), m, at(12))["route"], "BRIEF_ASK")

    def test_overdraft_for_severe(self):
        plan = k.route_all(card(knocks_sent_today=3), [mom(cost_of_delay="severe")], at(12))
        self.assertTrue(plan["sends"][0]["overdraft"])
        self.assertEqual(plan["items"][0]["route"], "KNOCK")

    def test_overdraft_keeps_only_severe(self):
        ms = [mom(id="a", cost_of_delay="severe"),
              mom(id="b", cost_of_delay="high", waiting="many", deadline=at(20).isoformat())]
        plan = k.route_all(card(knocks_sent_today=3), ms, at(12))
        self.assertEqual(plan["sends"][0]["ids"], ["a"])
        self.assertEqual(plan["items"][1]["route"], "BRIEF")

    def test_new_day_fresh_budget(self):
        plan = k.route_all(card(knocks_sent_today=3), [mom(cost_of_delay="severe", category="x")], at(23))
        self.assertFalse(plan["sends"][0]["overdraft"])

    def test_zero_budget(self):
        plan = k.route_all(card(knock_budget_per_day=0), [mom(cost_of_delay="high", waiting="many",
                                                               deadline=at(14).isoformat())], at(12))
        self.assertEqual(plan["sends"], [])


class Lint(unittest.TestCase):
    def errors(self, m, route):
        return [f["message"] for f in k.lint_moment(m, route) if f["level"] == "error"]

    def test_clean_approval(self):
        self.assertEqual(self.errors(act(), "ASK"), [])

    def test_ask_needs_chips(self):
        self.assertTrue(self.errors(act(chips=[]), "ASK"))

    def test_decision_knock_needs_chips(self):
        self.assertTrue(self.errors(mom(kind="decision"), "KNOCK"))

    def test_alert_knock_chips_optional(self):
        self.assertEqual(self.errors(mom(), "KNOCK"), [])

    def test_chip_count(self):
        m = act(chips=[{"label": "A"}, {"label": "B"}, {"label": "C"}, {"label": "Hold", "safe": True}])
        self.assertTrue(any("2-3" in e for e in self.errors(m, "ASK")))

    def test_chip_too_long(self):
        m = act(chips=[{"label": "Approve this payment please"}, {"label": "Hold", "safe": True}])
        self.assertTrue(self.errors(m, "ASK"))

    def test_chip_too_many_words(self):
        m = act(chips=[{"label": "go do it now"}, {"label": "Hold", "safe": True}])
        self.assertTrue(any("3 words" in e for e in self.errors(m, "ASK")))

    def test_cjk_chip_ok(self):
        m = act(chips=[{"label": "现在付款"}, {"label": "先等等", "safe": True}], default_if_silent="先等等")
        self.assertEqual(self.errors(m, "ASK"), [])

    def test_cjk_chip_too_long(self):
        m = act(chips=[{"label": "请帮我马上把这笔钱付给房东谢谢"}, {"label": "先等等", "safe": True}],
                default_if_silent="先等等")
        self.assertTrue(self.errors(m, "ASK"))

    def test_duplicate_and_blank(self):
        m = act(chips=[{"label": "Hold", "safe": True}, {"label": "hold"}, {"label": " "}])
        errs = self.errors(m, "ASK")
        self.assertTrue(any("duplicate" in e for e in errs))
        self.assertTrue(any("blank" in e for e in errs))

    def test_needs_safe_chip(self):
        m = act(chips=[{"label": "Yes"}, {"label": "Sure"}], default_if_silent="Yes")
        self.assertTrue(any("safe" in e for e in self.errors(m, "ASK")))

    def test_silence_never_approves(self):
        m = act(default_if_silent="Do it")
        self.assertTrue(any("silence" in e for e in self.errors(m, "BRIEF_ASK")))

    def test_default_must_be_chip(self):
        m = mom(chips=[{"label": "A"}, {"label": "B"}], default_if_silent="C")
        self.assertTrue(self.errors(m, "KNOCK"))

    def test_missing_default(self):
        self.assertTrue(self.errors(act(default_if_silent=""), "ASK"))

    def test_knock_needs_why_now(self):
        self.assertTrue(self.errors(mom(why_now=""), "KNOCK"))
        self.assertEqual(self.errors(mom(why_now=""), "BRIEF"), [])

    def test_shouting_warning(self):
        found = k.lint_moment(mom(summary="URGENT!! do this"), "BRIEF")
        self.assertTrue(any(f["level"] == "warning" for f in found))

    def test_chinese_shouting_warning(self):
        found = k.lint_moment(mom(summary="紧急：请处理"), "BRIEF")
        self.assertTrue(found)

    def test_question_only_warning(self):
        found = k.lint_moment(mom(summary="Should I pay the invoice?"), "BRIEF")
        self.assertTrue(any("question" in f["message"] for f in found))

    def test_long_summary_warning(self):
        found = k.lint_moment(mom(summary="a" * 200), "BRIEF")
        self.assertTrue(any("160" in f["message"] for f in found))


class Check(unittest.TestCase):
    def plan(self, ms, now=None, **over):
        return json.loads(json.dumps(k.route_all(card(**over), ms, now or at(12)), default=str))

    def test_generated_plan_passes(self):
        p = self.plan([act(id="s"), mom(cost_of_delay="severe"), mom(id="y", kind="fyi", cost_of_delay="none")])
        self.assertEqual(k.check_plan(p), [])

    def test_edited_silent_fails(self):
        p = self.plan([act(action={"external": True})])
        p["items"][0]["route"] = "SILENT"
        self.assertTrue(any("SILENT" in e for e in k.check_plan(p)))

    def test_knock_in_quiet_fails(self):
        p = self.plan([mom(cost_of_delay="severe", category="work")], now=at(23))
        p["items"][0]["at"] = at(23).isoformat()
        self.assertTrue(any("quiet" in e for e in k.check_plan(p)))

    def test_action_as_knock_fails(self):
        p = self.plan([act(action={"class": "delete"})])
        p["items"][0]["route"] = "BRIEF"
        self.assertTrue(any("action must be" in e for e in k.check_plan(p)))

    def test_drop_non_fyi_fails(self):
        p = self.plan([mom()])
        p["items"][0]["route"] = "DROP"
        self.assertTrue(k.check_plan(p))

    def test_unknown_route(self):
        p = self.plan([mom()])
        p["items"][0]["route"] = "SHOUT"
        self.assertTrue(k.check_plan(p))

    def test_over_budget_fails(self):
        p = self.plan([mom(cost_of_delay="high", waiting="many", deadline=at(20).isoformat())])
        p["card"]["knocks_sent_today"] = 3
        self.assertTrue(any("budget" in e for e in k.check_plan(p)))

    def test_lint_errors_count(self):
        p = self.plan([act(action={"class": "delete"})])
        p["items"][0]["moment"]["default_if_silent"] = "Do it"
        self.assertTrue(any("silence" in e for e in k.check_plan(p)))

    def test_bad_plan_shape(self):
        with self.assertRaises(k.InputError):
            k.check_plan({"items": []})


class RenderAndCalibrate(unittest.TestCase):
    def test_render_sections(self):
        ms = [act(id="s"), act(id="a", action={"class": "delete"}), mom(id="b", kind="fyi"),
              mom(id="d", kind="fyi", cost_of_delay="none"), mom(id="k", cost_of_delay="severe")]
        text = k.render(json.loads(json.dumps(k.route_all(card(), ms, at(12)))))
        for part in ("## Knock at", "Needs your OK", "Worth knowing", "Done quietly", "Skipped 1"):
            self.assertIn(part, text)
        self.assertIn("no answer = Hold", text)

    def test_render_marks_unverified(self):
        text = k.render(k.route_all(card(), [mom(source="untrusted")], at(12)))
        self.assertIn("(unverified sender)", text)

    def test_render_nothing(self):
        self.assertIn("Nothing new", k.render(k.route_all(card(), [], at(12))))

    def test_calibrate_keep(self):
        rows = [{"route": "KNOCK", "feedback": "needed_me"}] * 5
        r = k.calibrate(rows, 6)
        self.assertEqual(r["precision"], 1.0)
        self.assertEqual(r["suggested_threshold"], 6)

    def test_calibrate_raise(self):
        rows = [{"route": "KNOCK", "feedback": "could_wait"}] * 4 + [{"route": "ASK", "feedback": "needed_me"}]
        self.assertEqual(k.calibrate(rows, 6)["suggested_threshold"], 7)

    def test_calibrate_lower_on_miss(self):
        rows = [{"route": "BRIEF", "feedback": "too_late"}, {"route": "KNOCK", "feedback": "could_wait"}]
        self.assertEqual(k.calibrate(rows, 6)["suggested_threshold"], 5)

    def test_calibrate_wrong_silent(self):
        rows = [{"id": "m2", "route": "SILENT", "class": "archive", "feedback": "wrong_silent"}]
        r = k.calibrate(rows, 6)
        self.assertTrue(any("archive" in a for a in r["advice"]))

    def test_calibrate_no_feedback(self):
        r = k.calibrate([{"route": "KNOCK", "feedback": None}], 6)
        self.assertIsNone(r["precision"])
        self.assertIn("not enough", r["advice"][0])


class Cli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ex = os.path.join(HERE, "..", "examples")

    def test_init_and_no_overwrite(self):
        d = os.path.join(self.tmp, "w")
        self.assertEqual(run(["init", d])[0], 0)
        code, out, _ = run(["init", d])
        self.assertIn("left alone", out)

    def test_route_example(self):
        plan = os.path.join(self.tmp, "plan.json")
        led = os.path.join(self.tmp, "ledger.jsonl")
        code, out, _ = run(["route", os.path.join(self.ex, "card.json"), os.path.join(self.ex, "moments.json"),
                            "--now", "2026-10-28T09:40:00+08:00", "--out", plan, "--ledger", led])
        self.assertEqual(code, 0)
        self.assertIn("sends: 2", out)
        self.assertEqual(run(["check", plan])[0], 0)
        with open(led) as fh:
            self.assertEqual(len(fh.readlines()), 10)

    def test_example_routes(self):
        c = k.Card(k.load_json(os.path.join(self.ex, "card.json")))
        ms = k.load_json(os.path.join(self.ex, "moments.json"))
        plan = k.route_all(c, ms, k.parse_dt("2026-10-28T09:40:00+08:00", c.zone))
        routes = {it["id"]: it["route"] for it in plan["items"]}
        self.assertEqual(routes["m6"], "KNOCK")
        self.assertEqual(routes["m4"], "BRIEF")
        self.assertEqual(routes["m7"], "DROP")
        self.assertEqual(routes["m10"], "BRIEF_ASK")

    def test_route_lint_error_exit(self):
        cp = os.path.join(self.tmp, "c.json")
        mp = os.path.join(self.tmp, "m.json")
        k.write_json(cp, card().raw)
        k.write_json(mp, [act(action={"class": "delete"}, default_if_silent="Do it")])
        self.assertEqual(run(["route", cp, mp, "--now", at(12).isoformat()])[0], 1)

    def test_invalid_input_exit(self):
        mp = os.path.join(self.tmp, "bad.json")
        with open(mp, "w") as fh:
            fh.write("{not json")
        code, _, err = run(["route", mp, mp])
        self.assertEqual(code, 2)
        self.assertIn("invalid input", err)

    def test_render_cli(self):
        plan = os.path.join(self.tmp, "p.json")
        run(["route", os.path.join(self.ex, "card.json"), os.path.join(self.ex, "moments.json"),
             "--now", "2026-10-28T09:40:00+08:00", "--out", plan])
        code, out, _ = run(["render", plan])
        self.assertIn("Brief for Lin", out)

    def test_lint_cli(self):
        mp = os.path.join(self.tmp, "m.json")
        k.write_json(mp, [act()])
        self.assertEqual(run(["lint", mp])[0], 0)
        k.write_json(mp, [act(chips=[])])
        self.assertEqual(run(["lint", mp])[0], 1)

    def test_calibrate_cli(self):
        lp = os.path.join(self.tmp, "l.jsonl")
        with open(lp, "w") as fh:
            fh.write(json.dumps({"route": "KNOCK", "feedback": "needed_me"}) + "\n\n")
        code, out, _ = run(["calibrate", lp])
        self.assertEqual(code, 0)
        self.assertIn("precision", out)

    def test_calibrate_bad_ledger(self):
        lp = os.path.join(self.tmp, "l.jsonl")
        with open(lp, "w") as fh:
            fh.write("oops\n")
        self.assertEqual(run(["calibrate", lp])[0], 2)

    def test_no_command(self):
        self.assertEqual(run([])[0], 2)


if __name__ == "__main__":
    unittest.main()
