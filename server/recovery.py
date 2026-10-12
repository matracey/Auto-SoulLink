"""Missing-mon evidence and resolution history, independent of cartridge writes."""

from __future__ import annotations

import logging
from collections import Counter
from copy import deepcopy
from datetime import UTC, datetime
from time import monotonic
from uuid import uuid4

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
