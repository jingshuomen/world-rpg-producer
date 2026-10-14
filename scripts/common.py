"""Shared, dependency-free validation and atomic JSON I/O. Python 3.10+."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from pathlib import Path

SLUG = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
IDENT = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,63}$")
MEDIA = {"novel", "anime", "film", "tv", "mixed", "original"}
KINDS = {"official", "primary", "reference", "community", "user"}
SOURCE_STATUS = {"ok", "partial", "failed", "blocked", "candidate"}
CLASSIFICATIONS = {"canon", "interpretation", "inference", "design"}
VERIFICATIONS = {"verified", "single_source", "disputed", "unsupported", "not_applicable"}
QUEST_STATUS = {"open", "active", "complete", "failed"}
NPC_STATUS = {"alive", "dead", "missing", "inactive", "unknown"}


def load_json(path):
    def reject_constant(value):
        raise ValueError(f"Non-finite JSON constant: {value}")
    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result
    with open(path, encoding="utf-8-sig") as handle:
        return json.load(handle, parse_constant=reject_constant, object_pairs_hook=unique_keys)


def canonical(data):
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(data):
    return hashlib.sha256(canonical(data).encode("utf-8")).hexdigest()


def dump_json(path, data):
    """Replace one file atomically; callers decide overwrite authorization."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix="." + path.name + ".", suffix=".tmp", delete=False) as handle:
            temp_name = handle.name
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)


def number(value):
    # Python integers are finite without a lossy float conversion (which may overflow).
    return type(value) is int or (type(value) is float and math.isfinite(value))


def integer(value):
    return type(value) is int


def dice_spec(expression):
    match = re.fullmatch(r"([1-9][0-9]?)?d([1-9][0-9]{0,3})", expression or "", re.I)
    if not match:
        raise ValueError("dice must be NdM, e.g. 1d20 or 2d6")
    count, sides = int(match.group(1) or 1), int(match.group(2))
    if not 1 <= count <= 20 or not 2 <= sides <= 1000:
        raise ValueError("dice limits: 1-20 dice, 2-1000 sides")
    return count, sides


def validate_recall_metadata(value, game, tick, label="recall"):
    """Explicit audiences only. Public knowledge does not make every NPC a witness."""
    errors = []
    if not isinstance(value, dict):
        return [label + " must be an object"]
    visibility, owners = value.get("visibility"), value.get("known_by", [])
    if visibility not in {"gm", "private", "public"}:
        errors.append(label + ".visibility must be gm, private or public")
    if not isinstance(owners, list) or any(not isinstance(x, str) for x in owners):
        return errors + [label + ".known_by must be an actor ID array"]
    if len(owners) != len(set(owners)):
        errors.append(label + ".known_by must not repeat actors")
    if visibility == "gm" and owners:
        errors.append(label + ": GM-only text cannot declare non-GM owners")
    if visibility == "private" and not owners:
        errors.append(label + ": private text needs explicit known_by actors")
    npcs = {n["id"]: n for n in game.get("world", {}).get("npcs", []) if isinstance(n, dict)}
    for owner in owners:
        if owner == "player":
            continue
        if owner not in npcs or npcs[owner].get("available_from", 0) > tick:
            errors.append(label + ": unknown or not-yet-available knowledge owner " + owner)
    return errors


def validate_memories(memories, game, tick, branch_id):
    if not isinstance(memories, list) or len(memories) > 10000:
        return ["state.extensions.memories must be an array of at most 10000 entries"]
    errors, seen = [], set()
    for memory in memories:
        if not isinstance(memory, dict):
            errors.append("memory must be an object")
            continue
        ident = memory.get("id")
        if not isinstance(ident, str) or not IDENT.fullmatch(ident):
            errors.append("memory.id must be a safe identifier")
        elif ident in seen:
            errors.append("duplicate memory id: " + ident)
        else:
            seen.add(ident)
        text = memory.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > 2000:
            errors.append("memory.text must contain 1-2000 characters")
        when = memory.get("tick")
        if not integer(when) or when < 0 or when > tick:
            errors.append("memory.tick must be a nonnegative past/current story tick")
        elif isinstance(memory.get("branch_id"), str):
            errors.extend(validate_recall_metadata(memory, game, when, "memory " + str(ident)))
        if memory.get("branch_id") != branch_id:
            errors.append("memory belongs to another branch")
        priority = memory.get("importance", 1)
        if not integer(priority) or not 0 <= priority <= 5:
            errors.append("memory.importance must be an integer from 0 to 5")
    return errors


def validate_operation_recipe(operation, game, mechanics):
    """Check static payloads; current resource bounds and eligibility remain runtime checks."""
    if not isinstance(operation, dict):
        return ["check consequence operation must be an object"]
    world = game.get("world", {}) if isinstance(game.get("world"), dict) else {}
    def entity_ids(container, group):
        values = container.get(group, [])
        return {x.get("id") for x in values if isinstance(x, dict)} if isinstance(values, list) else set()
    npcs = entity_ids(world, "npcs")
    locations = entity_ids(world, "locations")
    quests = entity_ids(game.get("gameplay", {}), "quests")
    claims = entity_ids(game, "claims")
    def ident(value):
        return isinstance(value, str) and IDENT.fullmatch(value) is not None
    def label(value):
        return isinstance(value, str) and bool(value.strip()) and len(value) <= 128 and all(ord(c) >= 32 for c in value)
    def scalar(value):
        return value is None or isinstance(value, (str, bool)) or number(value)
    kind = operation.get("op")
    valid = False
    if kind == "resource":
        valid = operation.get("name") in mechanics.get("resources", {}) and number(operation.get("delta"))
    elif kind == "item":
        valid = label(operation.get("id")) and integer(operation.get("delta"))
    elif kind == "flag":
        valid = ident(operation.get("name")) and "value" in operation and scalar(operation["value"])
    elif kind == "relation":
        valid = operation.get("npc_id") in npcs and ident(operation.get("dimension", "trust")) and number(operation.get("delta"))
    elif kind == "quest":
        valid = operation.get("id") in quests and operation.get("status") in QUEST_STATUS
    elif kind == "move":
        valid = operation.get("location_id") in locations
    elif kind == "npc_status":
        valid = operation.get("npc_id") in npcs and operation.get("status") in NPC_STATUS
    elif kind == "discover":
        valid = operation.get("claim_id") in claims and (operation.get("npc_id") is None or operation["npc_id"] in npcs)
    elif kind == "advance":
        ticks = operation.get("ticks")
        valid = integer(ticks) and 0 <= ticks <= 1000000 and ("label" not in operation or isinstance(operation["label"], str))
    elif kind == "remember":
        text = operation.get("text")
        importance = operation.get("importance", 1)
        valid = ident(operation.get("id")) and isinstance(text, str) and bool(text.strip()) and len(text) <= 2000
        valid &= integer(importance) and 0 <= importance <= 5
        cap = game.get("meta", {}).get("cutoff_tick", 0)
        errors = validate_recall_metadata(operation, game, cap, "remember consequence")
        if errors:
            return errors
    return [] if valid else ["invalid check consequence payload or reference: " + str(kind)]


def validate_check_rules(mechanics, game):
    """Optional exact recipes; intentionally no natural-language rule interpreter."""
    if "checks" not in mechanics:
        return []
    checks = mechanics["checks"]
    if not isinstance(checks, dict) or len(checks) > 100:
        return ["mechanics.checks must be an object of at most 100 rules"]
    errors = []
    allowed_ops = {"resource", "item", "flag", "relation", "quest", "move", "npc_status", "discover", "advance", "remember"}
    for ident, rule in checks.items():
        label = "check rule " + str(ident)
        if not isinstance(ident, str) or not IDENT.fullmatch(ident) or not isinstance(rule, dict):
            errors.append(label + " needs a safe ID and object")
            continue
        try:
            dice_spec(rule.get("dice"))
        except (TypeError, ValueError):
            errors.append(label + " has invalid dice")
        if not number(rule.get("target")) or not number(rule.get("modifier", 0)) or abs(rule.get("modifier", 0)) > 1000000:
            errors.append(label + " needs a finite target and bounded modifier")
        preconditions = rule.get("preconditions", {})
        if not isinstance(preconditions, dict) or set(preconditions) - {"stats_min", "inventory_min", "flags"}:
            errors.append(label + " has invalid preconditions")
        else:
            for group in ("stats_min", "inventory_min", "flags"):
                values = preconditions.get(group, {})
                if not isinstance(values, dict):
                    errors.append(label + "." + group + " must be an object")
                    continue
                for name, value in values.items():
                    valid = isinstance(name, str) and bool(name.strip())
                    if group == "stats_min":
                        valid &= name in mechanics.get("resources", {}) and number(value)
                    elif group == "inventory_min":
                        valid &= integer(value) and value >= 0
                    else:
                        valid &= value is None or isinstance(value, (str, bool)) or number(value)
                    if not valid:
                        errors.append(label + " has invalid " + group + " entry")
        consequences = rule.get("consequences")
        if not isinstance(consequences, dict) or set(consequences) != {"success", "failure"}:
            errors.append(label + " needs success and failure consequence alternatives")
            continue
        for outcome, alternatives in consequences.items():
            if not isinstance(alternatives, list) or not alternatives or len(alternatives) > 20:
                errors.append(label + ": " + outcome + " needs 1-20 operation alternatives")
                continue
            for operations in alternatives:
                if not isinstance(operations, list) or len(operations) > 100 or any(not isinstance(op, dict) or op.get("op") not in allowed_ops for op in operations):
                    errors.append(label + ": consequences must contain supported operation arrays")
                else:
                    for operation in operations:
                        errors.extend(label + ": " + message for message in validate_operation_recipe(operation, game, mechanics))
    return errors


def entity_map(game):
    result = {}
    world = game.get("world", {})
    if isinstance(world, dict):
        for group in ("rules", "locations", "factions", "lore", "npcs"):
            for entity in world.get(group, []) if isinstance(world.get(group, []), list) else []:
                if isinstance(entity, dict) and isinstance(entity.get("id"), str):
                    result[entity["id"]] = entity
    return result


def _validate_state(state, game):
    errors = []
    if not isinstance(state, dict):
        return ["state must be an object"]
    for key in ("branch_id", "location_id"):
        if not isinstance(state.get(key), str) or not state.get(key):
            errors.append(f"state.{key} must be a nonempty string")
    if not integer(state.get("turn")) or state["turn"] < 0:
        errors.append("state.turn must be a nonnegative integer")
    time = state.get("time", {})
    if not isinstance(time, dict) or not integer(time.get("tick")) or time["tick"] < 0:
        errors.append("state.time.tick must be a nonnegative integer")
    locations = {x.get("id") for x in game.get("world", {}).get("locations", []) if isinstance(x, dict)}
    if state.get("location_id") not in locations:
        errors.append("state.location_id does not reference a location")
    elif isinstance(time, dict) and integer(time.get("tick")):
        location = next(x for x in game["world"]["locations"] if x.get("id") == state["location_id"])
        if location.get("available_from", 0) > time["tick"]:
            errors.append("state.location_id is not yet available at state.time.tick")
    player = state.get("player")
    if not isinstance(player, dict):
        return errors + ["state.player must be an object"]
    for key in ("name", "identity"):
        if not isinstance(player.get(key), str) or not player[key].strip():
            errors.append(f"state.player.{key} is required")
    for key in ("stats", "inventory", "flags", "relations"):
        if not isinstance(player.get(key), dict):
            errors.append(f"state.player.{key} must be an object")
    resources = game.get("gameplay", {}).get("mechanics", {}).get("resources", {})
    for name, value in player.get("stats", {}).items() if isinstance(player.get("stats"), dict) else []:
        if not number(value):
            errors.append(f"stat {name} must be finite numeric")
        elif name in resources and isinstance(resources[name], dict):
            bounds = resources[name]
            if number(bounds.get("min")) and value < bounds["min"]:
                errors.append(f"stat {name} below minimum")
            if number(bounds.get("max")) and value > bounds["max"]:
                errors.append(f"stat {name} above maximum")
    for name in resources:
        if isinstance(player.get("stats"), dict) and name not in player["stats"]:
            errors.append(f"declared resource {name} missing from player.stats")
    for name, quantity in player.get("inventory", {}).items() if isinstance(player.get("inventory"), dict) else []:
        if not isinstance(name, str) or not name or not integer(quantity) or quantity < 0:
            errors.append(f"inventory {name} must have a nonnegative integer quantity")
    npc_ids = {x.get("id") for x in game.get("world", {}).get("npcs", []) if isinstance(x, dict)}
    rb = game.get("gameplay", {}).get("mechanics", {}).get("relationship_bounds", {"min": -100, "max": 100})
    for npc, dimensions in player.get("relations", {}).items() if isinstance(player.get("relations"), dict) else []:
        if npc not in npc_ids or not isinstance(dimensions, dict):
            errors.append(f"relationship {npc} must reference an NPC and hold an object")
            continue
        for dim, value in dimensions.items():
            if not number(value) or value < rb.get("min", -100) or value > rb.get("max", 100):
                errors.append(f"relationship {npc}.{dim} outside bounds")
    statuses = state.get("npc_status")
    if not isinstance(statuses, dict):
        errors.append("state.npc_status must be an object")
    else:
        for npc, status in statuses.items():
            if npc not in npc_ids or status not in NPC_STATUS:
                errors.append(f"invalid NPC status: {npc}")
    quests = {x.get("id") for x in game.get("gameplay", {}).get("quests", []) if isinstance(x, dict)}
    qstate = state.get("quests")
    if not isinstance(qstate, dict):
        errors.append("state.quests must be an object")
    else:
        for qid, status in qstate.items():
            if qid not in quests or status not in QUEST_STATUS:
                errors.append(f"invalid quest state: {qid}")
    claim_ids = {x.get("id") for x in game.get("claims", []) if isinstance(x, dict)}
    known = state.get("facts_known")
    if not isinstance(known, list) or any(x not in claim_ids for x in known):
        errors.append("state.facts_known must reference existing claims")
    elif isinstance(time, dict) and integer(time.get("tick")):
        lookup = {x["id"]: x for x in game.get("claims", [])}
        cap = min(time["tick"], game["meta"]["cutoff_tick"])
        if any(lookup[x].get("available_from", 0) > cap for x in known):
            errors.append("state.facts_known contains future facts")
    extensions = state.get("extensions", {})
    if not isinstance(extensions, dict):
        errors.append("state.extensions must be an object")
    elif "npc_knowledge" in extensions:
        mapping = extensions["npc_knowledge"]
        if not isinstance(mapping, dict):
            errors.append("state.extensions.npc_knowledge must be an object")
        else:
            for nid, facts in mapping.items():
                if nid not in npc_ids or not isinstance(facts, list) or any(x not in claim_ids for x in facts):
                    errors.append(f"invalid dynamic NPC knowledge: {nid}")
                elif isinstance(time, dict) and integer(time.get("tick")):
                    lookup = {x["id"]: x for x in game.get("claims", [])}
                    cap = min(time["tick"], game["meta"]["cutoff_tick"])
                    if any(lookup[x].get("available_from", 0) > cap for x in facts):
                        errors.append(f"future dynamic NPC knowledge: {nid}")
    if isinstance(extensions, dict) and "memories" in extensions and isinstance(time, dict) and integer(time.get("tick")):
        errors.extend(validate_memories(extensions["memories"], game, time["tick"], state.get("branch_id")))
    try:
        canonical(state)
    except (ValueError, TypeError):
        errors.append("state contains values that cannot be serialized as finite JSON")
    return errors


def _validate_game(game, strict=False):
    errors, warnings = [], []
    metrics = {"sources": 0, "claims": 0, "verified_claims": 0, "single_source_claims": 0,
               "semantic_evidence_checked_by_script": False}
    if not isinstance(game, dict):
        return {"ok": False, "errors": ["game must be an object"], "warnings": [], "metrics": metrics}
    def obj(value, label):
        if not isinstance(value, dict):
            errors.append(label + " must be an object")
            return {}
        return value
    def arr(value, label):
        if not isinstance(value, list):
            errors.append(label + " must be an array")
            return []
        return value
    def txt(value, label):
        if not isinstance(value, str) or not value.strip():
            errors.append(label + " must be nonempty text")
    def ids(values, valid, label):
        for value in arr(values, label):
            if not isinstance(value, str) or value not in valid:
                errors.append(f"{label}: dangling reference {value!r}")
    def temporal(entity, label, cap):
        tick = entity.get("available_from", 0)
        if not integer(tick) or tick < 0:
            errors.append(label + ".available_from must be a nonnegative integer")
        elif tick > cap:
            errors.append(label + " exceeds fixed research/spoiler cutoff")
    if game.get("schema_version") != "1.0":
        errors.append("unsupported schema_version (expected 1.0)")
    meta = obj(game.get("meta"), "meta")
    if not isinstance(meta.get("id"), str) or not SLUG.fullmatch(meta.get("id", "")):
        errors.append("meta.id must be a safe lowercase ASCII slug (1-63 chars)")
    for name in ("title", "work", "continuity", "cutoff", "language", "version", "created_at"):
        txt(meta.get(name), "meta." + name)
    if meta.get("medium") not in MEDIA:
        errors.append("unsupported meta.medium")
    cap = meta.get("cutoff_tick")
    if not integer(cap) or cap < 0:
        errors.append("meta.cutoff_tick must be a nonnegative integer")
        cap = 0
    if meta.get("status") not in {"draft", "ready"}:
        errors.append("meta.status must be draft or ready")
    if strict:
        txt(meta.get("research_completed_at"), "meta.research_completed_at")
    extensions = obj(game.get("extensions", {}), "extensions")
    whitelist = extensions.get("continuities", [])
    if not isinstance(whitelist, list) or any(not isinstance(x, str) for x in whitelist):
        errors.append("extensions.continuities must be a string array")
        whitelist = []
    continuities = {meta.get("continuity"), *whitelist}
    if meta.get("medium") == "mixed" and (len(whitelist) < 2 or not extensions.get("continuity_policy")):
        errors.append("mixed media needs >=2 continuities and an explicit continuity_policy")
    sources = arr(game.get("sources"), "sources")
    claims = arr(game.get("claims"), "claims")
    source_map, claim_map, global_ids = {}, {}, set()
    def identity(value, label):
        ident = value.get("id")
        if not isinstance(ident, str) or not IDENT.fullmatch(ident):
            errors.append(label + ".id is invalid")
            return None
        if ident in global_ids:
            errors.append("duplicate global id: " + ident)
        global_ids.add(ident)
        return ident
    for index, raw in enumerate(sources):
        source = obj(raw, f"sources[{index}]")
        ident = identity(source, f"sources[{index}]")
        if ident:
            source_map[ident] = source
        for name in ("title", "uri", "retrieved_at"):
            txt(source.get(name), f"source {ident}.{name}")
        if source.get("kind") not in KINDS:
            errors.append(f"source {ident}: unsupported kind")
        if source.get("status") not in SOURCE_STATUS:
            errors.append(f"source {ident}: unsupported status")
        if source.get("status") != "ok":
            warnings.append(f"source {ident} is {source.get('status')}, not usable as verified evidence")
        if source.get("continuity") and source["continuity"] not in continuities:
            warnings.append(f"source {ident} belongs to another continuity; do not cite it as this version")
    for index, raw in enumerate(claims):
        claim = obj(raw, f"claims[{index}]")
        ident = identity(claim, f"claims[{index}]")
        if ident:
            claim_map[ident] = claim
        txt(claim.get("text"), f"claim {ident}.text")
        classification, verification = claim.get("classification"), claim.get("verification")
        if classification not in CLASSIFICATIONS:
            errors.append(f"claim {ident}: unsupported classification")
        if verification not in VERIFICATIONS:
            errors.append(f"claim {ident}: unsupported verification")
        if type(claim.get("core")) is not bool:
            errors.append(f"claim {ident}.core must be boolean")
        temporal(claim, f"claim {ident}", cap)
        if classification != "design" and claim.get("continuity") not in continuities:
            errors.append(f"claim {ident}: continuity mismatch")
        usable, groups = [], set()
        for ref in arr(claim.get("source_refs"), f"claim {ident}.source_refs"):
            ref = obj(ref, f"claim {ident} source reference")
            source = source_map.get(ref.get("source_id"))
            txt(ref.get("locator"), f"claim {ident} source locator")
            if source is None:
                errors.append(f"claim {ident}: missing source {ref.get('source_id')}")
            elif source.get("status") == "ok":
                if source.get("continuity") and source["continuity"] != claim.get("continuity"):
                    errors.append(f"claim {ident}: cited source continuity mismatch")
                usable.append(source)
                group = source.get("independence_group")
                if isinstance(group, str) and group.strip():
                    groups.add(group)
        if verification == "verified":
            metrics["verified_claims"] += 1
            if classification == "design" or len(groups) < 2:
                errors.append(f"claim {ident}: verified needs two explicitly independent usable source groups")
            if len({s.get("uri") for s in usable}) < 2:
                errors.append(f"claim {ident}: repeated source URI is not independent evidence")
            warnings.append(f"claim {ident}: script checks citation structure; AI must verify semantic support and source independence")
        elif verification == "single_source":
            metrics["single_source_claims"] += 1
            if not usable:
                errors.append(f"claim {ident}: single_source needs usable evidence")
            warnings.append(f"claim {ident} is single_source, not cross-verified")
        elif classification == "design":
            if verification != "not_applicable":
                errors.append(f"claim {ident}: design uses not_applicable verification")
        elif verification == "not_applicable":
            errors.append(f"claim {ident}: only design may use not_applicable")
        if verification in {"unsupported", "disputed"}:
            message = f"claim {ident}: {verification}"
            (errors if strict and claim.get("core") else warnings).append(message)
        conflicts = arr(claim.get("conflicts", []), f"claim {ident}.conflicts")
        if conflicts and verification == "verified":
            errors.append(f"claim {ident}: unresolved conflicts cannot be verified")
    metrics["sources"], metrics["claims"] = len(sources), len(claims)
    if strict and meta.get("medium") != "original":
        if not any(s.get("status") == "ok" for s in source_map.values()):
            errors.append("adapted work requires usable research sources")
        if not any(c.get("classification") == "canon" for c in claim_map.values()):
            errors.append("adapted work requires explicit canon claims")
    world = obj(game.get("world"), "world")
    txt(world.get("premise"), "world.premise")
    npc_list = []
    for group in ("rules", "locations", "factions", "lore", "npcs"):
        entities = arr(world.get(group), "world." + group)
        if group in {"rules", "locations"} and not entities:
            errors.append("world." + group + " must not be empty")
        for raw in entities:
            entity = obj(raw, "world." + group + " entity")
            ident = identity(entity, "world." + group)
            ids(entity.get("claim_ids"), claim_map, f"entity {ident}.claim_ids")
            temporal(entity, f"entity {ident}", cap)
            for cid in entity.get("claim_ids", []) if isinstance(entity.get("claim_ids"), list) else []:
                claim = claim_map.get(cid)
                if claim and integer(claim.get("available_from", 0)) and integer(entity.get("available_from", 0)):
                    if claim.get("available_from", 0) > entity.get("available_from", 0):
                        errors.append(f"entity {ident}: future supporting claim {cid}; split or retime the entry")
            if group == "rules":
                txt(entity.get("text"), f"rule {ident}.text")
            else:
                txt(entity.get("name"), f"entity {ident}.name")
                txt(entity.get("content") if group == "lore" else entity.get("description"), f"entity {ident} content")
            if group in {"locations", "factions", "lore"}:
                keys = arr(entity.get("keys"), f"entity {ident}.keys")
                if any(not isinstance(k, str) or not k.strip() for k in keys):
                    errors.append(f"entity {ident}: keys must be nonempty strings")
                if not keys:
                    warnings.append(f"entity {ident}: no keyword triggers; exporter must pin or disable explicitly")
                if entity.get("visibility") not in {"public", "gm"}:
                    errors.append(f"entity {ident}: visibility must be public or gm")
            if group == "npcs":
                npc_list.append(entity)
                if ident in {"gm", "player"}:
                    errors.append(f"NPC {ident}: gm and player are reserved context actor IDs")
                for field in ("goals", "behavior"):
                    vals = arr(entity.get(field), f"NPC {ident}.{field}")
                    if not vals or any(not isinstance(x, str) or not x.strip() for x in vals):
                        errors.append(f"NPC {ident}.{field} must contain behavior/goal text")
                txt(entity.get("speech"), f"NPC {ident}.speech")
                ids(entity.get("knowledge"), claim_map, f"NPC {ident}.knowledge")
                if isinstance(entity.get("claim_ids"), list) and isinstance(entity.get("knowledge"), list):
                    unknown_profile = set(entity["claim_ids"]) - set(entity["knowledge"])
                    if unknown_profile:
                        errors.append(f"NPC {ident}: own dossier cites unknown facts {sorted(unknown_profile)}; move hidden backstory to GM lore")
                initial_tick = game.get("state", {}).get("time", {}).get("tick", 0)
                if integer(initial_tick) and integer(entity.get("available_from", 0)):
                    npc_start = max(initial_tick, entity.get("available_from", 0))
                    for cid in entity.get("knowledge", []) if isinstance(entity.get("knowledge"), list) else []:
                        if cid in claim_map and claim_map[cid].get("available_from", 0) > npc_start:
                            errors.append(f"NPC {ident}: baseline knowledge includes future claim {cid}")
                if entity.get("status") not in NPC_STATUS:
                    errors.append(f"NPC {ident}: invalid status")
    npc_ids = {x.get("id") for x in npc_list}
    for lore in world.get("lore", []) if isinstance(world.get("lore"), list) else []:
        if isinstance(lore, dict):
            ids(lore.get("known_by", []), npc_ids, f"lore {lore.get('id')}.known_by")
    gameplay = obj(game.get("gameplay"), "gameplay")
    for field in ("gm_rules", "loops", "endings"):
        vals = arr(gameplay.get(field), "gameplay." + field)
        if not vals or any(not isinstance(x, str) or not x.strip() for x in vals):
            errors.append("gameplay." + field + " must contain nonempty text")
    txt(gameplay.get("failure_policy"), "gameplay.failure_policy")
    opening = obj(gameplay.get("opening"), "gameplay.opening")
    txt(opening.get("scene"), "gameplay.opening.scene")
    choices = arr(opening.get("choices"), "gameplay.opening.choices")
    if len(choices) < 2 or any(not isinstance(x, str) or not x.strip() for x in choices):
        errors.append("opening must offer >=2 meaningful options (free action remains available)")
    mechanics = obj(gameplay.get("mechanics"), "gameplay.mechanics")
    txt(mechanics.get("dice"), "mechanics.dice")
    resources = obj(mechanics.get("resources"), "mechanics.resources")
    for name, raw in resources.items():
        bounds = obj(raw, "resource " + name)
        if not number(bounds.get("min")) or not number(bounds.get("max")) or bounds.get("min", 0) > bounds.get("max", 0):
            errors.append(f"resource {name}: finite min <= max required")
    if "relationship_bounds" in mechanics:
        rb = obj(mechanics["relationship_bounds"], "mechanics.relationship_bounds")
        if not number(rb.get("min")) or not number(rb.get("max")) or rb.get("min", 0) > rb.get("max", 0):
            errors.append("invalid relationship_bounds")
    errors.extend(validate_check_rules(mechanics, game))
    for quest in arr(gameplay.get("quests"), "gameplay.quests"):
        quest = obj(quest, "quest")
        ident = identity(quest, "quest")
        txt(quest.get("title"), f"quest {ident}.title")
        txt(quest.get("objective"), f"quest {ident}.objective")
        if quest.get("status") not in QUEST_STATUS:
            errors.append(f"quest {ident}: invalid status")
    # Avoid cascading type exceptions for malformed drafts: runtime validation assumes containers.
    if not errors:
        errors.extend(validate_state(game.get("state"), game))
        if not errors and game["state"]["time"]["tick"] > cap:
            errors.append("initial state exceeds research/spoiler cutoff")
    try:
        canonical(game)
    except (ValueError, TypeError):
        errors.append("game contains non-finite or non-JSON values")
    return {"ok": not errors, "errors": errors, "warnings": list(dict.fromkeys(warnings)), "metrics": metrics}


def validate_state(state, game):
    try:
        return _validate_state(state, game)
    except (TypeError, AttributeError, KeyError, ValueError) as exc:
        return ["malformed state container/value: " + str(exc)]


def validate_game(game, strict=False):
    try:
        return _validate_game(game, strict)
    except (TypeError, AttributeError, KeyError, ValueError) as exc:
        return {"ok": False, "errors": ["malformed game container/value: " + str(exc)],
                "warnings": [], "metrics": {"semantic_evidence_checked_by_script": False}}
