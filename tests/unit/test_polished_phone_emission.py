"""Persisted Phone-driver evidence, from real Lua over an isolated native engine.

The engine below owns menu/input transitions and RAM; it never reads the driver's
observations or manufactures its JSON. The driver, pol_lib, charmap and JSON codec
are loaded as shipped. This is an emission seam, not cartridge qualification.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from lupa import LuaRuntime

REPO = Path(__file__).resolve().parents[2]
WINDOWS = {
    "s0.entry", "s1.entry", "s4.scroll", "s4.entry", "s3.entry",
    "s3.delete", "s3.native_call",
}
HOOK_NAMES = (
    "OWPlayerInput", "StartMenu", "SetInitialOptions.joypad_loop", "PokeGear",
    "PokegearPhoneContactSubmenu", "PokegearPhoneContactSubmenu.Delete",
    "MakePhoneCallFromPokegear", "SlinkPhone_CallGate", "SlinkPhone_CallerName",
    "SlinkPhone_CanDelete", "SlinkPhone_CountSetBits", "YesNoBox", "SlinkPanel",
)


class NativePhone:
    """Small native menu machine, with Gambatte's zero-valued write callbacks."""

    def __init__(self, lua):
        self.lua = lua
        self.wram = bytearray(0x8000)
        self.frame = 0
        self.pc = 0x4100
        self.sp = 0xDFF0
        self.bank = 0x24
        self.svbk = 1
        self.mode = "overworld"
        self.buttons = {}
        self.previous = set()
        self.inputs = []
        self.exec_trace = []
        self.stores = []
        self.hooks = {}
        self.registrations = []
        self.removals = []
        self.next_handle = 1
        self.symbols = {
            "wPhoneList": [1, 0xD100],
            "wPokegearPhoneCursorPosition": [1, 0xD110],
            "wPokegearPhoneScrollPosition": [1, 0xD111],
            "wTilemap": [0, 0xC400],
            "wSlinkMailbox": [0, 0xC800],
            "wSlinkMailboxEnd": [0, 0xC845],
            "wSlinkPanelText": [0, 0xC850],
            "hROMBank": [0, 0xFF9D],
            "rSVBK": [0, 0xFF70],
        }
        fields = (
            "wPokegearFlags", "wJumptableIndex", "wPokegearCard",
            "wNumSetBits", "wCurCaller", "wPokegearPhoneSubmenuCursor",
            "wPokegearPhoneSelectedPerson", "wMapGroup", "wMapNumber",
            "wScriptRunning", "wMenuCursorY",
        )
        self.symbols.update({name: [1, 0xD200 + i] for i, name in enumerate(fields)})
        self.symbols["wMenuItemsList"] = [1, 0xD240]
        self.symbols.update({name: [0x24, 0x4100 + i * 0x20]
                             for i, name in enumerate(HOOK_NAMES)})
        self.put("wMapGroup", 24)
        self.put("wMapNumber", 3)
        self.put("wCurCaller", 7)
        self.put("wSlinkMailbox", 0x53)
        self.put("wSlinkMailbox", 0x4C, 1)
        self.put("wSlinkMailbox", 0x4E, 2)
        self.put("wSlinkMailbox", 0x4B, 3)
        glyphs = lua.execute("return dofile(...).glyphs", (REPO / "data/games/polished_crystal/charmap.lua").as_posix())
        self.charset = {value: key for key, value in glyphs.items() if len(value) == 1}
        self.draw()

    def offset(self, name, plus=0):
        bank, address = self.symbols[name]
        address += plus
        return address - 0xC000 if address < 0xD000 else (bank or 1) * 0x1000 + address - 0xD000

    def get(self, name, plus=0):
        return self.wram[self.offset(name, plus)]

    def put(self, name, value, plus=0):
        self.wram[self.offset(name, plus)] = value

    def read(self, address, domain="System Bus"):
        if domain == "WRAM":
            return self.wram[address]
        if domain == "System Bus":
            if address == 0xFF70:
                return self.svbk
            if address == self.symbols["hROMBank"][1]:
                return self.bank
            if 0xC000 <= address < 0xD000:
                return self.wram[address - 0xC000]
            if 0xD000 <= address < 0xE000:
                return self.wram[(self.svbk or 1) * 0x1000 + address - 0xD000]
        return 0

    def read_array(self, address, size, domain):
        # BizHawk arrays are zero-indexed; L.wbytes itself is one-indexed.
        return self.lua.table_from({i: self.read(address + i, domain) for i in range(size)})

    def register(self, kind, callback, address, name, domain):
        handle = f"native-hook-{self.next_handle}"
        self.next_handle += 1
        self.hooks[handle] = (kind, callback, address, name, domain)
        self.registrations.append((handle, kind, address, name, domain))
        return handle

    def unregister(self, handle):
        self.removals.append(handle)
        self.hooks.pop(handle, None)

    def dispatch_write(self, address, domain):
        for kind, callback, watched, _, watched_domain in tuple(self.hooks.values()):
            if kind == "write" and watched == address and watched_domain == domain:
                callback(address, 0, 0)

    def host_write(self, address, value, domain):
        assert domain == "WRAM"
        self.wram[address] = value
        bus = address + 0xC000 if address < 0x1000 else 0xD000 + address % 0x1000
        self.dispatch_write(bus, "System Bus")

    def native_write(self, name, value, plus=0, *, bank=1):
        address = self.symbols[name][1] + plus
        self.svbk = bank
        offset = bank * 0x1000 + address - 0xD000
        old = self.wram[offset]
        self.wram[offset] = value
        self.stores.append({"frame": self.frame, "addr": address, "old": old,
                            "new": value, "svbk": bank, "pc": self.pc})
        self.dispatch_write(address, "System Bus")
        self.svbk = 1

    def execute(self, name, *, wrong_bank=False):
        bank, address = self.symbols[name]
        self.pc = address
        self.bank = bank + 1 if wrong_bank else bank
        self.exec_trace.append((self.frame, name, self.bank, address))
        for kind, callback, watched, _, domain in tuple(self.hooks.values()):
            if kind == "exec" and watched == address and domain == "System Bus":
                callback()
        self.bank = bank

    def contacts(self):
        bits = int.from_bytes(self.wram[self.offset("wPhoneList"):self.offset("wPhoneList") + 5], "little")
        native = [(bit, label, person) for bit, label, person in (
            (0, "Mom:", 1), (3, "Prof.Elm:", 5), (14, "Joey:", 15), (15, "Wade:", 16),
        ) if bits & (1 << bit)]
        return native + [(None, "SLink:", 38)]

    def line(self, y, text):
        base = self.offset("wTilemap", y * 20)
        text = text.ljust(20)[:20]
        for i, char in enumerate(text):
            self.wram[base + i] = self.charset.get(char, self.charset[" "])

    def draw(self):
        for y in range(18):
            self.line(y, "")
        if self.mode in {"phone", "submenu", "yes", "panel", "call"}:
            contacts = self.contacts()
            scroll = self.get("wPokegearPhoneScrollPosition")
            cursor = self.get("wPokegearPhoneCursorPosition")
            for row, (_, label, _) in enumerate(contacts[scroll:scroll + 4]):
                self.line(4 + 2 * row, "  " + label)
                if label == "SLink:":
                    self.line(5 + 2 * row, "   Soul Link")
            self.put("wTilemap", 240, (4 + 2 * cursor) * 20 + 1)
            self.line(14, "Whom do you want")
        if self.mode == "submenu":
            self.line(6, "Call")
            self.line(8, "Cancel")
            if self.get("wPokegearPhoneSelectedPerson") not in {1, 5, 38}:
                self.line(8, "Delete")
                self.line(10, "Cancel")
        elif self.mode == "yes":
            self.line(12, "Delete? YES / NO")
        elif self.mode == "panel":
            self.line(12, "SOUL LINK")
            self.line(14, "NO CLIENT")
        elif self.mode == "call":
            self.line(14, "Mom is calling")

    def phone(self, *, reset=False):
        self.mode = "phone"
        self.put("wJumptableIndex", 0x0A)
        self.put("wPokegearCard", 2)
        self.put("wScriptRunning", 1)
        if reset:
            self.put("wPokegearPhoneCursorPosition", 0)
            self.put("wPokegearPhoneScrollPosition", 0)
        # Deliberately retain the cartridge's anomalous s4 count as raw input.
        self.put("wNumSetBits", 33 if len(self.contacts()) == 5 else len(self.contacts()))
        self.execute("SlinkPhone_CountSetBits")
        self.execute("SlinkPhone_CallerName", wrong_bank=True)
        self.execute("SlinkPhone_CallerName")

    def set_buttons(self, buttons):
        self.buttons = dict(buttons.items())
        self.inputs.append((self.frame, self.buttons.copy()))

    def advance(self):
        self.frame += 1
        assert self.frame < 15000, "native fixture driver failed to settle"
        pressed = {name for name, value in self.buttons.items() if value}
        new = pressed - self.previous
        self.previous = pressed
        if self.mode == "overworld":
            self.execute("OWPlayerInput")
            if "Start" in new:
                self.mode = "start"
                self.put("wMenuItemsList", 1)
                self.put("wMenuItemsList", 7, 1)
                self.put("wMenuCursorY", 1)
                self.execute("StartMenu")
        elif self.mode == "start" and "A" in new:
            self.execute("PokeGear")
            self.phone(reset=True)
        elif self.mode == "phone":
            index = self.get("wPokegearPhoneCursorPosition") + self.get("wPokegearPhoneScrollPosition")
            if new & {"Down", "Up"}:
                index = max(0, min(len(self.contacts()) - 1, index + (1 if "Down" in new else -1)))
                self.native_write("wPokegearPhoneCursorPosition", min(index, 3))
                self.native_write("wPokegearPhoneScrollPosition", max(0, index - 3))
            elif "A" in new:
                self.mode = "submenu"
                self.put("wJumptableIndex", 0x0B)
                self.put("wPokegearPhoneSelectedPerson", self.contacts()[index][2])
                self.put("wPokegearPhoneSubmenuCursor", 0)
                self.execute("PokegearPhoneContactSubmenu")
                self.execute("SlinkPhone_CanDelete")
            elif "B" in new:
                self.mode = "overworld"
                self.put("wJumptableIndex", 0)
                self.put("wScriptRunning", 0)
                self.execute("OWPlayerInput")
        elif self.mode == "submenu":
            if "Down" in new:
                self.put("wPokegearPhoneSubmenuCursor", 1)
            elif "B" in new:
                self.phone()
            elif "A" in new:
                if self.get("wPokegearPhoneSubmenuCursor") == 1:
                    self.mode = "yes"
                    self.execute("PokegearPhoneContactSubmenu.Delete")
                    self.execute("YesNoBox")
                elif self.get("wPokegearPhoneSelectedPerson") == 38:
                    self.mode = "panel"
                    self.put("wJumptableIndex", 0x0C)
                    self.execute("SlinkPhone_CallGate")
                    self.execute("SlinkPanel")
                else:
                    self.mode = "call"
                    self.put("wJumptableIndex", 0x0D)
                    self.put("wCurCaller", 1)
                    self.execute("MakePhoneCallFromPokegear")
        elif self.mode == "yes" and "A" in new:
            self.pc = self.symbols["PokegearPhoneContactSubmenu.Delete"][1]
            self.native_write("wPhoneList", 0x7E, 3, bank=2)  # same bus address, wrong WRAM bank
            self.native_write("wPhoneList", 0x01, 4)
            self.native_write("wPhoneList", 0x00, 4)  # repeated-address ambiguity must survive
            self.native_write("wPhoneList", 0x00, 1)  # Joey's actual native deletion
            self.phone()
        elif (self.mode == "panel" and "B" in new) or (self.mode == "call" and new & {"A", "B"}):
            self.phone()
        self.draw()

    def register_value(self, name):
        return {"PC": self.pc, "SP": self.sp, "A": 0, "F": 0}.get(name.upper(), 0)


@pytest.fixture(scope="module")
def emitted_phone(tmp_path_factory):
    run = tmp_path_factory.mktemp("native-phone")
    lua = LuaRuntime(unpack_returned_tuples=True)
    engine = NativePhone(lua)
    symbols = run / "symbols.json"
    symbols.write_text(json.dumps(engine.symbols), encoding="utf-8")
    environment = {
        "SLINK_ROOT": REPO.as_posix(), "POL_RUN": run.as_posix(),
        "POL_OUT": (run / "phone.log").as_posix(), "POL_SYMS": symbols.as_posix(),
    }
    globals_ = lua.globals()
    globals_.fixture_getenv = environment.get
    lua.execute("os.getenv = function(name) return fixture_getenv(name) end")
    globals_.memory = lua.table_from({
        "read_u8": engine.read, "write_u8": engine.host_write,
        "read_bytes_as_array": engine.read_array,
        "getmemorydomainsize": lambda domain: len(engine.wram) if domain == "WRAM" else 0x10000,
        "getregister": engine.register_value,
    })
    globals_.emu = lua.table_from({
        "framecount": lambda: engine.frame, "frameadvance": engine.advance,
        "getregister": engine.register_value,
    })
    globals_.joypad = lua.table_from({"set": engine.set_buttons})
    globals_.event = lua.table_from({
        "on_bus_exec": lambda cb, addr, name, domain: engine.register("exec", cb, addr, name, domain),
        "on_bus_write": lambda cb, addr, name, domain: engine.register("write", cb, addr, name, domain),
        "unregisterbyid": engine.unregister,
    })
    globals_.client = lua.table_from({
        "speedmode": lambda _: None, "screenshot": lambda _: None,
        "saveram": lambda: None, "exit": lambda: None,
    })
    globals_.console = lua.table_from({"log": lambda _: None})
    globals_.gameinfo = lua.table_from({"getromhash": lambda: "fixture-native-phone"})
    ok, result = lua.execute("return pcall(dofile, ...)", (REPO / "tools/polished_live/phone.lua").as_posix())
    assert not ok and result == "pol-live-finished", result
    path = run / "phone.json"
    assert path.is_file(), "actual phone.lua must persist its raw evidence"
    return json.loads(path.read_text(encoding="utf-8")), engine


def scenarios(evidence):
    return {row["tag"]: row for row in evidence["scenarios"]}


def test_persisted_entry_readings_are_native_snapshots(emitted_phone):
    evidence, _ = emitted_phone
    cases = scenarios(evidence)
    assert set(cases) == {"s0", "s1", "s4", "s3"}
    expected = {"s0": ("0000000000", [0, 0]), "s1": ("0100000000", [1, 0]),
                "s4": ("09c0000000", [3, 1]), "s3": ("0940000000", [3, 0])}
    for tag, (phone_list, cursor) in expected.items():
        entry = cases[tag]["entry"]
        assert entry["cursor"] == cursor
        readings = entry["readings"]
        assert set(readings) == {"before", "submenu", "call", "after_b"}
        before, submenu, call, after = (readings[point] for point in ("before", "submenu", "call", "after_b"))
        for point, reading in readings.items():
            assert reading["point"] == point
            assert reading["stage"] == f"{tag}.entry"
            assert reading["phone_list"].lower() == phone_list
            assert [reading["cursor"], reading["scroll"]] == cursor
            assert len(reading["tilemap_hex"]) == 20 * 18 * 2
            assert len(reading["mailbox_hex"]) == 69 * 2
            assert reading["map_group"] == 24 and reading["map_number"] == 3
            assert reading["pc"] > 0 and reading["sp"] > 0
            assert reading["rom_bank"] == 0x24 and reading["svbk"] == 1
            assert reading in evidence["observations"]
        assert before["state"] == after["state"] == 0x0A
        assert submenu["submenu_cursor"] == 0
        assert call["selected_person"] == 38
        assert before["caller"] == call["caller"] == after["caller"] == 7
        assert call["hits"]["MakePhoneCallFromPokegear"] == before["hits"]["MakePhoneCallFromPokegear"]
        assert call["hits"]["SlinkPhone_CallGate"] == before["hits"]["SlinkPhone_CallGate"] + 1
        assert call["hits"]["SlinkPanel"] == before["hits"]["SlinkPanel"] + 1
        assert "Call" in entry["menu"] and "Cancel" in entry["menu"] and "Delete" not in entry["menu"]
        assert "SOUL LINK" in entry["entry_box"] and "NO CLIENT" in entry["entry_box"]
        assert "Whom do you want" in entry["back"]


def test_scroll_preserves_anomalous_native_count_without_normalizing(emitted_phone):
    evidence, _ = emitted_phone
    s4 = scenarios(evidence)["s4"]
    assert s4["num_set_bits"] == 33
    before, after = (s4["scroll_readings"][point] for point in ("before", "after"))
    assert [before["cursor"], before["scroll"]] == [0, 0]
    assert [after["cursor"], after["scroll"]] == [3, 1]
    assert before["num_set_bits"] == after["num_set_bits"] == 33
    assert before["phone_list"] == after["phone_list"]
    assert s4["scrolled"].count("SLink:") == 1
    assert all(row["num_set_bits"] == 33 for row in s4["entry"]["readings"].values())


def test_deletion_and_positive_native_call_keep_postdelete_readings(emitted_phone):
    evidence, _ = emitted_phone
    s3 = scenarios(evidence)["s3"]
    deletion = s3["deletion"]["readings"]
    assert set(deletion) == {"before", "submenu", "confirm", "after"}
    assert deletion["before"]["phone_list"].lower() == "0940000000"
    assert deletion["submenu"]["selected_person"] == 15
    assert deletion["confirm"]["hits"]["YesNoBox"] > deletion["before"]["hits"]["YesNoBox"]
    assert deletion["after"]["phone_list"].lower() == "0900000000"
    assert deletion["after"]["num_set_bits"] == 3
    assert "Joey:" not in s3["after_delete"] and s3["after_delete"].count("SLink:") == 1
    native = s3["native_call"]["readings"]
    assert set(native) == {"before", "submenu", "call", "after"}
    assert all(row["phone_list"].lower() == "0900000000" for row in native.values())
    assert native["call"]["caller"] == 1
    assert native["call"]["hits"]["MakePhoneCallFromPokegear"] == native["before"]["hits"]["MakePhoneCallFromPokegear"] + 1
    assert native["after"]["state"] == 0x0A
    # Scenario's original first-screen list is immutable historical evidence.
    assert s3["phone_list"].lower() == "0940000000"
    assert "Joey:" in s3["list"]
    assert s3["entry"]["readings"]["before"]["phone_list"].lower() == "0940000000"


def test_inputs_and_bank_qualified_hooks_are_correlated_to_engine(emitted_phone):
    evidence, engine = emitted_phone
    inputs = evidence["inputs"]
    assert len(inputs) == len(engine.inputs)
    assert [(row["frame"], row["buttons"]) for row in inputs] == engine.inputs
    assert all(isinstance(row["stage"], str) and row["stage"] for row in inputs)
    accepted = [(frame, name, bank, pc) for frame, name, bank, pc in engine.exec_trace
                if bank == engine.symbols[name][0]]
    hooks = evidence["hooks"]
    assert [(row["frame"], row["name"], row["bank"], row["pc"]) for row in hooks] == accepted
    input_by_frame = {row["frame"] + 1: row for row in inputs}
    for row in hooks:
        assert row["stage"] == input_by_frame[row["frame"]]["stage"]
    assert evidence["hits"]["SlinkPhone_CallerName"] == sum(row["name"] == "SlinkPhone_CallerName" for row in hooks)
    for array in ("observations", "inputs", "hooks", "native_writes", "write_frames", "synth_writes"):
        rows = evidence[array]
        assert all(isinstance(row["seq"], int) for row in rows)
        assert [row["seq"] for row in rows] == sorted({row["seq"] for row in rows})
        assert [row["frame"] for row in rows] == sorted(row["frame"] for row in rows)


def test_stage_accounting_contains_raw_readings_without_verdicts(emitted_phone):
    evidence, _ = emitted_phone
    coverage = {row["id"]: row for row in evidence["coverage"]}
    expected = WINDOWS | {"s0.list", "s1.list", "s4.list", "s3.list", "terminal"}
    assert expected <= coverage.keys()
    for stage in coverage.values():
        assert stage["frame_start"] <= stage["frame_end"]
        assert stage["observations"]
        assert not {"ok", "verdict", "result", "check_count", "checks_failed"} & stage.keys()
        for observation in stage["observations"]:
            assert observation in evidence["observations"]
            assert observation["stage"] == stage["id"]
            assert stage["frame_start"] <= observation["frame"] <= stage["frame_end"]
            assert {"seq", "point", "state", "pc", "sp", "rom_bank", "svbk",
                    "phone_list", "tilemap_hex", "mailbox_hex", "hits"} <= observation.keys()
    for event in evidence["inputs"]:
        if event["stage"] in coverage:
            stage = coverage[event["stage"]]
            assert stage["frame_start"] <= event["frame"] < stage["frame_end"]


def test_exact_scoped_write_hooks_leave_no_watchers_alive(emitted_phone):
    evidence, engine = emitted_phone
    registrations = [row for row in engine.registrations if row[1] == "write"]
    phone_address = engine.symbols["wPhoneList"][1]
    cursor_address = engine.symbols["wPokegearPhoneCursorPosition"][1]
    exact = {phone_address + i for i in range(5)} | {cursor_address, cursor_address + 1}
    assert len(registrations) == 7 * len(WINDOWS)
    assert {row[2] for row in registrations} == exact
    assert all(row[4] == "System Bus" for row in registrations)
    assert all(row[0] in engine.removals for row in registrations)
    assert not any(row[0] == "write" for row in engine.hooks.values())
    assert {row["stage"] for row in evidence["write_frames"]} == WINDOWS


def test_native_byte_operations_use_frame_images_not_callback_value(emitted_phone):
    evidence, engine = emitted_phone
    operations = evidence["native_writes"]
    by_frame = {(row["stage"], row["frame"]): row for row in evidence["write_frames"]}
    assert operations
    flattened = [operation for frame in evidence["write_frames"] for operation in frame["operations"]]
    assert flattened == operations
    for operation in operations:
        assert operation["domain"] == "System Bus"
        assert operation["callback_value"] == 0
        assert operation["svbk"] == 1
        assert operation["stage"] in WINDOWS
        assert operation["observed"] in range(256)
        assert operation["pc"] > 0 and operation["rom_bank"] == 0x24
        frame = by_frame[operation["stage"], operation["frame"]]
        assert frame["frame_start"] + 1 == frame["frame"]
        assert len(frame["before"]["phone_list_hex"]) == len(frame["after"]["phone_list_hex"]) == 10
        assert len(frame["before"]["cursor_hex"]) == len(frame["after"]["cursor_hex"]) == 4
        same_address = [row for row in frame["operations"] if row["addr"] == operation["addr"]]
        if len(same_address) > 1:
            assert operation["old"] is None and operation["new"] is None
            assert operation["reason"] == "multiple_stores_in_frame"
        else:
            symbol = operation["span"]
            offset = operation["addr"] - engine.symbols[symbol][1]
            key = "phone_list_hex" if symbol == "wPhoneList" else "cursor_hex"
            assert operation["old"] == bytes.fromhex(frame["before"][key])[offset]
            assert operation["new"] == bytes.fromhex(frame["after"][key])[offset]
    delete = [row for row in operations if row["span"] == "wPhoneList"]
    assert len(delete) == 3
    assert all(row["stage"] == "s3.delete" for row in delete)
    actual = next(row for row in delete if row["addr"] == engine.symbols["wPhoneList"][1] + 1)
    assert actual["old"] == 0x40 and actual["new"] == actual["observed"] == 0
    ambiguous = [row for row in delete if row["addr"] == engine.symbols["wPhoneList"][1] + 4]
    assert [row["observed"] for row in ambiguous] == [1, 0]
    frame = by_frame[actual["stage"], actual["frame"]]
    assert frame["before"]["phone_list_hex"].lower() == "0940000000"
    assert frame["after"]["phone_list_hex"].lower() == "0900000000"
    assert any(row["svbk"] == 2 for row in engine.stores), "wrong-bank control must actually execute"
    assert not any(row["addr"] == engine.symbols["wPhoneList"][1] + 3 for row in delete)
    assert not any(row["span"] == "wPhoneList" and row["stage"] != "s3.delete" for row in operations)


def test_synthetic_writes_are_separate_and_final_snapshot_is_complete(emitted_phone):
    evidence, engine = emitted_phone
    synth = evidence["synth_writes"]
    assert len(synth) == 21  # flags + four five-byte fixture phone lists
    assert all(row["domain"] == "WRAM" for row in synth)
    assert not any(row in evidence["native_writes"] for row in synth)
    terminal = next(row for row in evidence["observations"] if row["point"] == "terminal")
    assert terminal["wram_size"] == len(engine.wram)
    assert bytes.fromhex(terminal["wram_hex"]) == bytes(engine.wram)
    assert terminal["phone_list"].lower() == "0900000000"
    assert terminal["caller"] == 1 and terminal["state"] == 0
    assert terminal["map_group"] == 24 and terminal["map_number"] == 3
    assert terminal["script_running"] == 0
    assert terminal["pc"] == engine.symbols["OWPlayerInput"][1]
    assert evidence.get("result") != "PASS" and evidence.get("status") != "PASS"
