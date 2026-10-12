"""Production session/reducer over modeled RAM and durable filesystem primitives."""
from __future__ import annotations

import json

import lupa
import pytest

from tests.unit.gen3_world import REPO, World, key_of, mon_record

OT, A, B, C = 0xABCD, 0x11111111, 0x22222222, 0x33333333
KA, KB, KC = (key_of(pid, OT) for pid in (A, B, C))
BINDING = {"run_id": "run-1", "player": "a", "ot_id": "0000ABCD", "rom_sha1": "ab" * 20}
MODULE = (REPO / "lua/gen3/report_outbox.lua").as_posix()
JSON = (REPO / "lua/json_codec.lua").as_posix()


class Disk:
    def __init__(self):
        self.files = {}
        self.fail = False
        self.count = 0

    def factory(self, L):
        module = L.eval(f'dofile("{MODULE}")')
        codec = L.eval(f'dofile("{JSON}")')

        def lock(path):
            fresh = path not in self.files
            self.files.setdefault(path, "")
            return L.table(path=path), fresh

        def create(path, text):
            assert path not in self.files
            self.files[path] = text
            return True

        def write(handle, text):
            self.files[handle.path] = text
            return True

        def replace(path, text):
            if self.fail:
                raise OSError("durable replacement failed")
            self.files[path] = text
            return True

        fs = L.table(lock=lock, close=lambda _: True, read_file=lambda p: self.files.get(p),
                     read_handle=lambda h: self.files[h.path], create_file=create,
                     write_handle=write, replace_file=replace)
        store = module.file_store(L.table(fs=fs, json=codec, path="reports"))

        def new_id():
            self.count += 1
            return f"{self.count:032x}"

        return module.new(L.table(store=store, json=codec, player="a", rom_sha1="ab" * 20, new_id=new_id))


def test_file_store_reopens_ordered_reports_with_matching_receipts():
    disk = Disk()
    lua = lupa.LuaRuntime(unpack_returned_tuples=True)
    out = disk.factory(lua)
    out.bind(out, lua.table_from(BINDING), BINDING["ot_id"])
    out.append(out, "area_enter", lua.table(area_id="route_1"))
    first = out.head(out).fields.report
    reopened = disk.factory(lua)
    reopened.bind(reopened, lua.table_from(BINDING), BINDING["ot_id"])
    assert reopened.head(reopened).fields.report.number == first.number
    reopened.ack(reopened, lua.table(stream="wrong", number=first.number))
    assert reopened.pending_count(reopened) == 1
    reopened.ack(reopened, first)
    assert reopened.pending_count(reopened) == 0


def test_file_store_refuses_a_missing_guard_beside_existing_reports():
    disk = Disk()
    lua = lupa.LuaRuntime(unpack_returned_tuples=True)
    out = disk.factory(lua)
    out.bind(out, lua.table_from(BINDING), BINDING["ot_id"])
    out.append(out, "area_enter", lua.table(area_id="route_1"))
    del disk.files["reports.guard"]
    reopened = disk.factory(lua)
    with pytest.raises(lupa.LuaError, match="guard missing"):
        reopened.load(reopened, BINDING["ot_id"])
    assert reopened.failure is not None


def world(disk=None, *, connected=True):
    disk = disk or Disk()
    w = World(outbox=disk.factory, connected=connected)
    mask = w.profile["rom"]["PP_UP_GET_MASK_ADDR"] - 0x08000000
    for i, value in enumerate((3, 12, 48, 192)):
        w.rom[mask + i] = value
    w.set_party([mon_record(A, OT), mon_record(B, OT)])
    if connected:
        w.step_to(60)
        w.command(cmd="report_binding", binding=BINDING)
        w.step()
    return w, disk


def rows(disk):
    return json.loads(disk.files["reports.json"].split("\n", 2)[2])


def ack(w, event):
    w.command(cmd="report_ack", stream=event["report"]["stream"], number=event["report"]["number"])
    w.step()


def capture(w):
    w.set_party([mon_record(A, OT), mon_record(B, OT), mon_record(C, OT, species=19)])
    w.fire("mon_given")
    w.step()


def test_disconnected_capture_persists_area_and_replays_only_after_binding():
    w, disk = world()
    w.connected = False
    w.step()
    capture(w)
    saved = rows(disk)["reports"]
    assert len(saved) == 1 and saved[0]["event"] == "capture"
    assert saved[0]["fields"]["area_id"] == "route_1"
    assert saved[0]["fields"]["stats"]["spDef"] == 15
    assert not w.events("capture")
    w.set_location(3, 20)
    w.connected = True
    w.step()
    assert not w.events("capture")
    w.command(cmd="report_binding", binding=BINDING)
    w.step()
    event = w.events("capture")[0]
    assert event["key"] == KC and event["area_id"] == "route_1"
    ack(w, event)
    assert rows(disk)["reports"] == []

def test_partial_baseline_retains_real_pc_catch_and_excludes_old_resident():
    w = World()
    w.set_party([mon_record(A, OT), mon_record(B, OT)])
    w.set_box(12, 0, mon_record(0x88888888, OT, species=25))
    original = w.parts.reads.read_box
    w.parts.reads.read_box = lambda box: (None, "scan interrupted") if box == 1 else original(box)
    w.step_to(60)
    assert not w.client.state.box_baselined
    w.enter_battle([mon_record(C, OT, species=19)])
    w.set_box(10, 0, mon_record(C, OT, species=19))
    w.fire("capture_wild")
    w.fire("pc_move")
    w.step()
    assert not w.events("capture")
    assert len(w.client.state.pending_box_acq) == 1
    w.parts.reads.read_box = original
    w.leave_battle(outcome=7)
    w.step(90)
    captures = w.events("capture")
    assert len(captures) == 1 and captures[0]["key"] == KC
    assert captures[0]["area_id"] == "route_1"
    assert w.client.state.known[key_of(0x88888888, OT)]


def test_partial_baseline_without_attribution_remains_visible_uncertainty():
    w = World()
    w.set_party([mon_record(A, OT), mon_record(B, OT)])
    w.set_box(12, 0, mon_record(0x88888888, OT, species=25))
    original = w.parts.reads.read_box
    w.parts.reads.read_box = lambda box: (None, "scan interrupted") if box == 1 else original(box)
    w.step_to(60)
    w.fire("mon_given")
    w.step()
    w.parts.reads.read_box = original
    w.step(90)
    assert not w.events("capture")
    assert len(w.client.state.pending_box_acq) == 1
    assert any("CAPTURE UNCERTAIN" in str(h) for h in w.hud)
    assert not w.client.state.known[key_of(0x88888888, OT)]
