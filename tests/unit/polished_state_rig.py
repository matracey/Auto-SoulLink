"""Test helper (a plain module, not collected): the REAL server SoulLinkState driven by the events the composed
Polished client emits.

The Polished client tests (test_polished_box_contract.py, test_polished_withdraw_path.py ...) assert what the client
SENDS. What a command does to the SERVER (party_keys, party_size, queued_commands for BOTH players, sync_inflight,
rebuild_pending) was only ever argued from the code. PolishedStateBridge closes that gap: it walks the Rig's sent
events with a monotonic cursor (never re-feeding history), hands each new one to `SoulLinkState.handle_event` as a deep
copy, and keeps a deep-copied SNAPSHOT of both players' server-side model immediately after EACH event. Per-event
snapshots matter: a later tick can repair the model, so only the intermediate states show that a bad NACK re-boxed
the partner or dropped a rebuild key.

The only thing seeded rather than observed is the Soul Link pair itself (`seed_pair`): the link, its index and the
server's party model for the partner are written directly. Also seeded by the tests that use them: a minimal rebuild record (`seed_rebuild`, an unlinked phantom
key in part 1 keeps the rebuild open) and, where stated, death/count bookkeeping. That is a SEEDED fixture, not an admission and not a
natural capture.

Delivery is never faked: a server command reaches the client only through the state's own tick reply
(`deliver`), which arms sync_inflight exactly as server.py does, and the returned dict goes UNCHANGED to
`rig.send_command`.
"""
from __future__ import annotations

import copy
import json

import pytest

from server.adapters.gen2_polished import Gen2PolishedAdapter
from server.state import AreaStatus, LinkEntry, LinkStatus, MonInfo, SoulLinkState
from tests.unit.test_polished_boxes import Image
from tests.unit.test_polished_client import _entry, _pair
from tests.unit.test_polished_lua import ROOT
from tests.unit.test_polished_write_path import HARNESS, Rig, overlay, seal_save, sysbus

lupa = pytest.importorskip("lupa")

# dofile is wrapped so the SAME composition loads a mutated lua/gen2 module (the explode/rival-path route)
OVERRIDE = """
local real_dofile = dofile
function dofile(path)
    local source = SLINK_OVERRIDES[path:match("lua/gen2/[%w_]+%.lua$") or ""]
    if source then return assert(load(source, "=mutant"))() end
    return real_dofile(path)
end
"""

PLAYERS = ("a", "b")


def assert_sync_command(state, player, command, name, key):
    version = state.sync_versions[player][key]
    assert version["cmd"] == name
    assert version["revision"] > 0
    assert command == {
        "cmd": name,
        "key": key,
        "intent_id": f"{state.report_run_id}:{version['revision']}",
        "intent_revision": version["revision"],
    }


def new_state(tmp_path):
    """The real server state, isolated (links.json / memorial.json live under tmp_path), on the real Polished adapter."""
    return SoulLinkState(data_dir=str(tmp_path), adapter=Gen2PolishedAdapter(artifact_kind="overlay"))


class MutantRig(Rig):
    """Rig with any lua/gen2 module replaceable by a mutated source at load time (the explode/rival-path route:
    `dofile` is wrapped so the SAME composition loads the mutated text; nothing else changes)."""

    def __init__(self, mons, overrides):
        self.lua = lupa.LuaRuntime(unpack_returned_tuples=True)
        self.mem = self.lua.table_from(sysbus(mons))
        self.img = seal_save(Image())
        deps, self.io, self.log = self.lua.execute(HARNESS.replace("ROOTDIR", json.dumps(ROOT)))(
            overlay()[1], self.mem, self.img)
        self.lua.globals().SLINK_OVERRIDES = self.lua.table_from(overrides)
        self.lua.execute(OVERRIDE)
        self.parts, why = _pair(_entry(self.lua).build(deps))
        assert why is None, why
        self.client = self.parts.client
        self.lua.globals().SLINK_PARTS = self.parts
        self.client.start(self.client)


class PolishedStateBridge:
    """Feeds `rig.sent(None)` (from a cursor) into `state.handle_event(player, ...)` and snapshots the server model."""

    def __init__(self, rig, state, player="a"):
        self.rig, self.state, self.player = rig, state, player
        self.cursor = 0
        self.history: list[dict] = []

    # ── history ──────────────────────────────────────────────────────────────────────────────────────────
    def mark(self):
        """The current history length: `commands_since(mark, ...)` and `snapshots_since(mark)` look at later entries."""
        return len(self.history)

    def record(self, player, msg, returned):
        """Take the post-event snapshot of BOTH players' server-side model (deep copies)."""
        st = self.state
        self.history.append({
            "i": len(self.history), "player": player, "event": msg.get("event"), "key": msg.get("key"),
            "reason": msg.get("reason"), "returned": copy.deepcopy(returned),
            "queued": {p: copy.deepcopy(st.queued_commands[p]) for p in PLAYERS},
            "party_keys": {p: sorted(st.party_keys[p]) for p in PLAYERS},
            "party_size": {p: st.party_size.get(p, 0) for p in PLAYERS},
            "sync_inflight": {p: sorted((k, c, n) for (k, c), n in st.sync_inflight[p].items()) for p in PLAYERS},
            "rebuild": {p: copy.deepcopy(st.rebuild_pending.get(p)) for p in PLAYERS},
        })
        return self.history[-1]

    def feed(self, player, msg):
        """A state-only event (no emulator behind it, e.g. the partner's native box_to_party), recorded."""
        returned = self.state.handle_event(player, copy.deepcopy(msg))
        self.record(player, msg, returned)
        return returned

    def drain(self, player=None):
        """Feed every client event the rig emitted since the last drain (cursor: history is never re-fed)."""
        player = player or self.player
        sent = self.rig.sent(None)
        fresh, self.cursor = sent[self.cursor:], len(sent)
        for msg in fresh:
            self.feed(player, msg)
        return fresh

    def skip_history(self):
        """Advance the cursor over everything already emitted WITHOUT feeding it (the server model is then seeded)."""
        self.cursor = len(self.rig.sent(None))

    def deliver(self, player="a"):
        """One tick from `player`: the server's reply carries whatever is queued for them (arming sync_inflight)."""
        return self.feed(player, {"event": "tick"})

    # ── questions ────────────────────────────────────────────────────────────────────────────────────────
    def snapshots_since(self, mark):
        return self.history[mark:]

    def commands_since(self, mark, player, cmd, key=None):
        """[(snapshot index, "queued"|"returned")]: every moment after `mark` at which a `cmd` (for `key`, when given)
        was queued for `player` or handed back to them."""
        def hit(c):
            return c.get("cmd") == cmd and (key is None or c.get("key") == key)

        out = []
        for snap in self.history[mark:]:
            if any(hit(c) for c in snap["queued"][player]):
                out.append((snap["i"], "queued"))
            if snap["player"] == player and any(hit(c) for c in snap["returned"]):
                out.append((snap["i"], "returned"))
        return out

    def trace(self, mark=0):
        """A readable per-event line list (for assertion messages and the report)."""
        lines = []
        for s in self.history[mark:]:
            q = {p: [f"{c['cmd']}:{c.get('key', '')[:6]}" for c in s["queued"][p] if c["cmd"] != "hud_show"] for p in PLAYERS}
            r = [f"{c['cmd']}:{c.get('key', '')[:6]}" for c in s["returned"] if c["cmd"] not in ("noop", "hud_show")]
            rb = {p: (None if s["rebuild"][p] is None else
                      {"queued": [k[:6] for k in s["rebuild"][p]["queued_keys"]],
                       "restored": sorted(k[:6] for k in s["rebuild"][p]["restored_keys"])}) for p in PLAYERS}
            lines.append(f"#{s['i']} {s['player']}:{s['event']}:{(s['key'] or '')[:6]} ret={r} queued={q} "
                         f"pk={ {p: [k[:6] for k in s['party_keys'][p]] for p in PLAYERS} } size={s['party_size']} "
                         f"inflight={ {p: [(k[:6], c) for k, c, _ in s['sync_inflight'][p]] for p in PLAYERS} } rebuild={rb}")
        return "\n".join(lines)


def seed_pair(state, a_key, b_key, area="route_29", status=LinkStatus.ALIVE, a_party=False, b_party=True,
              a_level=12, b_level=14):
    """SEEDED (not an admission, not a natural capture): one linked pair written straight into the server model,
    A's half boxed (or in party), B's half in party. The area id is one the Polished adapter's pack accepts."""
    entry = LinkEntry(area_id=area, a=MonInfo(key=a_key, level=a_level), b=MonInfo(key=b_key, level=b_level), status=status)
    state.links.append(entry)
    state._index_entry(entry)
    state.area_states[area] = AreaStatus.LINKED
    state.pokeballs_obtained = {"a": True, "b": True}
    (state.party_keys["a"].add if a_party else state.party_keys["a"].discard)(a_key)
    (state.party_keys["b"].add if b_party else state.party_keys["b"].discard)(b_key)
    return entry


def seed_rebuild(state, player, queued_keys):
    """SEEDED: a MINIMAL ACK-bookkeeping rebuild record (queued_keys/restored_keys only). It is NOT the production
    shape: _handle_whiteout (server/state.py ~3275-3279) also writes started_at and queued_partner_keys, and plans only
    fully boxed pairs. Use it for ACK/NACK bookkeeping tests, never as a whiteout-fidelity claim."""
    state.rebuild_pending[player] = {"queued_keys": list(queued_keys), "restored_keys": set()}
