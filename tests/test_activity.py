import copy
import json
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from fleet import activity, config, derive, snapshot


def thread(thread_id="lane", parent=None, **changes):
    return {"server": "alpha", "threadId": thread_id, "parentThreadId": parent,
            "updatedAt": "2026-10-21T23:00:00Z", "itemCount": 2, "latestRunId": "run",
            **changes}


def item(command="git push origin main", position=1, **changes):
    return {"itemId": f"item-{position}", "position": position,
            "type": "command_execution", "status": "completed",
            "text": "$ " + command + "\nOUTPUT MUST NEVER APPEAR",
            "updatedAt": "2026-10-21T22:00:00Z", **changes}


def page(items, more=False, run_start="2026-10-21T19:40:00Z"):
    return {"items": items, "nextPosition": max((i["position"] for i in items), default=None),
            "hasMore": more, "recentRuns": [{"runId": "run", "startedAt": run_start}]}


class FakeClients:
    def __init__(self, *pages):
        self.pages = list(pages)
        self.calls = []

    def call(self, server, tool, arguments):
        self.calls.append((server, tool, arguments))
        answer = self.pages.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


class ActivityTest(unittest.TestCase):
    def collect(self, clients, threads=None):
        source = activity.Activity(config.Config(), clients)
        hub = SimpleNamespace(partial=lambda name: {"threads": threads or [thread()]})
        return source, hub, source.collect(hub)

    def test_each_command_side_effect(self):
        commands = {
            "git push origin main": "push", "git -C /repo push": "push",
            "gh pr create --title example": "pr", "gh pr merge 12 --squash": "merge",
            "gh pr comment 12 --body example": "comment", "gh pr review 12 --approve": "comment",
            "gh issue comment 12 --body example": "comment", "npx wrangler deploy": "deploy",
            "wrangler publish": "deploy", "vercel --prod": "deploy", "vercel deploy": "deploy",
            "fly deploy": "deploy", "flyctl deploy": "deploy", "netlify deploy": "deploy",
            "cd /repo && git push origin main": "push",
            "/usr/bin/bash -lc 'git push origin main'": "push",
        }
        for command, kind in commands.items():
            with self.subTest(command=command):
                _, _, result = self.collect(FakeClients(page([item(command)])))
                effect = result["lane"]["lastSideEffect"]
                self.assertEqual(effect["kind"], kind)
                self.assertNotIn("OUTPUT", effect["summary"])
                self.assertLessEqual(len(effect["summary"]), 80)

    def test_non_effects_and_failed_or_unfinished_calls(self):
        for command in ("git status", "echo 'git push'", "git push --dry-run", "wrangler deploy --dry-run"):
            with self.subTest(command=command):
                _, _, result = self.collect(FakeClients(page([item(command)])))
                self.assertIsNone(result["lane"]["lastSideEffect"])
        for status in ("running", "pending", "failed", "cancelled", "interrupted"):
            with self.subTest(status=status):
                _, _, result = self.collect(FakeClients(page([item(status=status)])))
                self.assertIsNone(result["lane"]["lastSideEffect"])

    def test_step_assistant_sentence_and_tool_target(self):
        samples = [
            (item(text="First sentence. Second sentence.", type="assistant_message"), "First sentence."),
            (item(type="dynamic_tool", text=json.dumps({"toolName": "t3.t3_thread_read",
                                                       "input": {"threadId": "child"}})), "t3.t3_thread_read child"),
            (item(text="$ git status\nprivate output"), "git status"),
        ]
        for value, expected in samples:
            _, _, result = self.collect(FakeClients(page([value])))
            self.assertEqual(result["lane"]["currentStep"], expected)
        _, _, result = self.collect(FakeClients(page([item(text="A " * 100, type="assistant_message")])))
        self.assertEqual(len(result["lane"]["currentStep"]), 100)

    def test_native_tools_and_wrapped_commands(self):
        for tool, kind in activity.TOOL_KINDS.items():
            value = item(type="dynamic_tool", text=json.dumps({"toolName": "github." + tool,
                                                               "input": {"repository": "example/repo"}}))
            _, _, result = self.collect(FakeClients(page([value])))
            self.assertEqual(result["lane"]["lastSideEffect"]["kind"], kind)
        value = item(type="dynamic_tool", text=json.dumps({"toolName": "exec_command",
                                                           "input": {"cmd": "git push origin main"}}))
        _, _, result = self.collect(FakeClients(page([value])))
        self.assertEqual(result["lane"]["lastSideEffect"]["kind"], "push")
        value["text"] = '{"toolName":"exec_command","input":{"cmd":"git push origin main", "padding":"'
        _, _, result = self.collect(FakeClients(page([value])))
        self.assertEqual(result["lane"]["lastSideEffect"]["kind"], "push")

    def test_redaction_and_limit(self):
        secret = "syntheticCredential012345678901234567890"
        _, _, result = self.collect(FakeClients(page([item("git push https://" + secret + "@example.test/repo")])))
        self.assertNotIn(secret, json.dumps(result))
        self.assertIn("[redacted]", result["lane"]["lastSideEffect"]["summary"])
        for value in ("TOKEN=short", "Bearer short", "api_key=short"):
            self.assertNotIn("short", activity.summary(value, 80))

    def test_cursor_pagination_changed_only_and_interval(self):
        clients = FakeClients(page([item(position=501)], more=True), page([item(position=600)]),
                              page([item("gh pr create", position=1001)]), page([]))
        current = [thread(itemCount=1000)]
        source, hub, first = self.collect(clients, current)
        self.assertEqual(source.interval, 10)
        self.assertEqual([c[2]["afterPosition"] for c in clients.calls], [500, 501])
        self.assertEqual(first, source.collect(hub))
        self.assertEqual(len(clients.calls), 2)
        current[0]["itemCount"] = 1001
        self.assertEqual(source.collect(hub)["lane"]["lastSideEffect"]["kind"], "pr")
        self.assertEqual(clients.calls[-1][2]["afterPosition"], 600)
        current[0]["updatedAt"] = "2026-10-21T23:01:00Z"
        source.collect(hub)
        self.assertEqual(clients.calls[-1][2]["afterPosition"], 1001)
        self.assertTrue(all(c[1] == "t3_thread_read" and c[2]["view"] == "activity" for c in clients.calls))
        self.assertTrue(all(c[2]["maxCharsPerItem"] == 2000 for c in clients.calls))

    def test_pending_completion_without_new_position(self):
        clients = FakeClients(page([item(status="running")]), page([item()]), page([]))
        current = [thread()]
        source, hub, first = self.collect(clients, current)
        self.assertIsNone(first["lane"]["lastSideEffect"])
        current[0]["updatedAt"] = "2026-10-21T23:01:00Z"
        result = source.collect(hub)
        self.assertEqual(result["lane"]["lastSideEffect"]["kind"], "push")
        self.assertEqual(clients.calls[1][2]["itemId"], "item-1")
        self.assertEqual(clients.calls[2][2]["afterPosition"], 1)

    def test_all_threads_read_and_latest_descendant_effect(self):
        threads = [thread(), thread("child", "lane"), thread("grandchild", "child"), thread("other")]
        clients = FakeClients(page([item(text="Working. More.", type="assistant_message")]),
                              page([item()]), page([item("gh pr create", updatedAt="2026-10-21T22:30:00Z")]),
                              page([item("fly deploy", updatedAt="2026-10-21T22:59:00Z")]))
        _, _, data = self.collect(clients, threads)
        context = derive.Context(threads, set(), datetime.now(timezone.utc), config.Thresholds(), data)
        lanes = [{"threadId": "lane", "server": "alpha"}]
        activity.apply(lanes, context)
        self.assertEqual(lanes[0]["currentStep"], "Working.")
        self.assertEqual(lanes[0]["lastSideEffect"]["kind"], "pr")
        self.assertEqual(len(clients.calls), 4)

    def test_missing_source_and_unknown_run(self):
        source = activity.Activity(config.Config(), FakeClients())
        self.assertEqual(source.collect(SimpleNamespace(partial=lambda _: None)), {})
        clients = FakeClients({"items": [], "recentRuns": []})
        _, _, data = self.collect(clients)
        self.assertIsNone(data["lane"]["runStartedAt"])

    def test_new_run_does_not_reuse_previous_run_start(self):
        clients = FakeClients(page([]), {"items": [], "recentRuns": []})
        current = [thread()]
        source, hub, first = self.collect(clients, current)
        self.assertIsNotNone(first["lane"]["runStartedAt"])
        current[0].update(latestRunId="new-run", updatedAt="2026-10-21T23:01:00Z")
        self.assertIsNone(source.collect(hub)["lane"]["runStartedAt"])

    def test_retry_after_read_failure_and_prune_removed_threads(self):
        clients = FakeClients(RuntimeError("synthetic failure"), page([item()]))
        source = activity.Activity(config.Config(), clients)
        current = [thread()]
        hub = SimpleNamespace(partial=lambda _: {"threads": current})
        self.assertIsNone(source.collect(hub)["lane"]["lastSideEffect"])
        self.assertIsNone(source.threads[("alpha", "lane")]["marker"])
        self.assertEqual(source.threads[("alpha", "lane")]["cursor"], 0)
        self.assertEqual(source.collect(hub)["lane"]["lastSideEffect"]["kind"], "push")
        current.clear()
        self.assertEqual(source.collect(hub), {})
        self.assertEqual(source.threads, {})

    def test_healthy_thread_is_read_after_another_thread_fails(self):
        clients = FakeClients(RuntimeError("synthetic failure"), page([item()]),
                              page([item("gh pr create")]))
        source, hub, result = self.collect(clients, [thread("failing"), thread("healthy")])
        self.assertIsNone(result["failing"]["lastSideEffect"])
        self.assertEqual(result["healthy"]["lastSideEffect"]["kind"], "push")
        self.assertEqual([c[2]["threadId"] for c in clients.calls], ["failing", "healthy"])
        result = source.collect(hub)
        self.assertEqual(result["failing"]["lastSideEffect"]["kind"], "pr")
        self.assertEqual(result["healthy"]["lastSideEffect"]["kind"], "push")
        self.assertEqual([c[2]["threadId"] for c in clients.calls],
                         ["failing", "healthy", "failing"])

    def test_later_page_failure_preserves_state_for_retry(self):
        clients = FakeClients(page([item(status="running")]),
                              page([item()]), page([item("gh pr create", position=2)], more=True),
                              RuntimeError("synthetic failure"),
                              page([item()]), page([item("gh pr create", position=2)], more=True),
                              page([item("fly deploy", position=3)]))
        current = [thread()]
        source, hub, first = self.collect(clients, current)
        before = copy.deepcopy(source.threads[("alpha", "lane")])
        current[0]["updatedAt"] = "2026-10-21T23:01:00Z"
        failed = source.collect(hub)
        self.assertEqual(failed["lane"].pop("error")["error"], "RuntimeError")
        self.assertEqual(failed, {"lane": {k: v for k, v in first["lane"].items() if k != "error"}})
        self.assertEqual(dict(source.threads[("alpha", "lane")], error=None), before)
        result = source.collect(hub)
        self.assertEqual(result["lane"]["lastSideEffect"]["kind"], "deploy")
        self.assertEqual([c[2].get("afterPosition") for c in clients.calls],
                         [0, None, 1, 2, None, 1, 2])


    def test_read_failure_is_marked_until_a_read_succeeds(self):
        clients = FakeClients(page([item()]), RuntimeError("synthetic failure"),
                              RuntimeError("synthetic failure"), page([]))
        current = [thread(), thread("child", "lane")]
        source, hub, first = self.collect(clients, current)
        self.assertIsNone(first["lane"]["error"])
        self.assertEqual(first["child"]["error"]["error"], "RuntimeError")
        since = first["child"]["error"]["since"]
        current[1]["updatedAt"] = "2026-10-21T23:01:00Z"
        second = source.collect(hub)
        self.assertEqual(second["child"]["error"], {"error": "RuntimeError", "since": since})
        self.assertEqual(second["lane"]["lastSideEffect"]["kind"], "push")   # healthy data stays
        context = derive.Context(current, set(), datetime.now(timezone.utc), config.Thresholds(), second)
        lanes = [{"threadId": "lane", "server": "alpha"}]
        activity.apply(lanes, context)
        self.assertEqual(lanes[0]["activityError"], {"error": "RuntimeError", "since": since})
        self.assertNotIn("synthetic", json.dumps(second))
        current[1]["updatedAt"] = "2026-10-21T23:02:00Z"
        context.activity = source.collect(hub)
        activity.apply(lanes, context)
        self.assertIsNone(lanes[0]["activityError"])


class CommandTest(unittest.TestCase):
    def effect(self, text):
        return activity.describe({"type": "command_execution", "text": text})

    def test_side_effect_on_a_later_line(self):
        cases = {
            "$ set -e\ngit push origin main\nTo example.test:repo.git": ("push", "git push origin main"),
            "$ cd /repo\ngit add .\ngit commit -m \"title\n\nbody\"\ngh pr create --fill\nhttps://example.test/pr/1":
                ("pr", "gh pr create --fill"),
            "$ cat > body.md <<'EOF'\ngit push origin main\nEOF\ngh pr comment 1 --body-file body.md\ndone":
                ("comment", "gh pr comment 1 --body-file body.md"),
            "$ bash -lc 'set -e\ngit push origin main'\nEverything up-to-date": ("push", "git push origin main"),
            "$ git status && \\\n  git push\noutput": ("push", "git status &&   git push"),
        }
        for text, (kind, statement) in cases.items():
            with self.subTest(text=text):
                step, found, effect = self.effect(text)
                self.assertEqual((found, effect), (kind, statement))
                self.assertNotIn("\n", step)

    def test_output_ends_the_command(self):
        for text in ("$ git diff\n--- a/run.sh\n+++ b/run.sh\n+git push origin main",
                     "$ gh pr diff 1\n@@ -1 +1 @@\n git push origin main",
                     "$ make check\nremote: ok\ngit push origin main",
                     "$ cat > notes.md <<'EOF'\ngit push origin main\nEOF",
                     "$ echo 'git push'\n[main 1234567] message\ngit push origin main"):
            with self.subTest(text=text):
                self.assertEqual(self.effect(text)[1:], (None, None))
        self.assertEqual(self.effect("$ git status\nprivate output")[0], "git status")

    def test_tool_command_spans_lines(self):
        value = {"type": "dynamic_tool", "text": json.dumps(
            {"toolName": "exec_command", "input": {"cmd": "set -e\ngh pr merge 3 --squash"}})}
        self.assertEqual(activity.describe(value), ("set -e", "merge", "gh pr merge 3 --squash"))


class QuietTest(unittest.TestCase):
    def setUp(self):
        self.lanes = [{"server": "alpha", "threadId": "lane", "title": "Example lane", "status": "running",
                       "lastSideEffect": None}]
        self.context = derive.Context([thread()], set(), datetime(2026, 10, 21, 23, tzinfo=timezone.utc),
                                      config.Thresholds(), {"lane": {"runStartedAt": "2026-10-21T19:40:00Z"}})

    def test_quiet_reason_since_and_threshold(self):
        result = activity.attention(self.lanes, self.context)
        self.assertEqual(result[0], {"kind": "quiet", "severity": "warn", "threadId": "lane", "server": "alpha",
                                     "title": "Example lane", "laneTitle": "Example lane",
                                     "reason": "Running with no push, PR, merge, deploy or comment for 3 h 20 min",
                                     "since": "2026-10-21T19:40:00Z", "prUrl": None})
        self.context.thresholds = config.Thresholds(quiet_hours=4)
        self.assertEqual(activity.attention(self.lanes, self.context), [])

    def test_recent_effect_new_run_exact_threshold_and_inactive_do_not_warn(self):
        for when in ("2026-10-21T21:00:00Z", "2026-10-21T22:00:00Z"):
            self.lanes[0]["lastSideEffect"] = {"at": when}
            self.assertEqual(activity.attention(self.lanes, self.context), [])
        self.lanes[0]["lastSideEffect"] = {"at": "2026-10-21T18:00:00Z"}
        self.context.activity["lane"]["runStartedAt"] = "2026-10-21T22:00:00Z"
        self.assertEqual(activity.attention(self.lanes, self.context), [])
        self.context.activity["lane"]["runStartedAt"] = "2026-10-21T19:40:00Z"
        self.lanes[0]["status"] = "completed"
        self.assertEqual(activity.attention(self.lanes, self.context), [])
        self.lanes[0]["status"] = "running"
        self.context.activity["lane"]["runStartedAt"] = None
        self.assertEqual(activity.attention(self.lanes, self.context), [])

    def test_quiet_reason_names_unreadable_activity(self):
        self.lanes[0]["activityError"] = {"error": "tool-error", "since": "2026-10-21T22:00:00Z"}
        self.assertTrue(activity.attention(self.lanes, self.context)[0]["reason"].endswith(
            "; activity could not be read"))

    def test_uses_later_side_effect_and_snapshot_flags(self):
        self.lanes[0]["lastSideEffect"] = {"at": "2026-10-21T20:30:00Z"}
        self.assertEqual(activity.attention(self.lanes, self.context)[0]["since"], "2026-10-21T20:30:00Z")
        # The T3 lane builder is a parallel ticket; provide its normalized result at the seam.
        from unittest.mock import patch
        with patch("fleet.derive.lanes", return_value=copy.deepcopy(self.lanes)):
            result = snapshot.assemble({"activity": {"ok": True, "at": "2026-10-21T23:00:00Z",
                                                     "error": None, "data": self.context.activity}},
                                       config.Config(), self.context.now)
        self.assertEqual(result["summary"]["stalled"], 1)
        self.assertEqual(result["lanes"][0]["flags"], ["quiet"])
        self.assertEqual(result["attention"][0]["kind"], "quiet")


if __name__ == "__main__":
    unittest.main()
