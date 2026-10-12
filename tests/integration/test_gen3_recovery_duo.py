"""Two production Lua clients over modeled RAM and real loopback TCP/HTTP."""
import json

import pytest
from aiohttp.test_utils import TestClient, TestServer

from server.server import SLinkServer, build_app
from server.state import LinkStatus
from tests.unit.gen3_world import World, key_of, mon_record
from tests.unit.test_gen3_report_outbox import Disk
from tests.unit.test_server_run_reconnect import _send, _tcp

OTS = {"a": 0xABCD, "b": 0xBCDE}
PIDS = {"a": 0x11111111, "b": 0x22222222}
KEYS = {p: key_of(PIDS[p] + 1, OTS[p]) for p in OTS}


class Duo:
    def __init__(self):
        self.worlds, self.disks, self.cursors, self.sockets = {}, {}, {}, {}
        self.drop_receipts = set()
        for player in OTS:
            disk = Disk()

            def outbox(lua, disk=disk, player=player):
                journal = disk.factory(lua)
                journal.player = player
                return journal

            world = World("gen3_rr", "radical_red", player=player, outbox=outbox)
            world.auto_recovery_control = False
            world.set_trainer(OTS[player], player.upper())
            world.set_party([self.starter(player)])
            world.set_balls(10)
            world.write_companion_mailbox()
            rom = world.profile["rom"]
            for move, pp in ((0, 35), (33, 35), (45, 40)):
                address = rom["BATTLE_MOVES_ADDR"] + move * world.d["BATTLE_MOVE_ENTRY_SIZE"]
                world.rom[address + world.d["BATTLE_MOVE_PP_OFFSET"] - 0x08000000] = pp
            for slot, mask in enumerate((3, 12, 48, 192)):
                world.rom[rom["PP_UP_GET_MASK_ADDR"] + slot - 0x08000000] = mask
            self.worlds[player], self.disks[player], self.cursors[player] = world, disk, 0

    @staticmethod
    def starter(player):
        return mon_record(PIDS[player], OTS[player], species=4 if player == "a" else 7)

    @staticmethod
    def catch(player):
        return mon_record(PIDS[player] + 1, OTS[player], species=25 if player == "a" else 74)

    async def connect(self, connect):
        for player, world in self.worlds.items():
            self.sockets[player] = await connect()
            world.connected = True

    async def step(self, count=1):
        for _ in range(count):
            for player, world in self.worlds.items():
                world.step()
                for message in world.sent[self.cursors[player]:]:
                    assert world.connected
                    reply = await _send(self.sockets[player], message)
                    if player in self.drop_receipts:
                        reply["commands"] = [row for row in reply["commands"] if row["cmd"] != "report_ack"]
                    world.replies.append(json.dumps(reply))
                self.cursors[player] = len(world.sent)

    def capture(self, player):
        world = self.worlds[player]
        caught = self.catch(player)
        world.enter_battle([caught])
        world.set_party([self.starter(player), caught])
        world.fire("capture_wild")

    def leave_battles(self):
        for world in self.worlds.values():
            world.leave_battle(outcome=7)
            world.overworld_safe()

    def offline(self):
        for world in self.worlds.values():
            world.connected = False
            world.step()


@pytest.mark.asyncio
async def test_offline_catch_and_lost_receipt_replay_once_after_server_restart(tmp_path, monkeypatch):
    monkeypatch.delenv("SLINK_ADMIN_KEY", raising=False)
    duo = Duo()
    srv = SLinkServer(data_dir=str(tmp_path), retry_same_species=False)
    async with _tcp(srv) as (connect, _):
        await duo.connect(connect)
        await duo.step(65)
        assert all(srv.is_admitted(player) for player in OTS)
        duo.worlds["a"].connected = False
        duo.sockets["a"][1].close()
        await duo.sockets["a"][1].wait_closed()
        await duo.step()
        for player in OTS:
            duo.capture(player)
        await duo.step()
        duo.leave_battles()
        await duo.step(95)
        assert not srv.state.links
        assert srv.state.pending_captures["route_1"]["b"].key == KEYS["b"]
        assert not duo.worlds["a"].events("capture")
        duo.sockets["a"] = await connect()
        duo.worlds["a"].connected = True
        duo.drop_receipts.add("a")
        await duo.step(95)
        assert len(srv.state.links) == 1
        original = duo.worlds["a"].events("capture")[-1]["report"]
        assert duo.worlds["a"].io.report_outbox.pending_count(duo.worlds["a"].io.report_outbox) > 0
    duo.offline()
    restored = SLinkServer(data_dir=str(tmp_path), retry_same_species=False)
    assert not restored.state.load_failed
    duo.drop_receipts.clear()
    async with _tcp(restored) as (connect, _), TestClient(TestServer(build_app(restored))) as http:
        await duo.connect(connect)
        await duo.step(95)
        assert len(restored.state.links) == 1
        assert duo.worlds["a"].events("capture")[-1]["report"] == original
        for world in duo.worlds.values():
            assert world.io.report_outbox.pending_count(world.io.report_outbox) == 0
        status = await (await http.get("/api/status")).json()
        assert status["links"][0]["a_key"] == KEYS["a"]
        assert status["links"][0]["b_key"] == KEYS["b"]
        assert not any(status["limbo"].values())
        assert not any(restored.dispatch_errors.values())


@pytest.mark.asyncio
async def test_rollback_limbo_holds_only_affected_writes_and_admin_detaches(tmp_path, monkeypatch):
    monkeypatch.delenv("SLINK_ADMIN_KEY", raising=False)
    duo = Duo()
    srv = SLinkServer(data_dir=str(tmp_path), retry_same_species=False)
    async with _tcp(srv) as (connect, _), TestClient(TestServer(build_app(srv))) as http:
        await duo.connect(connect)
        await duo.step(65)
        for player in OTS:
            duo.capture(player)
        await duo.step()
        duo.leave_battles()
        await duo.step(95)
        assert len(srv.state.links) == 1
        for player, world in duo.worlds.items():
            for box in range(world.d["BOXES_PER_STORE"]):
                for slot in range(30):
                    world.set_box(box, slot, None)
            world.set_party([duo.starter(player), duo.catch(player)])
            world.fire("pc_withdraw")
        await duo.step(65)
        assert all(world.party_hp(1) == 20 for world in duo.worlds.values())
        duo.worlds["a"].set_party([duo.starter("a")])
        await duo.step(620)
        status = await (await http.get("/api/status")).json()
        incident = status["limbo"]["a"][KEYS["a"]]
        srv.state.queued_commands["b"] += [
            {"cmd": "force_faint", "key": KEYS["b"]},
            {"cmd": "force_faint", "key": key_of(PIDS["b"], OTS["b"])},
        ]
        await duo.step(65)
        assert duo.worlds["b"].party_hp(1) == 20
        assert duo.worlds["b"].party_hp(0) == 0
        html = await (await http.get("/")).text()
        assert 'class="mon-recovery"' in html
        body = {"player": "a", "key": KEYS["a"], "revision": incident["revision"], "action": "detach"}
        assert (await http.post("/api/recovery", json={**body, "revision": "stale"})).status == 409
        response = await http.post("/api/recovery", json=body)
        assert response.status == 200, await response.text()
        await duo.step(65)
        assert not srv.state.links and not srv.state.recovery.limbo["a"]
        assert srv.state.unpaired_mons["route_1"]["b"].key == KEYS["b"]
        assert duo.worlds["b"].party_hp(1) == 20
        assert not any(srv.dispatch_errors.values())
    restored = SLinkServer(data_dir=str(tmp_path), retry_same_species=False)
    assert restored.state.unpaired_mons["route_1"]["b"].key == KEYS["b"]
    assert restored.state.recovery.control(restored.state, "b")["cancelled"][KEYS["b"]] > 0


@pytest.mark.asyncio
@pytest.mark.parametrize("mover", ["a", "b"])
async def test_offline_pc_intent_survives_restart_then_withdrawal_and_linked_death(tmp_path, monkeypatch, mover):
    monkeypatch.delenv("SLINK_ADMIN_KEY", raising=False)
    partner = "b" if mover == "a" else "a"
    duo = Duo()
    srv = SLinkServer(data_dir=str(tmp_path), retry_same_species=False)
    async with _tcp(srv) as (connect, _):
        await duo.connect(connect)
        await duo.step(65)
        for player in OTS:
            duo.capture(player)
        await duo.step()
        duo.leave_battles()
        await duo.step(95)
        for player, world in duo.worlds.items():
            for box in range(world.d["BOXES_PER_STORE"]):
                for slot in range(30):
                    world.set_box(box, slot, None)
            world.set_party([duo.starter(player), duo.catch(player)])
            world.fire("pc_withdraw")
        await duo.step(65)
        assert all(KEYS[p] in srv.state.party_keys[p] for p in OTS)

        duo.worlds[partner].connected = False
        duo.sockets[partner][1].close()
        await duo.sockets[partner][1].wait_closed()
        await duo.step()
        world = duo.worlds[mover]
        world.set_party([duo.starter(mover)])
        world.set_box(0, 0, duo.catch(mover))
        world.fire("pc_deposit")
        await duo.step(65)
        pending = dict(srv.state.sync_versions[partner][KEYS[partner]])
        assert pending["active"] and pending["cmd"] == "box_mon"
    duo.offline()

    restored = SLinkServer(data_dir=str(tmp_path), retry_same_species=False)
    assert restored.state.sync_versions[partner][KEYS[partner]] == pending
    async with _tcp(restored) as (connect, _), TestClient(TestServer(build_app(restored))) as http:
        await duo.connect(connect)
        await duo.step(95)
        assert all(KEYS[p] not in restored.state.party_keys[p] for p in OTS)
        assert all(world.party_hp(1) == 0 for world in duo.worlds.values())
        assert restored.state.mon_stats[partner][KEYS[partner]]["spDef"] == 15
        assert not restored.state.sync_versions[partner][KEYS[partner]]["active"]

        world = duo.worlds[mover]
        world.set_box(0, 0, None)
        world.set_party([duo.starter(mover), duo.catch(mover)])
        world.fire("pc_withdraw")
        await duo.step(95)
        assert all(KEYS[p] in restored.state.party_keys[p] for p in OTS), {
            p: ([(r["key"], r["reason"]) for r in w.events("sync_retrieve_failed")],
                w.client.deferred.pending(w.client.deferred, w.frame))
            for p, w in duo.worlds.items()
        }
        assert all(world.party_hp(1) == 20 for world in duo.worlds.values())
        assert duo.worlds[partner].events("sync_retrieve_done")[-1]["key"] == KEYS[partner]

        for world in duo.worlds.values():
            world.battle_ok = True
            world.enter_battle([mon_record(99, 99, species=19)], trainer_id=1)
        await duo.step(35)
        dead = {**duo.catch(mover), "hp": 0}
        duo.worlds[mover].set_party([duo.starter(mover), dead])
        duo.worlds[mover].fire("faint")
        await duo.step(65)
        assert duo.worlds[partner].party_hp(1) == 0
        assert restored.state.links[0].status == LinkStatus.DEAD
        duo.leave_battles()
        await duo.step(620)
        assert restored.state.links[0].status == LinkStatus.MEMORIAL
        status = await (await http.get("/api/status")).json()
        for player, world in duo.worlds.items():
            matching = [mon for mon in status["players"][player]["pc_boxes"] if mon["key"] == KEYS[player]]
            assert len(matching) == 1 and matching[0]["box"] == world.d["BOXES_PER_STORE"] - 1
            assert world.events("memorialize_done")[-1]["key"] == KEYS[player]
            assert not restored.state.pending_memorials[player]
        assert not any(status["limbo"].values())
        assert not any(restored.dispatch_errors.values())
