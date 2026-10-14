#!/usr/bin/env python3
"""Offline upgrade regression tests, using only synthetic games and temporary files."""
from __future__ import annotations

import copy
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from common import canonical, dump_json, load_json, validate_game
from gamekit import append_request, context_for, make_session, verify_session

ROOT = Path(__file__).resolve().parent.parent


class UpgradeTests(unittest.TestCase):
    def setUp(self):
        self.game = load_json(ROOT / "assets" / "example-game.json")
        self.tmp = tempfile.TemporaryDirectory(prefix="rpg-upgrade-")
        self.directory = Path(self.tmp.name)
        self.game_path = self.directory / "game.json"
        self.session_path = self.directory / "session.json"
        dump_json(self.game_path, self.game)
        dump_json(self.session_path, make_session(self.game))

    def tearDown(self):
        self.tmp.cleanup()

    def cli(self, *args, script=None, success=True):
        result = subprocess.run(
            [sys.executable, "-S", "-B", "-X", "utf8", str(script or ROOT / "scripts" / "gamekit.py"), *map(str, args)],
            capture_output=True, text=True, encoding="utf-8", timeout=30,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return json.loads(result.stdout)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        return result

    def action(self, ident, operations=None, path=None, **extra):
        path = path or self.session_path
        request = {"type": "action", "id": ident,
                   "expected_revision": load_json(path)["revision"],
                   "summary": ident, "operations": operations or [], **extra}
        return append_request(self.game, path, request)

    def remember(self, ident, text, visibility="private", known_by=None, importance=5, path=None):
        return self.action("record-" + ident, [{"op": "remember", "id": ident, "text": text,
            "visibility": visibility, "known_by": known_by or [], "importance": importance}], path=path)

    def context(self, actor="gm", path=None, **limits):
        session = load_json(path or self.session_path)
        return context_for(self.game, session["state"], actor, session=session, **limits)

    def assert_rejected_unchanged(self, request):
        before = self.session_path.read_bytes()
        with self.assertRaises((ValueError, TypeError)):
            append_request(self.game, self.session_path, request)
        self.assertEqual(before, self.session_path.read_bytes())

    def test_legacy_runtime_created_save_replays_without_mutation(self):
        fixtures = ROOT / "assets" / "test-fixtures"
        game = load_json(fixtures / "legacy-game.json")
        session = load_json(fixtures / "legacy-session.json")
        before = canonical(session)
        result = verify_session(game, session)
        self.assertTrue(result["ok"])
        self.assertEqual(result["revision"], 1)
        self.assertEqual(session["state"]["player"]["stats"]["currency"], 10)
        self.assertEqual(canonical(session), before)
        self.assertNotIn("memories", session["state"]["extensions"])
        context_for(game, session["state"], "gm", session=session)
        self.assertEqual(canonical(session), before)

    def test_initial_future_location_rejected(self):
        game = copy.deepcopy(self.game)
        next(x for x in game["world"]["locations"] if x["id"] == game["state"]["location_id"])["available_from"] = 1
        self.assertFalse(validate_game(game, strict=True)["ok"])

    def test_declared_rule_invalid_consequence_is_rejected_before_export(self):
        game = copy.deepcopy(self.game)
        rule = next(iter(game["gameplay"]["mechanics"]["checks"].values()))
        rule["consequences"]["success"] = [[{"op": "item", "id": "invalid-count", "delta": 1.5}]]
        self.assertFalse(validate_game(game, strict=True)["ok"])
        rule["consequences"]["success"] = [[{"op": "move", "location_id": "MISSING_LOCATION"}]]
        self.assertFalse(validate_game(game, strict=True)["ok"])

    def test_reserved_actor_ids_do_not_pass_as_npcs(self):
        for reserved in ("gm", "player"):
            game = copy.deepcopy(self.game)
            game["world"]["npcs"].append({"id": reserved, "name": "Ambiguous actor", "description": "test",
                "goals": ["test"], "behavior": ["test"], "speech": "test", "knowledge": [],
                "claim_ids": [], "available_from": 0, "status": "alive"})
            self.assertFalse(validate_game(game, strict=True)["ok"])

    def test_memory_actor_visibility_and_current_relationship(self):
        self.remember("private-promise", "PRIVATE_PROMISE_TOKEN", known_by=["player", "N1"])
        self.remember("gm-secret", "GM_SECRET_TOKEN", visibility="gm")
        self.remember("public-news", "PUBLIC_NEWS_TOKEN", visibility="public")
        self.action("trust-change", [{"op": "relation", "npc_id": "N1", "dimension": "trust", "delta": 7}])
        self.assertIn("PRIVATE_PROMISE_TOKEN", canonical(self.context("player")))
        self.assertIn("PRIVATE_PROMISE_TOKEN", canonical(self.context("N1")))
        self.assertNotIn("PRIVATE_PROMISE_TOKEN", canonical(self.context("N2")))
        for actor in ("player", "N1", "N2"):
            self.assertNotIn("GM_SECRET_TOKEN", canonical(self.context(actor)))
        self.assertIn("PUBLIC_NEWS_TOKEN", canonical(self.context("player")))
        self.assertNotIn("PUBLIC_NEWS_TOKEN", canonical(self.context("N2")))
        self.assertEqual(self.context("N1")["self"]["relation_to_player"]["trust"], 7)

    def test_event_summary_defaults_private_and_tagged_summary_is_recalled(self):
        self.action("EVENT_GM_ONLY_TOKEN")
        self.action("EVENT_N1_TOKEN", recall={"visibility": "private", "known_by": ["player", "N1"]})
        self.assertIn("EVENT_GM_ONLY_TOKEN", canonical(self.context("gm")))
        self.assertNotIn("EVENT_GM_ONLY_TOKEN", canonical(self.context("player")))
        self.assertIn("EVENT_N1_TOKEN", canonical(self.context("N1")))
        self.assertNotIn("EVENT_N1_TOKEN", canonical(self.context("N2")))

    def test_important_memory_survives_recent_events_and_budget(self):
        self.remember("old-promise", "OLD_IMPORTANT_PROMISE_TOKEN", known_by=["player", "N1"])
        for n in range(18):
            self.action("routine-" + str(n), recall={"visibility": "public", "known_by": []})
        context = self.context("player", recall_limit=3, recall_chars=200)
        self.assertIn("OLD_IMPORTANT_PROMISE_TOKEN", canonical(context))
        entries = context["memory"]["important"] + context["memory"]["recent_events"]
        self.assertLessEqual(len(entries), 3)
        self.assertLessEqual(sum(len(e.get("text", e.get("summary", ""))) for e in entries), 200)

    def test_long_important_memories_leave_budget_for_recent_events(self):
        for n in range(3):
            self.remember("long-" + str(n), "X" * 2000, known_by=["player"])
        self.action("RECENT_SCENE_TOKEN", recall={"visibility": "public", "known_by": []})
        context = self.context("player", recall_limit=12, recall_chars=6000)
        self.assertTrue(context["memory"]["important"])
        self.assertIn("RECENT_SCENE_TOKEN", canonical(context["memory"]["recent_events"]))
        entries = context["memory"]["important"] + context["memory"]["recent_events"]
        self.assertLessEqual(sum(len(e.get("text", e.get("summary", ""))) for e in entries), 6000)

    def test_branch_memory_inherits_past_and_isolates_new_events(self):
        self.remember("past", "SHARED_PAST_TOKEN", known_by=["player", "N1"])
        fork_path = self.directory / "alternate.json"
        self.cli("fork", self.game_path, self.session_path, "--out", fork_path, "--branch", "alternate")
        self.assertIn("SHARED_PAST_TOKEN", canonical(self.context("player", fork_path)))
        self.remember("alternate", "ALTERNATE_ONLY_TOKEN", known_by=["player"], path=fork_path)
        self.remember("main", "MAIN_ONLY_TOKEN", known_by=["player"])
        self.assertNotIn("ALTERNATE_ONLY_TOKEN", canonical(self.context("player")))
        self.assertNotIn("MAIN_ONLY_TOKEN", canonical(self.context("player", fork_path)))
        self.assertTrue(verify_session(self.game, load_json(fork_path))["ok"])

    def test_memories_duplicate_and_failed_action_are_atomic(self):
        request = {"type": "action", "id": "remember-once", "expected_revision": 0, "summary": "record",
            "operations": [{"op": "remember", "id": "once", "text": "ONE_MEMORY_TOKEN",
                            "visibility": "private", "known_by": ["player"], "importance": 5}]}
        append_request(self.game, self.session_path, request)
        before = self.session_path.read_bytes()
        self.assertTrue(append_request(self.game, self.session_path, request)["already_applied"])
        self.assertEqual(before, self.session_path.read_bytes())
        bad = {"type": "action", "id": "bad", "expected_revision": 1, "summary": "invalid owner",
               "operations": [{"op": "remember", "id": "bad", "text": "must not persist",
                               "visibility": "private", "known_by": ["UNKNOWN_NPC"], "importance": 5}]}
        self.assert_rejected_unchanged(bad)

    def test_time_boundary_repair_then_original_continuation(self):
        material = "\u4fee\u8865\u6750\u6599"
        self.action("wait-for-low-tide", [{"op": "advance", "ticks": 2}])
        self.action("repair", [{"op": "item", "id": material, "delta": -1},
                               {"op": "advance", "ticks": 1}, {"op": "quest", "id": "Q2", "status": "complete"}])
        self.assertEqual(load_json(self.session_path)["state"]["time"]["tick"], 3)
        self.action("next-shift", [{"op": "advance", "ticks": 1}])
        self.assertEqual(load_json(self.session_path)["state"]["time"]["tick"], 4)
        self.assertTrue(verify_session(self.game, load_json(self.session_path))["ok"])

    def test_declared_rule_parameters_and_consequences_are_bound(self):
        rule_id = next(iter(self.game["gameplay"]["mechanics"]["checks"]))
        rule = self.game["gameplay"]["mechanics"]["checks"][rule_id]
        roll = self.cli("roll", self.game_path, self.session_path, "--event-id", "bound-check",
                        "--expected-revision", "0", "--rule-id", rule_id)
        receipt = roll["event"]["rolls"][0]
        self.assertEqual(receipt["target"], rule["target"])
        self.assertEqual(receipt["modifier"], rule.get("modifier", 0))
        request = {"type": "action", "id": "bound-result", "expected_revision": 1,
            "summary": "commit bound consequences", "rule_id": rule_id,
            "requires_check": {"event_id": "bound-check", "outcome": receipt["outcome"]},
            "operations": rule["consequences"][receipt["outcome"]][0] + [
                {"op": "remember", "id": "bridge-result", "text": "BOUND_RESULT_MEMORY_TOKEN",
                 "visibility": "private", "known_by": ["player"], "importance": 4}]}
        bad = copy.deepcopy(request)
        bad["operations"] = [{"op": "resource", "name": "currency", "delta": 500}]
        self.assert_rejected_unchanged(bad)
        append_request(self.game, self.session_path, request)
        self.assertIn("BOUND_RESULT_MEMORY_TOKEN", canonical(self.context("player")))
        self.assertTrue(append_request(self.game, self.session_path, request)["already_applied"])
        bad = copy.deepcopy(request)
        bad.update(id="second-result", expected_revision=2)
        self.assert_rejected_unchanged(bad)

    def test_rule_preconditions_and_parameter_mismatch_preserve_save(self):
        rule_id = next(iter(self.game["gameplay"]["mechanics"]["checks"]))
        rule = self.game["gameplay"]["mechanics"]["checks"][rule_id]
        request = {"type": "roll", "id": "wrong-target", "expected_revision": 0,
                   "summary": "invalid check", "rule_id": rule_id, "dice": rule["dice"],
                   "modifier": rule.get("modifier", 0), "target": rule["target"] + 1}
        self.assert_rejected_unchanged(request)
        game = copy.deepcopy(self.game)
        game["gameplay"]["mechanics"]["checks"][rule_id]["preconditions"] = {"stats_min": {"hp": 11}}
        path = self.directory / "precondition-session.json"
        dump_json(path, make_session(game))
        before = path.read_bytes()
        request.update(id="blocked-check", target=rule["target"])
        with self.assertRaises(ValueError):
            append_request(game, path, request)
        self.assertEqual(before, path.read_bytes())

    def test_synthetic_guaranteed_failure_commits_cost_and_keeps_route(self):
        game = copy.deepcopy(self.game)
        rule_id = next(iter(game["gameplay"]["mechanics"]["checks"]))
        rule = game["gameplay"]["mechanics"]["checks"][rule_id]
        rule["target"] = 21  # Explicit synthetic fixture: every genuine 1d20 result fails.
        path = self.directory / "failure-session.json"
        dump_json(path, make_session(game))
        request = {"type": "roll", "id": "failure-check", "expected_revision": 0,
                   "summary": "synthetic failure fixture", "rule_id": rule_id,
                   "dice": rule["dice"], "modifier": rule.get("modifier", 0), "target": 21}
        receipt = append_request(game, path, request)["event"]["rolls"][0]
        self.assertEqual(receipt["outcome"], "failure")
        append_request(game, path, {"type": "action", "id": "failure-cost", "expected_revision": 1,
            "summary": "failure cost and continuing route", "rule_id": rule_id,
            "requires_check": {"event_id": "failure-check", "outcome": "failure"},
            "operations": rule["consequences"]["failure"][0]})
        state = load_json(path)["state"]
        self.assertEqual(state["player"]["stats"]["hp"], 8)
        self.assertEqual(state["location_id"], "L3")
        self.assertTrue(verify_session(game, load_json(path))["ok"])

    def test_interrupted_check_snapshot_resumes_receipt_without_reroll(self):
        rule_id = next(iter(self.game["gameplay"]["mechanics"]["checks"]))
        args = ("roll", self.game_path, self.session_path, "--event-id", "pending-check",
                "--expected-revision", "0", "--rule-id", rule_id)
        receipt = self.cli(*args)["event"]["rolls"]
        self.cli("snapshot", self.game_path, self.session_path)
        restored_path = next((self.directory / "snapshots").glob("*.json"))
        retry_args = list(args)
        retry_args[2] = restored_path
        self.assertEqual(self.cli(*retry_args)["event"]["rolls"], receipt)
        outcome = receipt[0]["outcome"]
        operations = self.game["gameplay"]["mechanics"]["checks"][rule_id]["consequences"][outcome][0]
        self.action("resume-pending", operations, path=restored_path, rule_id=rule_id,
                    requires_check={"event_id": "pending-check", "outcome": outcome})
        self.assertEqual(load_json(restored_path)["revision"], 2)
        self.assertTrue(verify_session(self.game, load_json(restored_path))["ok"])
        self.assertEqual(load_json(self.session_path)["revision"], 1)

    def test_zero_recall_and_explicit_context_output_remain_safe(self):
        self.remember("bounded", "SAFE_BUDGET_TOKEN", known_by=["player"])
        context = self.context("player", recall_limit=0, recall_chars=0)
        self.assertEqual(context["memory"]["important"], [])
        self.assertEqual(context["memory"]["recent_events"], [])
        with self.assertRaises(ValueError):
            self.context("gm", recall_limit=-1)
        out = self.directory / "explicit-context.json"
        self.cli("context", self.game_path, "--session", self.session_path, "--actor", "gm", "--out", out)
        before = out.read_bytes()
        self.cli("context", self.game_path, "--session", self.session_path, "--actor", "gm", "--out", out, success=False)
        self.assertEqual(before, out.read_bytes())

    def test_default_snapshot_filename_handles_windows_branch_and_tampering(self):
        fork = self.directory / "colon-branch.json"
        self.cli("fork", self.game_path, self.session_path, "--out", fork, "--branch", "alternate:one")
        self.cli("snapshot", self.game_path, fork)
        saved = next((self.directory / "snapshots").glob("*.json"))
        self.assertNotIn(":", saved.name)
        content = load_json(saved)
        content["state"]["player"]["stats"]["currency"] += 1
        dump_json(saved, content)
        before = saved.read_bytes()
        self.cli("snapshot", self.game_path, fork, success=False)
        self.assertEqual(before, saved.read_bytes())

    def test_default_snapshots_distinguish_independent_same_revision_sessions(self):
        second = self.directory / "other-session.json"
        self.cli("start", self.game_path, "--out", second)
        self.cli("snapshot", self.game_path, self.session_path)
        self.cli("snapshot", self.game_path, second)
        self.assertEqual(len(list((self.directory / "snapshots").glob("*.json"))), 2)

    def test_exported_runtime_multiturn_context_snapshot_and_restore(self):
        exported = self.directory / "exported"
        self.cli(self.game_path, "--out", exported, "--formats", "all", "--strict",
                 script=ROOT / "scripts" / "export_game.py")
        portable = exported / "ai-skill" / self.game["meta"]["id"]
        script = portable / "scripts" / "gamekit.py"
        game_path = portable / "game.json"
        session_path = portable / "session.json"
        self.cli("start", game_path, "--out", session_path, script=script)
        generated_skill = (portable / "SKILL.md").read_text(encoding="utf-8")
        command = re.search(r"`(python scripts/gamekit\.py context [^`]+)`", generated_skill)
        self.assertIsNotNone(command, "Generated skill must show its runnable context command")
        generated_args = shlex.split(command.group(1))[1:]
        for n in range(3):
            generated_result = subprocess.run([sys.executable, "-S", "-B", "-X", "utf8", *generated_args],
                cwd=portable, capture_output=True, text=True, encoding="utf-8", timeout=30)
            self.assertEqual(generated_result.returncode, 0, generated_result.stdout + generated_result.stderr)
            context = self.cli("context", game_path, "--session", session_path, "--actor", "gm", script=script)
            self.assertEqual(context["turn"], n)
            request = portable / ("action-" + str(n) + ".json")
            dump_json(request, {"id": "turn-" + str(n), "expected_revision": n,
                "summary": "actual committed local turn", "operations": [{"op": "flag", "name": "last_turn", "value": n}]})
            self.cli("apply", game_path, session_path, request, script=script)
            self.cli("snapshot", game_path, session_path, script=script)
        saves = sorted((portable / "snapshots").glob("*.json"))
        self.assertEqual(len(saves), 3)
        last = next(p for p in saves if load_json(p)["revision"] == 3)
        before = last.read_bytes()
        self.cli("snapshot", game_path, session_path, script=script)
        self.assertEqual(before, last.read_bytes())
        self.assertEqual(len(list((portable / "snapshots").glob("*.json"))), 3)
        self.cli("verify", game_path, last, script=script)
        live = self.cli("context", game_path, "--session", session_path, "--actor", "gm", script=script)
        restored = self.cli("context", game_path, "--session", last, "--actor", "gm", script=script)
        self.assertEqual(live, restored)
        self.assertFalse((portable / "context.json").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
