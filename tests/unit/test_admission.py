"""Neutral actual-byte admission controls under Lua 5.4; no runtime qualification."""

import hashlib
import itertools
from pathlib import Path

import pytest
from lupa.lua54 import LuaError, LuaRuntime

ROOT = Path(__file__).resolve().parents[2]


class World:
    def __init__(self, image=b"known cartridge bytes", *, unknown=False, rows=None, source=None):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        path = (ROOT / "lua/admission.lua").as_posix()
        self.module = (self.lua.eval("dofile")(path) if source is None
                       else self.lua.eval("load")(source, "@" + path)())
        self.image = image
        self.reads = 0
        self.trace = []
        self.after_describe = None
        self.catalog = rows or [{"name": "one", "hashes": [hashlib.sha1(image).hexdigest()],
                                 "eligible": True, "kind": "clean", "anchors": []}]
        self.rows = self.lua.table_from(self.catalog, recursive=True)
        options = self.lua.eval("""function(rows, read, size, changed, unknown)
            return {
                acquire=function() return {size=size(), read_u8=function(i) return read(i) end} end,
                catalog=function() return rows end,
                hashes=function(row) return row.hashes end,
                eligible=function(row) return row.eligible, 'catalog gate closed' end,
                anchors=function(row) return row.anchors end,
                kind=function(row, mode) return row.kind end,
                describe=function(row) changed(); return {title=row.name,nested={source='verified'}} end,
                allow_unknown_hash=unknown,
            }
        end""")(self.rows, self.read, lambda: len(self.image), self.describe, unknown)
        self.options = options
        self.engine = self.module.new(options)

    def read(self, offset):
        self.reads += 1
        self.trace.append(("read", offset))
        return self.image[int(offset)]

    def describe(self):
        self.trace.append(("describe",))
        if self.after_describe:
            self.after_describe()

    def admit(self):
        return self.engine.admit(self.engine, self.lua.table(rom_sha1="not trusted"))


@pytest.mark.parametrize("text,expected", [(b"", "da39a3ee5e6b4b0d3255bfef95601890afd80709"),
                                         (b"abc", "a9993e364706816aba3e25717850c26c9cd0d89d")])
def test_neutral_sha1_has_standard_known_answers(text, expected):
    world = World()
    reader = world.lua.eval("function(read) return function(i) return read(i) end end")(lambda i: text[i])
    assert world.module.sha1(reader, len(text)) == expected


def test_known_artifact_is_hashed_and_decision_is_deeply_immutable():
    world = World()
    decision = world.admit()
    assert decision.title == "one"
    assert decision.rom_sha1 == hashlib.sha1(world.image).hexdigest()
    assert decision.rehashed is True and decision.admitted_by == "sha1"
    assert world.reads == len(world.image) * 2
    with pytest.raises(LuaError, match="immutable"):
        world.lua.eval("function(value) value.title='changed' end")(decision)
    with pytest.raises(LuaError, match="immutable"):
        world.lua.eval("function(value) value.nested.source='changed' end")(decision)
    world.rows[1].name = "later catalog mutation"
    assert decision.title == "one"


@pytest.mark.parametrize("value", [None, True, -1, 256, 0.5, "0", float("nan"), float("inf")])
@pytest.mark.parametrize("offset", [0, 511, 512, 1023])
@pytest.mark.parametrize("reacquire", [False, True])
def test_invalid_acquired_byte_refuses_at_exact_offset(value, offset, reacquire):
    world = World(bytes(range(256)) * 4)
    invalid = list(world.image)
    invalid[offset] = value
    if reacquire:
        world.after_describe = lambda: setattr(world, "image", invalid)
    else:
        world.image = invalid

    decision, reason = world.admit()

    assert decision is None
    assert reason.endswith(f"unavailable or invalid artifact byte at {offset}")
    assert world.reads == offset + 1 + (len(invalid) if reacquire else 0)


def test_lazy_error_edit_preserves_admission_results_and_read_order():
    from tools import gen2_code_digest as code

    eager = b'assert(byte(value), "unavailable or invalid artifact byte at " .. offset)'
    lazy = b'if not byte(value) then error("unavailable or invalid artifact byte at " .. offset) end'
    optimized = (ROOT / "lua/admission.lua").read_bytes().replace(b"\r\n", b"\n")
    assert optimized.count(lazy) == 1
    sources = [optimized.replace(lazy, eager, 1), optimized]
    for blob, source in zip(code.C5_LAZY_ERROR_BLOBS, sources, strict=True):
        assert hashlib.sha1(f"blob {len(source)}\0".encode() + source).hexdigest() == blob

    values = [None, True, False, -1, 256, 0.5, "0", float("nan"), float("inf"), float("-inf"), {}, []]
    cases = [("valid", None, None, False), ("changed", None, None, True)]
    cases += [("invalid", value, offset, reacquire)
              for value, offset, reacquire in itertools.product(values, (0, 511, 512, 1023), (False, True))]
    for kind, value, offset, reacquire in cases:
        outcomes = []
        for source in sources:
            world = World(bytes(range(256)) * 4, source=source)
            if kind != "valid":
                changed = list(world.image)
                changed[0 if offset is None else offset] = 255 if kind == "changed" else value
                if reacquire:
                    world.after_describe = lambda w=world, image=changed: setattr(w, "image", image)
                else:
                    world.image = changed
            result = world.admit()
            if isinstance(result, tuple):
                assert result[0] is None
                if kind == "invalid":
                    assert result[1].endswith(f"unavailable or invalid artifact byte at {offset}")
                else:
                    assert kind == "changed" and "artifact changed during admission" in result[1]
                outcome = result
            else:
                assert kind == "valid"
                outcome = (result.title, result.rom_sha1, result.kind, result.admitted_by,
                           result.rehashed, result.nested.source)
                assert result.rom_sha1 == hashlib.sha1(world.image).hexdigest()
            outcomes.append((outcome, world.trace))
        assert outcomes[0] == outcomes[1], (kind, value, offset, reacquire)

    messages = []
    for source in sources:
        world = World(source=source)
        count = world.lua.execute("""
            local original, messages = assert, 0
            assert = function(value, message, ...)
                if type(message) == 'string' and message:match('^unavailable or invalid artifact byte at ') then
                    messages = messages + 1
                end
                return original(value, message, ...)
            end
            return function() return messages end
        """)
        assert world.admit().rehashed is True
        messages.append(count())
    assert messages == [2 * len(world.image), 0]


def test_unknown_hash_never_falls_back_without_explicit_binder_policy():
    row = {"name": "one", "hashes": ["0" * 40], "eligible": True, "kind": "randomized",
           "anchors": [{"offset": 0, "hex": "6b6e6f776e"}]}
    world = World(rows=[row])
    decision, reason = world.admit()
    assert decision is None and "unknown" in reason
    explicit = World(rows=[row], unknown=True)
    assert explicit.admit().admitted_by == "anchors"


def test_required_anchors_are_checked_even_for_a_known_hash():
    world = World()
    world.rows[1].anchors = world.lua.table_from([{"offset": 0, "hex": "00"}], recursive=True)
    decision, reason = world.admit()
    assert decision is None and "anchor" in reason


@pytest.mark.parametrize("anchors", [[], [{"offset": 0, "hex": "f"}],
                                     [{"offset": -1, "hex": "00"}],
                                     [{"offset": 100, "hex": "00"}]])
def test_empty_or_malformed_fallback_anchor_sets_refuse(anchors):
    world = World(unknown=True, rows=[{"name": "one", "hashes": ["0" * 40],
                                      "eligible": True, "kind": "randomized", "anchors": anchors}])
    assert world.admit()[0] is None


def test_two_eligible_candidates_are_ambiguous_even_if_they_share_a_hash():
    world = World()
    world.rows[2] = world.rows[1]
    decision, reason = world.admit()
    assert decision is None and "ambiguous" in reason


def test_known_but_ineligible_hash_does_not_escape_through_anchor_fallback():
    image = b"known cartridge bytes"
    world = World(image, unknown=True, rows=[
        {"name": "blocked", "hashes": [hashlib.sha1(image).hexdigest()], "eligible": False,
         "kind": "clean", "anchors": []},
        {"name": "fallback", "hashes": ["0" * 40], "eligible": True,
         "kind": "randomized", "anchors": [{"offset": 0, "hex": "6b6e6f776e"}]},
    ])
    assert world.admit()[0] is None


def test_rom_replacement_during_evaluation_refuses_the_decision():
    world = World()
    world.after_describe = lambda: setattr(world, "image", b"other cartridge bytes")
    decision, reason = world.admit()
    assert decision is None and "changed" in reason


def test_missing_policy_and_missing_acquisition_cannot_admit_reported_hash():
    world = World()
    with pytest.raises(LuaError, match="acquire"):
        world.module.new(world.lua.table())
    world.read = None
    world.image = b""
    assert world.admit()[0] is None


def test_decision_proof_fields_do_not_mutate_the_binders_catalog_row():
    world = World()
    world.options.describe = world.lua.eval("function(row) return row end")
    decision = world.admit()
    assert decision.rom_sha1 == hashlib.sha1(world.image).hexdigest()
    assert world.rows[1].rom_sha1 is None
    assert world.rows[1].admitted_by is None


def test_description_cannot_override_the_approved_kind_or_actual_hash():
    world = World()
    world.options.describe = world.lua.eval("function() return {kind='unapproved',rom_sha1='claimed'} end")
    decision = world.admit()
    assert decision.kind == "clean"
    assert decision.rom_sha1 == hashlib.sha1(world.image).hexdigest()


def test_pairs_iterator_state_cannot_mutate_top_level_decision_proof():
    world = World()
    decision = world.admit()
    world.lua.eval("""function(value)
        local iterator, state = pairs(value)
        if type(state) == 'table' then state.kind='forged'; state.rom_sha1='forged' end
    end""")(decision)
    assert decision.kind == "clean"
    assert decision.rom_sha1 == hashlib.sha1(world.image).hexdigest()


def test_pairs_iterator_state_cannot_mutate_nested_decision_values():
    world = World()
    decision = world.admit()
    world.lua.eval("""function(value)
        local iterator, state = pairs(value.nested)
        if type(state) == 'table' then state.source='forged' end
    end""")(decision)
    assert decision.nested.source == "verified"


def test_ordinary_pairs_iteration_still_enumerates_top_level_and_nested_values():
    world = World()
    decision = world.admit()
    copied = world.lua.eval("""function(value)
        local result={}
        for key, item in pairs(value) do result[key]=item end
        local nested={}
        for key, item in pairs(value.nested) do nested[key]=item end
        result.nested=nested
        return result
    end""")(decision)
    assert copied.kind == "clean" and copied.title == "one"
    assert copied.rom_sha1 == hashlib.sha1(world.image).hexdigest()
    assert copied.nested.source == "verified"
