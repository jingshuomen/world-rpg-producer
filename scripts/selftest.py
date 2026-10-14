#!/usr/bin/env python3
"""Offline behavioral/format regression checks; uses temporary files only."""
from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from common import canonical, digest, dump_json, load_json, validate_game
from gamekit import (append_request, context_for, make_session, session_lock, verify_session)

ROOT = Path(__file__).resolve().parent.parent


class RPGTests(unittest.TestCase):
    def setUp(self):
        self.game = load_json(ROOT / "assets" / "example-game.json")
        self.tmp = tempfile.TemporaryDirectory(prefix="world-rpg-selftest-")
        self.directory = Path(self.tmp.name)
        self.session_path = self.directory / "session.json"
        dump_json(self.session_path, make_session(self.game))

    def tearDown(self):
        self.tmp.cleanup()

    def action(self, ident="action-1", revision=0, operations=None, **extras):
        return {"type": "action", "id": ident, "expected_revision": revision,
                "summary": "测试实际状态后果", "operations": operations or [], **extras}

    def assert_rejected_unchanged(self, request):
        before = self.session_path.read_bytes()
        with self.assertRaises((ValueError, TypeError)):
            append_request(self.game, self.session_path, request)
        self.assertEqual(before, self.session_path.read_bytes())

    def test_original_ready(self):
        self.assertTrue(validate_game(self.game, strict=True)["ok"])

    def test_four_adaptation_media_need_evidence(self):
        for medium in ("novel", "anime", "film", "tv"):
            with self.subTest(medium=medium):
                game = copy.deepcopy(self.game)
                game["meta"]["medium"] = medium
                self.assertFalse(validate_game(game, strict=True)["ok"])
                game["sources"] = [{"id": "S1", "title": "Structural test fixture, not live research",
                    "uri": "https://example.org/test-fixture", "kind": "primary", "status": "ok",
                    "retrieved_at": "2026-10-14", "continuity": game["meta"]["continuity"],
                    "independence_group": "fixture"}]
                claim = game["claims"][0]
                claim.update(classification="canon", verification="single_source",
                             source_refs=[{"source_id": "S1", "locator": "fixture section"}])
                self.assertTrue(validate_game(game, strict=True)["ok"])

    def test_mixed_continuity_needs_policy(self):
        game = copy.deepcopy(self.game)
        game["meta"]["medium"] = "mixed"
        self.assertFalse(validate_game(game)["ok"])

    def test_fake_verified_and_missing_source_fail(self):
        game = copy.deepcopy(self.game)
        game["claims"][0].update(classification="canon", verification="verified",
             source_refs=[{"source_id": "missing", "locator": "chapter 1"}])
        self.assertFalse(validate_game(game, strict=True)["ok"])

    def test_duplicate_ids_and_unsafe_slug_fail(self):
        game = copy.deepcopy(self.game)
        game["world"]["locations"][0]["id"] = game["claims"][0]["id"]
        self.assertFalse(validate_game(game)["ok"])
        game = copy.deepcopy(self.game)
        game["meta"]["id"] = "../../outside"
        self.assertFalse(validate_game(game)["ok"])

    def test_malformed_values_return_errors(self):
        cases = [None, [], {}, {"schema_version": "1.0", "meta": {"continuity": []}}]
        for path, value in [("sources", [None]), ("claims", [{"id": []}]), ("world", []), ("state", [])]:
            game = copy.deepcopy(self.game)
            game[path] = value
            cases.append(game)
        for case in cases:
            with self.subTest(case=str(case)[:80]):
                self.assertFalse(validate_game(case)["ok"])

    def test_nonfinite_and_duplicate_json_rejected(self):
        for text in ('{"x":NaN}', '{"x":1,"x":2}'):
            path = self.directory / "bad.json"
            path.write_text(text, encoding="utf-8")
            with self.assertRaises(ValueError):
                load_json(path)

    def test_huge_integer_does_not_crash(self):
        game = copy.deepcopy(self.game)
        game["state"]["player"]["stats"]["hp"] = 10 ** 1000
        self.assertFalse(validate_game(game)["ok"])

    def test_player_npc_gm_secret_and_future(self):
        player = context_for(self.game, self.game["state"], "player")
        npc1 = context_for(self.game, self.game["state"], "N1")
        npc2 = context_for(self.game, self.game["state"], "N2")
        gm = context_for(self.game, self.game["state"], "gm")
        self.assertNotIn('"C5"', canonical(player))
        self.assertNotIn('"C5"', canonical(npc1))
        self.assertIn('"C5"', canonical(npc2))
        self.assertIn('"C5"', canonical(gm))
        for context in (player, npc1, npc2, gm):
            self.assertNotIn('"C6"', canonical(context))
        self.assertNotIn("behavior", canonical(player))

    def test_retimed_future_entry_and_baseline_knowledge_rejected(self):
        game = copy.deepcopy(self.game)
        future = next(x for x in game["world"]["lore"] if "C6" in x["claim_ids"])
        future["available_from"] = 0
        self.assertFalse(validate_game(game)["ok"])
        game = copy.deepcopy(self.game)
        game["world"]["npcs"][0]["knowledge"].append("C6")
        self.assertFalse(validate_game(game)["ok"])
        game = copy.deepcopy(self.game)
        game["state"]["facts_known"].append("C6")
        self.assertFalse(validate_game(game)["ok"])

    def test_atomic_multioperation_and_negative_inventory(self):
        self.assert_rejected_unchanged(self.action(operations=[
            {"op": "resource", "name": "currency", "delta": 1},
            {"op": "item", "id": "不存在的钥匙", "delta": -1}]))
        self.assert_rejected_unchanged(self.action(operations=[{"op": "resource", "name": "hp", "delta": -100}]))

    def test_unknown_npc_backstory_withheld(self):
        game = copy.deepcopy(self.game)
        game["world"]["npcs"][0]["description"] += " AUDIT_UNKNOWN_SECRET"
        game["world"]["npcs"][0]["claim_ids"].append("C5")
        self.assertFalse(validate_game(game)["ok"])
        self.assertNotIn("AUDIT_UNKNOWN_SECRET", canonical(context_for(game, game["state"], "N1")))

    def test_duplicate_request_is_idempotent(self):
        request = self.action(operations=[{"op": "resource", "name": "currency", "delta": 2}])
        first = append_request(self.game, self.session_path, request)
        second = append_request(self.game, self.session_path, request)
        self.assertFalse(first["already_applied"])
        self.assertTrue(second["already_applied"])
        session = load_json(self.session_path)
        self.assertEqual(session["state"]["player"]["stats"]["currency"], 10)
        self.assertEqual(session["revision"], 1)
        self.assertEqual(session["state"]["turn"], 1)
        changed = copy.deepcopy(request)
        changed["operations"][0]["delta"] = 3
        self.assert_rejected_unchanged(changed)

    def test_stale_revision_and_lock_rejected(self):
        append_request(self.game, self.session_path, self.action())
        self.assert_rejected_unchanged(self.action("action-2", revision=0))
        with session_lock(self.session_path):
            self.assert_rejected_unchanged(self.action("action-2", revision=1))

    def test_roll_retry_and_single_consumption(self):
        request = {"type": "roll", "id": "check-1", "expected_revision": 0, "summary": "无失败目标的回归骰子",
                   "dice": "1d20", "modifier": 0, "target": 0}
        first = append_request(self.game, self.session_path, request)
        second = append_request(self.game, self.session_path, request)
        self.assertEqual(first["event"]["rolls"], second["event"]["rolls"])
        self.assertEqual(load_json(self.session_path)["state"]["turn"], 0)
        req = {"event_id": "check-1", "outcome": "success"}
        append_request(self.game, self.session_path, self.action("reward-1", 1,
            [{"op": "item", "id": "回归测试钥匙", "delta": 1}], requires_check=req))
        self.assert_rejected_unchanged(self.action("reward-2", 2,
            [{"op": "item", "id": "回归测试钥匙", "delta": 1}], requires_check=req))

    def test_tampered_snapshot_event_and_game_rejected(self):
        append_request(self.game, self.session_path, self.action())
        original = load_json(self.session_path)
        for mode in ("snapshot", "event", "initial"):
            session = copy.deepcopy(original)
            if mode == "snapshot":
                session["state"]["player"]["stats"]["currency"] += 1
            elif mode == "event":
                session["events"][0]["request"]["summary"] = "tampered"
            else:
                session["initial_state"]["player"]["stats"]["currency"] += 1
            with self.assertRaises(ValueError):
                verify_session(self.game, session)
        changed = copy.deepcopy(self.game)
        changed["meta"]["version"] += "-tampered"
        with self.assertRaises(ValueError):
            verify_session(changed, original)

    def test_future_discovery_rejected_then_available(self):
        self.assert_rejected_unchanged(self.action(operations=[{"op": "discover", "claim_id": "C6"}]))
        append_request(self.game, self.session_path, self.action(operations=[
            {"op": "advance", "ticks": 2}, {"op": "discover", "claim_id": "C6"}]))
        session = load_json(self.session_path)
        self.assertIn('"C6"', canonical(context_for(self.game, session["state"], "player")))

    def test_no_silent_resurrection_or_quest_reopen(self):
        append_request(self.game, self.session_path, self.action(operations=[
            {"op": "npc_status", "npc_id": "N1", "status": "dead"},
            {"op": "quest", "id": "Q1", "status": "complete"}]))
        self.assert_rejected_unchanged(self.action("revive", 1,
            [{"op": "npc_status", "npc_id": "N1", "status": "alive"}]))
        self.assert_rejected_unchanged(self.action("reopen", 1,
            [{"op": "quest", "id": "Q1", "status": "open"}]))

    def test_portable_export_roundtrip(self):
        exported = self.directory / "exported"
        result = subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "scripts" / "export_game.py"),
            str(ROOT / "assets" / "example-game.json"), "--out", str(exported), "--formats", "all", "--strict"],
            capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(load_json(exported / "tavern" / "character-v2.json")["spec"], "chara_card_v2")
        self.assertEqual(load_json(exported / "tavern" / "character-v3.json")["spec"], "chara_card_v3")
        portable = exported / "ai-skill" / self.game["meta"]["id"]
        result = subprocess.run([sys.executable, "-X", "utf8", str(portable / "scripts" / "gamekit.py"),
            "validate", str(portable / "game.json"), "--strict"], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
