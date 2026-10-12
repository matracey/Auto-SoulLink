"""Missing-mon evidence and resolution history, independent of cartridge writes."""

from __future__ import annotations

import logging
from collections import Counter
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import UTC, datetime
from time import monotonic
from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from server.state import SoulLinkState

PLAYERS = ("a", "b")
MUTATIONS = frozenset({"box_mon", "party_mon", "memorialize", "force_faint", "force_explode"})
log = logging.getLogger(__name__)


class RecoveryConflict(ValueError):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat()


class Recovery:
    def __init__(self):
        self.limbo: dict[str, dict[str, dict]] = {pid: {} for pid in PLAYERS}
        self.history: list[dict] = []
        self.candidates: dict[str, list[dict]] = {pid: [] for pid in PLAYERS}
        self._generation: dict[str, int] = {}
        self._suspected: dict[str, set[str]] = {pid: set() for pid in PLAYERS}
        self._present: dict[str, dict[str, dict]] = {pid: {} for pid in PLAYERS}
        self._observed_at: dict[str, float] = {}
        self.control_revision = 0
        self.cancellations: dict[str, dict[str, int]] = {pid: {} for pid in PLAYERS}

    @classmethod
    def load(cls, saved: dict | None) -> Recovery:
        result = cls()
        if saved is None:
            return result
        if not isinstance(saved, dict) or saved.get("version") != 1:
            raise ValueError("invalid recovery state version")
        limbo, history = saved.get("limbo"), saved.get("history")
        if not isinstance(limbo, dict) or set(limbo) != set(PLAYERS) or not isinstance(history, list):
            raise ValueError("invalid recovery state")
        for pid in PLAYERS:
            if not isinstance(limbo[pid], dict):
                raise ValueError("invalid limbo census")
            for key, incident in limbo[pid].items():
                if (not isinstance(key, str) or not key or not isinstance(incident, dict)
                        or incident.get("key") != key or incident.get("player") != pid
                        or not isinstance(incident.get("revision"), str) or not incident["revision"]
                        or not isinstance(incident.get("area_id"), str)):
                    raise ValueError("invalid limbo incident")
        if any(not isinstance(row, dict) or row.get("action") not in {
                "reappeared", "rebind", "retire", "detach"} for row in history):
            raise ValueError("invalid recovery history")
        result.limbo, result.history = deepcopy(limbo), deepcopy(history)
        result.control_revision = saved.get("control_revision", 0)
        result.cancellations = deepcopy(saved.get("cancellations", {pid: {} for pid in PLAYERS}))
        if (type(result.control_revision) is not int or result.control_revision < 0
                or not isinstance(result.cancellations, dict)
                or set(result.cancellations) != set(PLAYERS)):
            raise ValueError("invalid recovery cancellations")
        for rows in result.cancellations.values():
            if not isinstance(rows, dict) or any(
                    not isinstance(key, str) or not key or type(revision) is not int
                    or not 0 < revision <= result.control_revision for key, revision in rows.items()):
                raise ValueError("invalid recovery cancellation revision")
        return result

    def dump(self) -> dict:
        return deepcopy({"version": 1, "limbo": self.limbo, "history": self.history,
                         "control_revision": self.control_revision, "cancellations": self.cancellations})

    def disconnect(self, player: str) -> None:
        self._generation.pop(player, None)
        self.invalidate(player)

    def invalidate(self, player: str) -> None:
        """Unqualified evidence cannot extend a candidate's freshness or an absence streak."""
        self._suspected[player].clear()
        self._present[player].clear()
        self.candidates[player] = []
        self._observed_at.pop(player, None)

    def observe(self, player: str, generation: int, records: list[dict],
                expected: dict[str, dict], claimed: set[str] | None = None) -> list[dict]:
        """Accept only a caller-qualified, complete party-and-PC census.

        Two independently refreshed inventories establish absence; repeating a
        cached generation cannot confirm a missing mon.
        """
        if player not in PLAYERS or type(generation) is not int or generation < 1:
            raise ValueError("invalid recovery census identity")
        if not isinstance(records, list) or any(
                not isinstance(row, dict) or not isinstance(row.get("key"), str)
                or not row["key"] for row in records):
            raise ValueError("incomplete recovery census")
        if generation <= self._generation.get(player, 0):
            return []
        self._generation[player] = generation
        self._observed_at[player] = monotonic()
        counts = Counter(row["key"] for row in records)
        present = {row["key"]: deepcopy(row) for row in records if counts[row["key"]] == 1}
        self._present[player] = present
        claimed = set(expected) if claimed is None else claimed | expected.keys()
        self.candidates[player] = [row for key, row in present.items()
                                   if key not in claimed and key not in self.limbo[player]]
        changes = []
        for key in list(self.limbo[player]):
            if key in present:
                changes.append(self.finish(player, key, "reappeared"))
        absent = set(expected) - counts.keys()
        for key in sorted(absent & self._suspected[player]):
            if key in self.limbo[player]:
                continue
            incident = {
                **deepcopy(expected[key]), "key": key, "player": player,
                "revision": uuid4().hex, "since": _now(),
            }
            self.limbo[player][key] = incident
            changes.append({**incident, "action": "missing"})
        self._suspected[player] = absent
        return changes

    def incident(self, player: str, key: str, revision: str) -> dict:
        incident = self.limbo.get(player, {}).get(key)
        if incident is None or incident["revision"] != revision:
            raise RecoveryConflict("This incident changed. Refresh the board before resolving it.")
        return deepcopy(incident)

    def candidate(self, player: str, key: str) -> dict:
        if player not in PLAYERS:
            raise RecoveryConflict("Unknown player.")
        if monotonic() - self._observed_at.get(player, float("-inf")) > 30:
            raise RecoveryConflict("Wait for a fresh, complete census before matching this Pokémon.")
        candidate = next((row for row in self.candidates[player] if row["key"] == key), None)
        if candidate is None:
            raise RecoveryConflict("The selected Pokémon is not in a fresh, unambiguous census.")
        return deepcopy(candidate)

    def available(self, player: str) -> list[dict]:
        if monotonic() - self._observed_at.get(player, float("-inf")) > 30:
            return []
        return deepcopy(self.candidates[player])

    def finish(self, player: str, key: str, action: str, target_key: str = "") -> dict:
        if action not in {"reappeared", "rebind", "retire", "detach"}:
            raise ValueError("invalid recovery action")
        incident = self.limbo[player].pop(key)
        self._suspected[player].discard(key)
        record = {**incident, "action": action, "resolved_at": _now()}
        if target_key:
            record["target_key"] = target_key
        self.history.append(record)
        return deepcopy(record)

    @staticmethod
    def expected(state: SoulLinkState, player: str) -> dict[str, dict]:
        expected = {}
        for entry in state.links:
            mon = getattr(entry, player)
            if mon and entry.status.value == "alive":
                expected[mon.key] = {**asdict(mon), "area_id": entry.area_id}
        for collection in (state.pending_captures, state.unpaired_mons):
            for area, players in collection.items():
                if mon := players.get(player):
                    expected[mon.key] = {**asdict(mon), "area_id": area}
        for key in state.bonus_keys[player]:
            if key not in expected:
                stats = state.mon_stats[player].get(key, {})
                expected[key] = {
                    "key": key, "area_id": "_bonus_pending_" + key,
                    "nickname": stats.get("nickname", ""), "species": stats.get("species_id", 0),
                    "level": stats.get("level", 0), "is_shiny": True,
                }
        return expected

    @classmethod
    def claimed(cls, state: SoulLinkState, player: str) -> set[str]:
        keys = set(cls.expected(state, player))
        for entry in state.links:
            if mon := getattr(entry, player):
                keys.add(mon.key)
        keys.update(state.pending_memorials[player])
        keys.update(state.ambiguous_keys[player])
        return keys

    def paused(self, state: SoulLinkState) -> dict[str, set[str]]:
        paused = {pid: set(self.limbo[pid]) | self._suspected[pid] for pid in PLAYERS}
        for entry in state.links:
            halves = [(pid, getattr(entry, pid)) for pid in PLAYERS]
            if any(mon and mon.key in paused[pid] for pid, mon in halves):
                for pid, mon in halves:
                    if mon:
                        paused[pid].add(mon.key)
        return paused

    def control(self, state: SoulLinkState, player: str) -> dict:
        return {"cmd": "recovery_control", "paused": sorted(self.paused(state)[player]),
                "cancelled": dict(self.cancellations[player])}

    def observe_message(self, state: SoulLinkState, player: str, msg: dict) -> None:
        """Called after hello admission, before snapshot reconciliation."""
        if (msg.get("party_hidden") is True or state.party_snapshot_withheld(player, msg) or msg.get("in_battle")
                or state.pending_trade or state._trade_settle_ticks.get(player, 0)
                or any(state.report_backlog.values())):
            self.invalidate(player)
            return
        if "inventory_complete" not in msg and "inventory_generation" not in msg:
            return
        generation = msg.get("inventory_generation")
        records = [*msg.get("party", []), *msg.get("pc_boxes", [])] if (
            isinstance(msg.get("party"), list) and isinstance(msg.get("pc_boxes"), list)) else None
        if (msg.get("inventory_complete") is not True or type(generation) is not int or generation < 1
                or records is None or any(not isinstance(row, dict) or not isinstance(row.get("key"), str)
                                          or not row["key"] for row in records)):
            self.invalidate(player)
            return
        changes = self.observe(player, generation, records, self.expected(state, player),
                               self.claimed(state, player))
        if changes:
            for row in changes:
                log.info("[%s] recovery %s: %s", player, row["action"], row["key"])
            state._save()

    def _cancel(self, state: SoulLinkState, owners: dict[str, set[str]]) -> None:
        self.control_revision += 1
        for player, keys in owners.items():
            for key in keys:
                self.cancellations[player][key] = self.control_revision
                state.cancel_sync(player, key, reason="recovery resolution")
                state.death_inflight[player].pop(key, None)
            state.queued_commands[player] = [
                row for row in state.queued_commands[player]
                if row.get("key") not in keys or row.get("cmd") not in MUTATIONS]

    def resolve(self, state: SoulLinkState, player: str, key: str, revision: str,
                action: str, target: str = "") -> dict:
        """Commit an administrator's decision and its command cancellations together."""
        from server.state import AreaStatus, LinkEntry, LinkStatus, MonInfo

        incident = self.incident(player, key, revision)
        if action not in {"rebind", "retire", "detach"}:
            raise RecoveryConflict("Unknown recovery action.")
        if state.load_failed or state.pending_trade or any(state.report_backlog.values()):
            raise RecoveryConflict("Wait for run recovery and queued gameplay reports to finish.")
        entry = state.entry_for(player, key)
        if entry and entry.status != LinkStatus.ALIVE and action != "retire":
            raise RecoveryConflict("A confirmed death can only be retired.")
        area = incident["area_id"]
        owners = {pid: set() for pid in PLAYERS}
        owners[player].add(key)
        if entry:
            for pid in PLAYERS:
                if mon := getattr(entry, pid):
                    owners[pid].add(mon.key)
            partner = "b" if player == "a" else "a"
            survivor = getattr(entry, partner)
            previous = state.unpaired_mons.get(area, {}).get(partner)
            if action == "detach" and previous and survivor and previous.key != survivor.key:
                raise RecoveryConflict("An unpaired encounter already occupies the surviving side.")
        candidate = None
        if action == "rebind":
            candidate = self.candidate(player, target)
            if target in self.claimed(state, player):
                raise RecoveryConflict("The selected Pokémon already belongs to another encounter.")
            if state._key_refs(target, player) - {"party_keys", "partner_blobs"}:
                raise RecoveryConflict("The selected Pokémon is involved in another operation.")
            if entry and entry.a and entry.b:
                proposed = replace(entry, **{player: MonInfo(
                    key=target, species=candidate.get("species_id", candidate.get("species", 0)),
                    nickname=candidate.get("nickname", ""), level=candidate.get("level", 0))})
                if violation := state._check_link_violation(proposed.a, proposed.b, existing_entry=entry):
                    raise RecoveryConflict(violation[0])
        before = deepcopy({k: v for k, v in state.__dict__.items()
                           if k != "adapter" and not callable(v)})
        try:
            state._report_transaction = True
            self._cancel(state, owners)
            if action == "rebind":
                stats = deepcopy(state.mon_stats[player].get(target, {}))
                state._migrate_key(player, {
                    "old_key": key, "new_key": target, "reason": "recovery",
                    "new_species": candidate.get("species_id", candidate.get("species", 0)),
                    "new_nickname": candidate.get("nickname", ""),
                }, entry)
                state.mon_stats[player][target] = stats
                owners[player].add(target)
                self.candidates[player] = [row for row in self.candidates[player] if row["key"] != target]
            else:
                for collection in (state.pending_captures, state.unpaired_mons):
                    players = collection.get(area, {})
                    if players.get(player) and players[player].key == key:
                        players.pop(player)
                    if not players:
                        collection.pop(area, None)
                state.bonus_keys[player].discard(key)
                partner = "b" if player == "a" else "a"
                state.pending_bonus[partner] = type(state.pending_bonus[partner])(
                    value for value in state.pending_bonus[partner] if value != key)
                if action == "detach":
                    if entry:
                        if survivor := getattr(entry, partner):
                            state.unpaired_mons.setdefault(area, {})[partner] = replace(survivor)
                        for pid in PLAYERS:
                            if mon := getattr(entry, pid):
                                state._unindex(pid, mon.key, entry)
                        state.links.remove(entry)
                else:
                    if entry is None:
                        mon = MonInfo(key=key, species=incident.get("species", 0),
                                      nickname=incident.get("nickname", ""), level=incident.get("level", 0))
                        entry = LinkEntry(area, mon if player == "a" else None,
                                          mon if player == "b" else None, LinkStatus.ALIVE)
                        state.links.append(entry)
                        state._index_entry(entry)
                    if entry.status == LinkStatus.ALIVE:
                        state._propagate_faint(player, entry, cause="recovery_retirement")
                    elif survivor := getattr(entry, partner):
                        state.queued_commands[partner].append({"cmd": "force_faint", "key": survivor.key})
                        if survivor.key in state.pending_memorials[partner]:
                            state._queue_memorialize(partner, survivor.key)
                    state.pending_memorials[player].discard(key)
                    state.queued_commands[player] = [
                        row for row in state.queued_commands[player]
                        if row.get("key") != key or row.get("cmd") not in MUTATIONS]
                state.party_keys[player].discard(key)
                state.encounter_locks.pop(area, None)
                state._set_area_state(area, AreaStatus.DEAD_ZONE, player=player, reason="recovery_" + action)
                for pid in PLAYERS:
                    state._queue_resolved_areas(pid)
            record = self.finish(player, key, action, target if action == "rebind" else "")
            for pid, keys in owners.items():
                for owned in keys:
                    state.resume_sync(pid, owned)
            state._report_transaction = False
            state._save()
            if state.save_failed:
                raise OSError(state.save_failed)
            log.info("[%s] recovery %s: %s", player, action, key)
            return record
        except Exception:
            state.__dict__.update(before)
            log.exception("[%s] recovery resolution not committed: %s", player, key)
            raise
