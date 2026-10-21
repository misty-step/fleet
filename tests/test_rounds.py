import json
import unittest
from pathlib import Path
from datetime import datetime, timezone

from fleet import config, derive, rounds

PR = {"repository": "example/shop", "number": 338, "url": "https://github.com/example/shop/pull/338", "state": "open"}
OTHER = {"repository": "example/notes", "number": 15, "url": "https://github.com/example/notes/pull/15", "state": "open"}


def thread(thread_id, title, parent=None, prs=(), created="2026-10-21T20:00:00Z", server="alpha"):
    return {"server": server, "threadId": thread_id, "title": title, "parentThreadId": parent,
            "prs": [dict(pr) for pr in prs], "createdAt": created}


def lane(thread_id="lane", prs=(PR,), server="alpha"):
    return {"threadId": thread_id, "server": server, "title": "Shop lane",
            "prs": [dict(pr, round=None) for pr in prs]}


def context(threads, ledger=None, loop_round=2):
    return derive.Context(threads=threads, roster=set(), now=datetime(2026, 10, 21, 23, tzinfo=timezone.utc),
                          thresholds=config.Thresholds(loop_round=loop_round), rounds=ledger or {})


def round_of(threads, name="example/shop#338"):
    return (rounds.from_threads(threads).get(name) or {}).get("round")


class TitleTest(unittest.TestCase):
    def with_child(self, title, thread_id="child"):
        return [thread("lane", "Shop lane", prs=[PR]), thread(thread_id, title, parent="lane")]

    def test_pr_names_and_round_markers(self):
        cases = {
            "QA shop#338 round 2": 2, "Review #338 round 2/3": 2, "Judge pr338 r2": 2, "Recheck PR-338 -r3": 3,
            "Builder #338 fix 2": 2, "QA #338 recheck 2": 2, "Fix PR#338 round-4": 4, "Ship example/shop#338 r2": 2,
        }
        for title, expected in cases.items():
            with self.subTest(title=title):
                self.assertEqual(round_of(self.with_child(title)), expected)

    def test_thread_id_names_the_pr_and_round(self):
        self.assertEqual(round_of(self.with_child("Recheck", thread_id="mcp:shop-pr338-r3")), 3)

    def test_first_qa_review_or_judge_without_marker_is_round_one(self):
        for title in ("QA shop#338", "Reviewer for #338", "Judge pr-338", "qa-review #338"):
            with self.subTest(title=title):
                self.assertEqual(round_of(self.with_child(title)), 1)

    def test_no_round_when_nothing_names_it_or_no_review_role(self):
        self.assertIsNone(round_of(self.with_child("Builder for #338")))
        self.assertIsNone(round_of(self.with_child("QA round 2 for #339")))
        self.assertEqual(round_of(self.with_child("QA #338 (R75 importer) at r75-host")), 1)   # "R75" is a name, not round 75
        self.assertIsNone(round_of([thread("lane", "Shop lane #338 round 4", prs=[PR])]))   # the lane itself is not a child

    def test_highest_round_wins_and_since_is_its_earliest_thread(self):
        threads = [thread("lane", "Shop lane", prs=[PR]),
                   thread("a", "QA #338", parent="lane", created="2026-10-21T18:00:00Z"),
                   thread("b", "Recheck #338 round 3", parent="lane", created="2026-10-21T21:00:00Z"),
                   thread("c", "Judge #338 r3", parent="b", created="2026-10-21T20:30:00Z"),
                   thread("d", "Review #338 round 2", parent="lane", created="2026-10-21T19:00:00Z")]
        self.assertEqual(rounds.from_threads(threads)["example/shop#338"], {"round": 3, "since": "2026-10-21T20:30:00Z"})

    def test_bare_number_stays_in_its_lane_and_repo_names_reach_the_fleet(self):
        threads = [thread("lane", "Shop lane", prs=[PR]),
                   thread("other", "Notes lane", prs=[dict(OTHER, number=338, repository="example/notes")]),
                   thread("x", "QA #338 round 2", parent="other"),
                   thread("y", "Judge shop#338 r3", parent="other")]
        found = rounds.from_threads(threads)
        self.assertEqual(found["example/notes#338"]["round"], 2)
        self.assertEqual(found["example/shop#338"]["round"], 3)


class FakeGh:
    def __init__(self, comments, author="builder", available=True):
        self.comments = comments
        self.author = author
        self.ok = available
        self.calls = []

    def available(self):
        return self.ok

    def api(self, *args):
        self.calls.append(args)
        if any(a.endswith("/pulls/338") for a in args):
            return self.author + "\n"
        return "".join(json.dumps(c) + "\n" for c in self.comments)


def event(comment_id, seq, kind, author="builder", at="2026-10-21T20:00:00Z", **fields):
    body = rounds.MARKER + "\n```json\n" + json.dumps(dict(v=1, kind=kind, seq=seq, key="k", **fields), indent=2) + "\n```\n"
    return [comment_id, body, author, at]


class FakeHub:
    def __init__(self, threads):
        self.threads = threads

    def partial(self, name):
        return {"servers": [], "threads": self.threads} if name == "t3" else None


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


LEDGER_FIXED = [event(1, 1, "init"), event(2, 2, "report"), event(3, 3, "verdict", verdict="FIX", at="2026-10-21T21:00:00Z"),
                [4, "a plain comment", "builder", "2026-10-21T21:05:00Z"],
                event(5, 4, "verdict", author="outsider", verdict="FIX"),
                event(6, 3, "verdict", verdict="FIX", at="2026-10-21T22:00:00Z")]


class LedgerTest(unittest.TestCase):
    def source(self, gh, clock=None):
        return rounds.Rounds(None, None, gh=gh, clock=clock or Clock())

    def test_ledger_round_counts_fix_verdicts_by_the_pr_author_only(self):
        self.assertEqual(rounds.replay(LEDGER_FIXED, "builder"), {"round": 2, "since": "2026-10-21T21:00:00Z"})
        self.assertEqual(rounds.replay([event(1, 1, "init")], "builder"), {"round": 1, "since": "2026-10-21T20:00:00Z"})
        self.assertIsNone(rounds.replay([[1, "no marker", "builder", None]], "builder"))

    def test_malformed_or_gapped_ledger_is_unreadable(self):
        broken = [[1, rounds.MARKER + "\n```json\n{not json\n```\n", "builder", None]]
        for comments in (broken, [event(1, 1, "init"), event(2, 3, "report")], [event(1, 1, "report")]):
            with self.assertRaises(rounds.LedgerUnreadable):
                rounds.replay(comments, "builder")

    def test_ledger_round_wins_over_titles(self):
        threads = [thread("lane", "Shop lane", prs=[PR]), thread("c", "Recheck #338 round 4", parent="lane")]
        partial = self.source(FakeGh(LEDGER_FIXED)).collect(FakeHub(threads))
        self.assertEqual(partial, {"example/shop#338": {"round": 2, "source": "ledger", "since": "2026-10-21T21:00:00Z"}})
        lanes = [lane()]
        rounds.apply(lanes, context(threads, partial))
        self.assertEqual(lanes[0]["prs"][0]["round"], 2)
        self.assertEqual(rounds.attention(lanes, context(threads, partial)), [])

    def test_ledger_read_at_most_once_a_minute_per_open_pr(self):
        threads = [thread("lane", "Shop lane", prs=[PR, dict(OTHER, state="merged")])]
        gh, clock = FakeGh(LEDGER_FIXED), Clock()
        source = self.source(gh, clock)
        source.collect(FakeHub(threads))
        source.collect(FakeHub(threads))
        self.assertEqual(len(gh.calls), 2)   # one PR author read and one comments read, for the open PR only
        clock.now += 60
        source.collect(FakeHub(threads))
        self.assertEqual(len(gh.calls), 4)

    def test_without_gh_titles_alone_and_no_error(self):
        threads = [thread("lane", "Shop lane", prs=[PR]), thread("c", "Judge #338 round 3", parent="lane")]
        gh = FakeGh(LEDGER_FIXED, available=False)
        partial = self.source(gh).collect(FakeHub(threads))
        self.assertEqual(partial, {})
        self.assertEqual(gh.calls, [])
        lanes = [lane()]
        rounds.apply(lanes, context(threads, partial))
        self.assertEqual(lanes[0]["prs"][0]["round"], 3)

    def test_missing_gh_binary_is_not_available(self):
        self.assertFalse(rounds.Gh(binary="fleet-no-such-gh").available())

    def test_unreadable_ledger_keeps_what_was_known(self):
        threads = [thread("lane", "Shop lane", prs=[PR])]
        gh, clock = FakeGh(LEDGER_FIXED), Clock()
        source = self.source(gh, clock)
        source.collect(FakeHub(threads))
        gh.comments = [[1, rounds.MARKER + "\nbroken", "builder", None]]
        clock.now += 60
        self.assertEqual(source.collect(FakeHub(threads))["example/shop#338"]["round"], 2)

    def test_merged_pr_keeps_its_ledger_round(self):
        gh, clock = FakeGh(LEDGER_FIXED), Clock()
        source = self.source(gh, clock)
        source.collect(FakeHub([thread("lane", "Shop lane", prs=[PR])]))
        clock.now += 120
        partial = source.collect(FakeHub([thread("lane", "Shop lane", prs=[dict(PR, state="merged")])]))
        self.assertEqual(len(gh.calls), 2)
        lanes = [lane(prs=[dict(PR, state="merged")])]
        rounds.apply(lanes, context([], partial))
        self.assertEqual(lanes[0]["prs"][0]["round"], 2)


class LoopRuleTest(unittest.TestCase):
    def loop(self, title, state="open", loop_round=2):
        threads = [thread("lane", "Shop lane", prs=[dict(PR, state=state)]),
                   thread("c", title, parent="lane", created="2026-10-21T21:30:00Z")]
        lanes = [lane(prs=[dict(PR, state=state)])]
        ctx = context(threads, loop_round=loop_round)
        rounds.apply(lanes, ctx)
        return rounds.attention(lanes, ctx)

    def test_review_round_three_is_fix_round_two_and_a_loop(self):
        self.assertEqual(self.loop("Recheck shop#338 round 3"), [{
            "kind": "loop", "severity": "bad", "threadId": "lane", "server": "alpha", "title": "Shop lane",
            "laneTitle": "Shop lane", "reason": "PR #338 is at fix round 2 (review round 3)",
            "since": "2026-10-21T21:30:00Z", "prUrl": "https://github.com/example/shop/pull/338"}])

    def test_below_threshold_closed_or_unknown_is_no_loop(self):
        self.assertEqual(self.loop("Recheck shop#338 round 2"), [])
        self.assertEqual(self.loop("Recheck shop#338 round 5", state="merged"), [])
        self.assertEqual(self.loop("Builder for shop#338"), [])

    def test_owner_qualified_name_matches_only_that_owner(self):
        twin = dict(PR, repository="other/shop", url="https://github.com/other/shop/pull/338")
        threads = [thread("lane", "Shop lane", prs=[PR]), thread("twin", "Twin lane", prs=[twin]),
                   thread("c", "QA example/shop#338 round 3", parent="lane", created="2026-10-21T21:30:00Z")]
        lanes = [lane(prs=[PR]), lane("twin", prs=[twin])]
        ctx = context(threads)
        rounds.apply(lanes, ctx)
        self.assertNotIn("other/shop#338", rounds.from_threads(threads))
        self.assertIsNone(lanes[1]["prs"][0]["round"])
        alerts = rounds.attention(lanes, ctx)
        self.assertEqual([(a["threadId"], a["prUrl"]) for a in alerts], [("lane", PR["url"])])

    def test_threshold_is_configuration(self):
        self.assertEqual(len(self.loop("Recheck shop#338 round 2", loop_round=1)), 1)

    def test_page_tint_uses_the_same_rule(self):
        app = (Path(__file__).resolve().parent.parent / "web" / "app.js").read_text()
        self.assertIn("p.round - 1 >= (snapshot.thresholds.loopRound || 2)", app)


if __name__ == "__main__":
    unittest.main()
