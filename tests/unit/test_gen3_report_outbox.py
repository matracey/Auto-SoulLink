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
