#!/usr/bin/env python3
"""Validate RPG packages; run atomic, replay-checked campaign ledgers. Python 3.10+."""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import secrets
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from common import (IDENT, NPC_STATUS, QUEST_STATUS, canonical, dice_spec, digest, dump_json,
                    integer, load_json, number, validate_game, validate_memories,
                    validate_recall_metadata, validate_state)


def utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def checked_game(path, strict=False):
    game = load_json(path)
    result = validate_game(game, strict=strict)
    if not result["ok"]:
        raise ValueError("Invalid game: " + "; ".join(result["errors"]))
    return game


@contextmanager
def session_lock(path):
    """An exclusive sidecar serializes read/check/replace, including concurrent retries."""
    lock = Path(str(path) + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise ValueError(f"Session is locked: {lock}. Check its process before removing a stale lock.")
    try:
        os.write(fd, json.dumps({"pid": os.getpid(), "created_at": utc()}).encode())
        os.close(fd)
        yield
    finally:
        lock.unlink(missing_ok=True)


def safe_event_id(value):
    if not isinstance(value, str) or not IDENT.fullmatch(value):
        raise ValueError("event id must be a safe identifier")


def safe_label(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 128 or any(ord(c) < 32 for c in value):
        raise ValueError("data label must be nonempty text of at most 128 characters")


def make_session(game, branch_id="main", initial_state=None, parent=None):
    safe_event_id(branch_id)
    state = state_in_branch(initial_state if initial_state is not None else game["state"], branch_id)
    return {"schema_version": "1.0", "game_id": game["meta"]["id"], "game_hash": digest(game),
            "branch_id": branch_id, "created_at": utc(), "revision": 0,
            "parent": parent, "initial_state": copy.deepcopy(state), "state": state, "events": []}


def state_in_branch(state, branch_id):
    """Only copied memories change ownership; absent extension fields remain absent."""
    result = copy.deepcopy(state)
    result["branch_id"] = branch_id
    for memory in result.get("extensions", {}).get("memories", []):
        if memory.get("branch_id") != branch_id:
            memory.setdefault("origin_branch_id", memory.get("branch_id"))
            memory["branch_id"] = branch_id
    return result


def roll_spec(expression):
    return dice_spec(expression)


def check_rule(game, state, request):
    ident = request.get("rule_id")
    if ident is None:
        return None
    safe_event_id(ident)
    rule = game["gameplay"]["mechanics"].get("checks", {}).get(ident)
    if not isinstance(rule, dict):
        raise ValueError("unknown declared check rule: " + ident)
    preconditions = rule.get("preconditions", {})
    for name, minimum in preconditions.get("stats_min", {}).items():
        if state["player"]["stats"].get(name, float("-inf")) < minimum:
            raise ValueError("check rule precondition failed: resource " + name)
    for item, minimum in preconditions.get("inventory_min", {}).items():
        if state["player"]["inventory"].get(item, 0) < minimum:
            raise ValueError("check rule precondition failed: inventory " + item)
    for name, expected in preconditions.get("flags", {}).items():
        if name not in state["player"]["flags"] or canonical(state["player"]["flags"][name]) != canonical(expected):
            raise ValueError("check rule precondition failed: flag " + name)
    if request.get("type") == "roll":
        for key, expected in (("dice", rule["dice"].lower()), ("target", rule["target"]), ("modifier", rule.get("modifier", 0))):
            if request.get(key) != expected:
                raise ValueError("roll parameters disagree with declared check rule: " + key)
    elif request.get("type") == "action":
        req = request.get("requires_check")
        if not isinstance(req, dict) or req.get("outcome") not in {"success", "failure"}:
            raise ValueError("rule-bound action needs a recorded success/failure check")
        operations = request.get("operations")
        def matches_recipe(recipe):
            # Consequences remain exact; append-only memories can join the same atomic event.
            return (isinstance(operations, list) and len(operations) >= len(recipe)
                    and canonical(operations[:len(recipe)]) == canonical(recipe)
                    and all(isinstance(op, dict) and op.get("op") == "remember"
                            for op in operations[len(recipe):]))
        if not any(matches_recipe(ops) for ops in rule["consequences"][req["outcome"]]):
            raise ValueError("action consequences disagree with declared check rule")
    return rule


def check_roll(roll):
    if not isinstance(roll, dict):
        raise ValueError("roll must be an object")
    count, sides = roll_spec(roll.get("dice"))
    values = roll.get("values")
    if not isinstance(values, list) or len(values) != count or any(not integer(x) or not 1 <= x <= sides for x in values):
        raise ValueError("invalid recorded dice faces")
    modifier, target = roll.get("modifier"), roll.get("target")
    if not number(modifier) or abs(modifier) > 1000000 or not number(target):
        raise ValueError("invalid roll modifier/target")
    if roll.get("total") != sum(values) + modifier:
        raise ValueError("recorded dice total mismatch")
    expected = "success" if roll["total"] >= target else "failure"
    if roll.get("outcome") != expected:
        raise ValueError("recorded dice outcome mismatch")


def apply_operations(game, state, operations):
    if not isinstance(operations, list) or len(operations) > 100:
        raise ValueError("operations must be an array of at most 100 operations")
    state = copy.deepcopy(state)
    player = state["player"]
    world = game["world"]
    resources = game["gameplay"]["mechanics"]["resources"]
    npcs = {x["id"]: x for x in world["npcs"]}
    locations = {x["id"]: x for x in world["locations"]}
    quests = {x["id"]: x for x in game["gameplay"]["quests"]}
    claims = {x["id"]: x for x in game["claims"]}
    for operation in operations:
        if not isinstance(operation, dict):
            raise ValueError("operation must be an object")
        kind = operation.get("op")
        if kind == "resource":
            name, delta = operation.get("name"), operation.get("delta")
            if name not in resources or not number(delta):
                raise ValueError("resource operation needs a declared resource and finite delta")
            player["stats"][name] += delta
        elif kind == "item":
            item, delta = operation.get("id"), operation.get("delta")
            safe_label(item)
            if not integer(delta):
                raise ValueError("item delta must be an integer")
            quantity = player["inventory"].get(item, 0) + delta
            if quantity < 0:
                raise ValueError("cannot spend unavailable inventory: " + item)
            if quantity:
                player["inventory"][item] = quantity
            else:
                player["inventory"].pop(item, None)
        elif kind == "flag":
            name = operation.get("name")
            safe_event_id(name)
            value = operation.get("value")
            if not (value is None or isinstance(value, (str, bool)) or number(value)):
                raise ValueError("flag value must be a finite scalar")
            player["flags"][name] = value
        elif kind == "relation":
            npc, dim, delta = operation.get("npc_id"), operation.get("dimension", "trust"), operation.get("delta")
            if npc not in npcs or not number(delta):
                raise ValueError("relationship operation needs a known NPC and finite delta")
            safe_event_id(dim)
            dims = player["relations"].setdefault(npc, {})
            dims[dim] = dims.get(dim, 0) + delta
        elif kind == "quest":
            qid, status = operation.get("id"), operation.get("status")
            if qid not in quests or status not in QUEST_STATUS:
                raise ValueError("unknown quest or quest status")
            old = state["quests"].get(qid, quests[qid]["status"])
            if old in {"complete", "failed"} and status != old and not game.get("extensions", {}).get("allow_quest_reopen"):
                raise ValueError("terminal quest cannot reopen without an explicit game rule")
            state["quests"][qid] = status
        elif kind == "move":
            lid = operation.get("location_id")
            if lid not in locations or locations[lid].get("available_from", 0) > state["time"]["tick"]:
                raise ValueError("unknown or not-yet-available location")
            state["location_id"] = lid
        elif kind == "npc_status":
            npc, status = operation.get("npc_id"), operation.get("status")
            if npc not in npcs or status not in NPC_STATUS:
                raise ValueError("unknown NPC or NPC status")
            old = state["npc_status"].get(npc, npcs[npc]["status"])
            if old == "dead" and status != "dead" and not game.get("extensions", {}).get("allow_resurrection"):
                raise ValueError("dead NPC cannot return without an explicit resurrection rule")
            state["npc_status"][npc] = status
        elif kind == "discover":
            cid, npc = operation.get("claim_id"), operation.get("npc_id")
            cap = min(state["time"]["tick"], game["meta"]["cutoff_tick"])
            if cid not in claims or claims[cid].get("available_from", 0) > cap:
                raise ValueError("unknown or future claim cannot be discovered")
            if npc is None:
                facts = state["facts_known"]
            elif npc in npcs:
                facts = state.setdefault("extensions", {}).setdefault("npc_knowledge", {}).setdefault(npc, [])
            else:
                raise ValueError("unknown NPC knowledge owner")
            if cid not in facts:
                facts.append(cid)
        elif kind == "advance":
            ticks = operation.get("ticks")
            if not integer(ticks) or ticks < 0 or ticks > 1000000:
                raise ValueError("advance ticks must be 0-1000000; rewind using a branch")
            state["time"]["tick"] += ticks
            if "label" in operation:
                if not isinstance(operation["label"], str):
                    raise ValueError("time label must be text")
                state["time"]["label"] = operation["label"]
        elif kind == "remember":
            memory = {"id": operation.get("id"), "text": operation.get("text"),
                      "visibility": operation.get("visibility"), "known_by": copy.deepcopy(operation.get("known_by", [])),
                      "importance": operation.get("importance", 1), "tick": state["time"]["tick"],
                      "branch_id": state["branch_id"]}
            errors = validate_memories([memory], game, state["time"]["tick"], state["branch_id"])
            if errors:
                raise ValueError("; ".join(errors))
            memories = state.setdefault("extensions", {}).setdefault("memories", [])
            if any(existing["id"] == memory["id"] for existing in memories):
                raise ValueError("memory id already exists; retry the original event rather than rewrite history")
            memories.append(memory)
        else:
            raise ValueError("unsupported operation: " + str(kind))
        errors = validate_state(state, game)
        if errors:
            raise ValueError("; ".join(errors))
    return state


def consume_check(state, request, history):
    req = request.get("requires_check")
    if req is None:
        return state
    if not isinstance(req, dict) or req.get("outcome") not in {"success", "failure"}:
        raise ValueError("requires_check needs event_id and success/failure outcome")
    found = next((x for x in history if x.get("id") == req.get("event_id")), None)
    if not found or found["request"].get("type") != "roll" or len(found.get("rolls", [])) != 1:
        raise ValueError("required check is not a recorded roll event")
    if found["request"].get("rule_id") != request.get("rule_id"):
        raise ValueError("action and recorded check must bind the same rule_id")
    if found["rolls"][0]["outcome"] != req["outcome"]:
        raise ValueError("required check outcome does not match recorded dice")
    resolved = state.setdefault("extensions", {}).setdefault("resolved_checks", [])
    if req["event_id"] in resolved:
        raise ValueError("check consequences already resolved")
    resolved.append(req["event_id"])
    return state


def reduce_event(game, state, request, rolls, history):
    check_rule(game, state, request)
    if request.get("type") == "roll":
        if len(rolls) != 1:
            raise ValueError("roll event requires exactly one recorded roll")
        check_roll(rolls[0])
        for key in ("dice", "modifier", "target"):
            if rolls[0][key] != request.get(key):
                raise ValueError("roll request and receipt mismatch")
        state = copy.deepcopy(state)
        state.setdefault("extensions", {})["last_check"] = {"event_id": request["id"], **rolls[0]}
    elif request.get("type") == "action":
        if rolls:
            raise ValueError("action events cannot supply arbitrary dice receipts")
        state = consume_check(copy.deepcopy(state), request, history)
        state = apply_operations(game, state, request.get("operations"))
        state["turn"] += 1
    else:
        raise ValueError("unknown event type")
    errors = validate_state(state, game)
    if "recall" in request:
        errors.extend(validate_recall_metadata(request["recall"], game, state["time"]["tick"]))
        if not isinstance(request.get("summary"), str) or not request["summary"].strip() or len(request["summary"]) > 2000:
            errors.append("recall summary must contain 1-2000 characters")
    if errors:
        raise ValueError("; ".join(errors))
    return state


def verify_session(game, session, _timeline=None):
    if not isinstance(session, dict) or session.get("schema_version") != "1.0":
        raise ValueError("invalid session schema")
    if session.get("game_id") != game["meta"]["id"] or session.get("game_hash") != digest(game):
        raise ValueError("session is bound to a different game/version; migrate explicitly")
    safe_event_id(session.get("branch_id"))
    state = copy.deepcopy(session.get("initial_state"))
    errors = validate_state(state, game)
    if errors:
        raise ValueError("invalid initial state: " + "; ".join(errors))
    if state["branch_id"] != session["branch_id"]:
        raise ValueError("session branch identity mismatch")
    parent = session.get("parent")
    if parent is None:
        expected = state_in_branch(game["state"], session["branch_id"])
        if state != expected:
            raise ValueError("root initial state does not match game")
    elif not isinstance(parent, dict) or parent.get("game_hash") != digest(game) or parent.get("state_hash") != digest(state):
        raise ValueError("fork parent receipt mismatch")
    events = session.get("events")
    if not isinstance(events, list):
        raise ValueError("events must be an array")
    seen, previous, history = set(), "0" * 64, []
    for index, event in enumerate(events):
        if not isinstance(event, dict) or not isinstance(event.get("request"), dict):
            raise ValueError("malformed ledger event")
        safe_event_id(event.get("id"))
        request = event["request"]
        if event["id"] in seen or request.get("id") != event["id"]:
            raise ValueError("duplicate or mismatched event id")
        if request.get("expected_revision") != index:
            raise ValueError("ledger revision mismatch")
        if event.get("previous_hash") != previous or event.get("before_hash") != digest(state):
            raise ValueError("ledger hash continuity mismatch")
        body = {k: v for k, v in event.items() if k != "event_hash"}
        if digest(body) != event.get("event_hash"):
            raise ValueError("event hash mismatch")
        state = reduce_event(game, state, request, event.get("rolls", []), history)
        if event.get("after_hash") != digest(state):
            raise ValueError("event state hash mismatch")
        seen.add(event["id"])
        previous = event["event_hash"]
        history.append(event)
        if _timeline is not None:
            _timeline.append((event, state["time"]["tick"]))
    if not integer(session.get("revision")) or session["revision"] != len(events) or session.get("state") != state:
        raise ValueError("session snapshot and replayed ledger disagree")
    return {"ok": True, "revision": len(events), "branch_id": session["branch_id"],
            "state_hash": digest(state), "head_hash": previous,
            "scope": "local structural integrity and deterministic replay; not a signed external authenticity proof"}


def append_request(game, session_path, request):
    safe_event_id(request.get("id"))
    if not integer(request.get("expected_revision")) or request["expected_revision"] < 0:
        raise ValueError("expected_revision must be a nonnegative integer")
    if not isinstance(request.get("summary"), str) or not request["summary"].strip():
        raise ValueError("summary is required")
    with session_lock(session_path):
        session = load_json(session_path)
        verify_session(game, session)
        existing = next((x for x in session["events"] if x["id"] == request["id"]), None)
        if existing:
            if existing["request"] != request:
                raise ValueError("event id already exists with different content")
            return {"ok": True, "already_applied": True, "revision": session["revision"], "event": existing}
        if request["expected_revision"] != session["revision"]:
            raise ValueError(f"stale revision: expected {request['expected_revision']}, actual {session['revision']}")
        rolls = []
        check_rule(game, session["state"], request)
        if request["type"] == "roll":
            count, sides = roll_spec(request["dice"])
            values = [secrets.randbelow(sides) + 1 for _ in range(count)]
            total = sum(values) + request["modifier"]
            rolls = [{"dice": request["dice"], "values": values, "modifier": request["modifier"],
                      "target": request["target"], "total": total,
                      "outcome": "success" if total >= request["target"] else "failure"}]
            check_roll(rolls[0])
        before = session["state"]
        after = reduce_event(game, before, request, rolls, session["events"])
        event = {"id": request["id"], "created_at": utc(), "request": request, "rolls": rolls,
                 "before_hash": digest(before), "after_hash": digest(after),
                 "previous_hash": session["events"][-1]["event_hash"] if session["events"] else "0" * 64}
        event["event_hash"] = digest(event)
        session["events"].append(event)
        session["revision"] += 1
        session["state"] = after
        verify_session(game, session)
        dump_json(session_path, session)
        return {"ok": True, "already_applied": False, "revision": session["revision"], "event": event}


def visible_recall(metadata, actor):
    if actor == "gm":
        return True
    visibility = metadata.get("visibility", "gm")
    if visibility == "gm":
        return False
    return actor in metadata.get("known_by", []) or (actor == "player" and visibility == "public")


def recall_context(game, state, actor, session, limit, characters):
    if not integer(limit) or not 0 <= limit <= 40 or not integer(characters) or not 0 <= characters <= 20000:
        raise ValueError("recall limits: 0-40 entries and 0-20000 text characters")
    result = {"important": [], "recent_events": [], "limits": {"entries": limit, "text_characters": characters}}
    memories = [m for m in state.get("extensions", {}).get("memories", [])
                if m.get("branch_id") == state["branch_id"] and m.get("tick", -1) <= state["time"]["tick"]
                and visible_recall(m, actor)]
    memories.sort(key=lambda m: (m.get("importance", 1), m["tick"], m["id"]), reverse=True)
    recent = []
    if session is not None:
        timeline = []
        verify_session(game, session, _timeline=timeline)
        if session["state"] != state or session["branch_id"] != state["branch_id"]:
            raise ValueError("recall session does not match the supplied state/branch")
        for event, tick in reversed(timeline):
            request = event["request"]
            if tick <= state["time"]["tick"] and visible_recall(request.get("recall", {}), actor):
                recent.append({"id": event["id"], "summary": request.get("summary", ""),
                               "tick": tick, "type": request["type"]})
                if len(recent) >= limit:
                    break
    remaining_entries, remaining_chars = limit, characters
    def add(value, group, text_key, text_budget=None):
        nonlocal remaining_entries, remaining_chars
        available = remaining_chars if text_budget is None else min(remaining_chars, text_budget)
        if not remaining_entries or not available:
            return False
        value = copy.deepcopy(value)
        text = value[text_key]
        if len(text) > available:
            value[text_key] = text[:available]
            value["truncated"] = True
        remaining_chars -= len(value[text_key])
        remaining_entries -= 1
        result[group].append(value)
        return True
    # Reserve both entry and character space for recent developments.
    split = (limit + 1) // 2 if recent else limit
    memory_chars = characters // 2 if recent else characters
    selected = 0
    for memory in memories[:split]:
        before_chars = remaining_chars
        if add({k: memory[k] for k in ("id", "text", "tick") } | {"importance": memory.get("importance", 1)}, "important", "text", memory_chars):
            selected += 1
            memory_chars -= before_chars - remaining_chars
    for event in recent:
        add(event, "recent_events", "summary")
    for memory in memories[selected:]:
        add({k: memory[k] for k in ("id", "text", "tick")} | {"importance": memory.get("importance", 1)}, "important", "text")
    return result


def context_for(game, state, actor, session=None, recall_limit=12, recall_chars=6000):
    errors = validate_state(state, game)
    if errors:
        raise ValueError("invalid context state: " + "; ".join(errors))
    cap = min(state["time"]["tick"], game["meta"]["cutoff_tick"])
    current = lambda x: x.get("available_from", 0) <= cap
    claims = {c["id"]: c for c in game["claims"] if current(c)}
    npc_map = {n["id"]: n for n in game["world"]["npcs"] if current(n)}
    if actor not in {"gm", "player"} and actor not in npc_map:
        raise ValueError("unknown or future actor")
    public_ids = set()
    for group in ("locations", "factions", "lore"):
        for entity in game["world"][group]:
            if current(entity) and entity.get("visibility") == "public":
                public_ids.update(entity.get("claim_ids", []))
    if actor == "gm":
        known = set(claims)
    elif actor == "player":
        known = public_ids | set(state["facts_known"])
    else:
        known = set(npc_map[actor]["knowledge"])
        known.update(state.get("extensions", {}).get("npc_knowledge", {}).get(actor, []))
        for lore in game["world"]["lore"]:
            if current(lore) and actor in lore.get("known_by", []):
                known.update(lore.get("claim_ids", []))
    known &= set(claims)
    def clean_entity(entity):
        result = copy.deepcopy(entity)
        # Never pass hidden knowledge, goals, behavior or source excerpts via a generic dossier.
        result.pop("knowledge", None)
        result.pop("known_by", None)
        result["claim_ids"] = [cid for cid in result.get("claim_ids", []) if cid in known]
        return result
    result = {"game_id": game["meta"]["id"], "actor": actor, "time": copy.deepcopy(state["time"]),
              "turn": state["turn"], "continuity": game["meta"]["continuity"],
              "scope": "Filtered context is a tool boundary, not secrecy from a shared-chat model or file reader.",
              "facts": [{"id": cid, "text": claims[cid]["text"], "classification": claims[cid]["classification"]}
                        for cid in sorted(known)], "world": {}}
    for group in ("locations", "factions", "lore"):
        visible = []
        for entity in game["world"][group]:
            if not current(entity):
                continue
            refs = set(entity.get("claim_ids", []))
            if refs - set(claims):
                continue  # Defensive even for a direct caller bypassing package validation.
            permitted = actor == "gm" or (actor == "player" and entity.get("visibility") == "public")
            permitted |= bool(refs) and refs <= known
            if actor not in {"gm", "player"} and actor in entity.get("known_by", []):
                permitted = True
            if permitted:
                visible.append(clean_entity(entity))
        result["world"][group] = visible
    if actor == "gm":
        result["world"]["npcs"] = [copy.deepcopy(n) for n in npc_map.values()]
        result["state"] = copy.deepcopy(state)
        # The full archive stays in the session; context includes only the bounded recall below.
        result["state"].get("extensions", {}).pop("memories", None)
        result["rules"] = copy.deepcopy(game["gameplay"])
    elif actor == "player":
        result["player"] = copy.deepcopy(state["player"])
        result["quests"] = copy.deepcopy(state["quests"])
        result["location_id"] = state["location_id"]
        result["world"]["npcs"] = [{"id": n["id"], "name": n["name"],
                                      "status": state["npc_status"].get(n["id"], n["status"])}
                                     for n in npc_map.values()]
    else:
        npc = copy.deepcopy(npc_map[actor])
        if set(npc.get("claim_ids", [])) - known:
            npc = {"id": npc["id"], "name": npc["name"], "status": npc["status"],
                   "warning": "NPC dossier withheld because its provenance references unknown facts."}
        npc["knowledge"] = sorted(known)
        npc["status"] = state["npc_status"].get(actor, npc["status"])
        npc["relation_to_player"] = copy.deepcopy(state["player"]["relations"].get(actor, {}))
        result["self"] = npc
    result["memory"] = recall_context(game, state, actor, session, recall_limit, recall_chars)
    return result


def draft(args):
    today = datetime.now(timezone.utc).date().isoformat()
    slug = args.id or "new-rpg-game"
    return {"schema_version": "1.0", "meta": {"id": slug, "title": args.title, "work": args.work,
            "medium": args.medium, "continuity": args.continuity, "cutoff": args.cutoff,
            "cutoff_tick": args.cutoff_tick, "language": "zh-CN", "version": "1.0.0",
            "created_at": today, "research_completed_at": "", "status": "draft"},
            "sources": [], "claims": [], "world": {"premise": "", "rules": [], "locations": [],
            "factions": [], "lore": [], "npcs": []}, "gameplay": {"gm_rules": [], "loops": [],
            "failure_policy": "", "mechanics": {"dice": "d20", "resources": {"hp": {"min": 0, "max": 10},
            "currency": {"min": 0, "max": 1000000}}}, "opening": {"scene": "", "choices": []},
            "quests": [], "endings": []}, "state": {"branch_id": "main", "turn": 0,
            "time": {"tick": 0, "label": "开局"}, "location_id": "", "player": {"name": "由玩家决定",
            "identity": "独立新角色", "stats": {"hp": 10, "currency": 0}, "inventory": {}, "flags": {},
            "relations": {}}, "npc_status": {}, "quests": {}, "facts_known": [], "extensions": {}}, "extensions": {}}


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="create an explicitly incomplete research draft")
    init.add_argument("--out", required=True)
    init.add_argument("--id")
    init.add_argument("--title", required=True)
    init.add_argument("--work", required=True)
    init.add_argument("--medium", required=True, choices=["novel", "anime", "film", "tv", "mixed", "original"])
    init.add_argument("--continuity", required=True)
    init.add_argument("--cutoff", required=True)
    init.add_argument("--cutoff-tick", type=int, default=0)
    validate = commands.add_parser("validate")
    validate.add_argument("game")
    validate.add_argument("--strict", action="store_true")
    start = commands.add_parser("start")
    start.add_argument("game")
    start.add_argument("--out", required=True)
    for name in ("verify", "apply", "roll", "fork", "snapshot"):
        command = commands.add_parser(name)
        command.add_argument("game")
        command.add_argument("session")
        if name == "apply":
            command.add_argument("event")
        if name == "fork":
            command.add_argument("--out", required=True)
        if name == "snapshot":
            command.add_argument("--out")
        if name == "fork":
            command.add_argument("--branch", required=True)
        if name == "roll":
            command.add_argument("--event-id", required=True)
            command.add_argument("--expected-revision", required=True, type=int)
            command.add_argument("--dice")
            command.add_argument("--modifier", type=float)
            command.add_argument("--target", type=float)
            command.add_argument("--rule-id")
            command.add_argument("--visibility", choices=["gm", "private", "public"])
            command.add_argument("--known-by", nargs="*", default=[])
            command.add_argument("--summary", default="规则判定")
    context = commands.add_parser("context")
    context.add_argument("game")
    context.add_argument("--session")
    context.add_argument("--actor", choices=None, required=True)
    context.add_argument("--out")
    context.add_argument("--recall-limit", type=int, default=12)
    context.add_argument("--recall-chars", type=int, default=6000)
    return parser


def no_overwrite(path):
    if Path(path).exists():
        raise ValueError("Refusing to overwrite existing file: " + str(path))


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.command == "init":
            no_overwrite(args.out)
            data = draft(args)
            if not re.fullmatch(r"[a-z][a-z0-9-]{0,62}", data["meta"]["id"]):
                raise ValueError("invalid game id")
            dump_json(args.out, data)
            result = {"ok": True, "status": "draft", "path": str(Path(args.out).resolve()),
                      "next": "Research and fill this draft. Empty drafts cannot be exported."}
        elif args.command == "validate":
            result = validate_game(load_json(args.game), strict=args.strict)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["ok"] else 2
        else:
            game = checked_game(args.game)
            if args.command == "start":
                with session_lock(args.out):
                    no_overwrite(args.out)
                    session = make_session(game, game["state"]["branch_id"])
                    result = verify_session(game, session)
                    dump_json(args.out, session)
            elif args.command == "context":
                session = None
                if args.session:
                    session = load_json(args.session)
                    state = session["state"]
                else:
                    state = game["state"]
                result = context_for(game, state, args.actor, session=session,
                                     recall_limit=args.recall_limit, recall_chars=args.recall_chars)
                if args.out:
                    no_overwrite(args.out)
                    dump_json(args.out, result)
            elif args.command == "apply":
                request = load_json(args.event)
                if not isinstance(request, dict):
                    raise ValueError("event request must be an object")
                request = copy.deepcopy(request)
                request.setdefault("type", "action")
                if request["type"] != "action":
                    raise ValueError("apply accepts action events; use roll for checks")
                result = append_request(game, args.session, request)
            elif args.command == "roll":
                rule = game["gameplay"]["mechanics"].get("checks", {}).get(args.rule_id) if args.rule_id else None
                if args.rule_id and not isinstance(rule, dict):
                    raise ValueError("unknown declared check rule: " + args.rule_id)
                dice = args.dice if args.dice is not None else (rule["dice"] if rule else "1d20")
                modifier = args.modifier if args.modifier is not None else (rule.get("modifier", 0) if rule else 0)
                target = args.target if args.target is not None else (rule["target"] if rule else None)
                if target is None:
                    raise ValueError("roll requires --target unless --rule-id supplies it")
                request = {"type": "roll", "id": args.event_id, "expected_revision": args.expected_revision,
                           "summary": args.summary, "dice": dice.lower(), "modifier": modifier,
                           "target": target}
                if args.rule_id:
                    request["rule_id"] = args.rule_id
                if args.visibility:
                    request["recall"] = {"visibility": args.visibility, "known_by": args.known_by}
                elif args.known_by:
                    raise ValueError("--known-by needs explicit --visibility")
                result = append_request(game, args.session, request)
            else:
                session = load_json(args.session)
                result = verify_session(game, session)
                if args.command == "fork":
                    if args.branch == session["branch_id"]:
                        raise ValueError("fork needs a different branch id")
                    with session_lock(args.out):
                        no_overwrite(args.out)
                        state = state_in_branch(session["state"], args.branch)
                        parent = {"branch_id": session["branch_id"], "revision": session["revision"],
                                  "head_hash": result["head_hash"], "state_hash": digest(state),
                                  "game_hash": digest(game)}
                        fork = make_session(game, args.branch, state, parent)
                        result = verify_session(game, fork)
                        dump_json(args.out, fork)
                elif args.command == "snapshot":
                    branch = re.sub(r"[^A-Za-z0-9_-]", "-", session["branch_id"])[:48]
                    source_id = digest(str(Path(args.session).resolve()))[:10]
                    filename = f"session-{source_id}-branch-{branch}-{digest(session['branch_id'])[:10]}-r{session['revision']}.json"
                    destination = Path(args.out) if args.out else Path(args.session).parent / "snapshots" / filename
                    with session_lock(destination):
                        already_saved = False
                        if args.out:
                            no_overwrite(destination)
                        elif destination.exists():
                            saved = load_json(destination)
                            verify_session(game, saved)
                            if saved != session:
                                raise ValueError("default snapshot path already contains different content")
                            already_saved = True
                        if not already_saved:
                            dump_json(destination, session)
                    result = {**result, "path": str(destination.resolve()), "already_saved": already_saved}
                elif args.command != "verify":
                    raise ValueError("unknown command")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, TypeError, KeyError, AttributeError, OverflowError, RecursionError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
