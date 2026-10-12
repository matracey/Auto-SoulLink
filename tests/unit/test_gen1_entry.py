"""lua/gen1/entry.lua's cartridge detection, and the rom_type strings it hands the server.

The title is what selects the memory profile, the engine-signal table, the write checkpoint
and the settings the HUD shows, so a false negative here is not a cosmetic failure: the
client builds against the wrong addresses or refuses to build at all (entry.lua
`Entry.build` asserts on an unknown title). Two things must hold and are pinned here:

  * `Entry.detect_title` reads ROM $0134..$0143 the way the cartridge writes it -- a
    zero-padded 16-byte field, name first -- and matches the three titles SLink ships.
  * The strings it produces are the ones `Entry.ROM_TYPE` maps to, and those values are the
    ones the server routes on: `game_id_for_rom_type` (server/adapters/__init__.py:90-95,
    table at :45-46) registers exactly "Red"/"Blue"/"Yellow" for gen1_rby. A third opinion
    about capitalisation here would be a silent mis-route, not a crash.

The real dumps are read when they are present, because a synthetic header proves the
comparison works but not that the cartridges in patch/build carry those titles.
"""
from __future__ import annotations

import hashlib
import json
import os

import pytest

from server.adapters import game_id_for_rom_type

lupa = pytest.importorskip("lupa", reason="lupa is needed to execute the Gen 1 entry module")

_REPO = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_ENTRY_PATH = os.path.join(_REPO, "lua", "gen1", "entry.lua").replace("\\", "/")
_TITLE_OFFSET = 0x134
_TITLE_BYTES = 16
_ROM_FILES = {"red": "gen1_red.gb", "blue": "gen1_blue.gb", "yellow": "gen1_yellow.gbc"}


@pytest.fixture(scope="module")
def entry():
    """The real module, under lupa, with the tuple unpacking its nil-returning path needs."""
    lua = lupa.LuaRuntime(unpack_returned_tuples=True)
    return lua.eval(f'dofile("{_ENTRY_PATH}")')


def _reader(image: bytes):
    """A `read_rom_u8(addr)` over an image addressed ABSOLUTELY, as BizHawk's ROM domain is."""
    def read_u8(addr: int) -> int:
        return image[addr] if 0 <= addr < len(image) else 0
    return read_u8


def _header_image(title: str) -> bytes:
    """A ROM image whose $0134 field holds `title`, zero-padded, like the real thing."""
    raw = title.encode("ascii")
    assert len(raw) <= _TITLE_BYTES, f"{title!r} does not fit the 16-byte title field"
    image = bytearray(_TITLE_OFFSET + _TITLE_BYTES)
    image[_TITLE_OFFSET:_TITLE_OFFSET + len(raw)] = raw
    return bytes(image)


def _dump(title: str) -> bytes:
    path = os.path.join(_REPO, "patch", "build", _ROM_FILES[title])
    if not os.path.exists(path):
        pytest.skip(f"{_ROM_FILES[title]} not present — no cartridge to read")
    with open(path, "rb") as f:
        return f.read()


@pytest.mark.parametrize("title,expected", [("POKEMON RED", "red"),
                                            ("POKEMON BLUE", "blue"),
                                            ("POKEMON YELLOW", "yellow")])
def test_the_three_titles_are_recognised(entry, title, expected):
    assert entry.detect_title(_reader(_header_image(title))) == expected


def test_an_unrelated_title_returns_nil_and_the_header_text(entry):
    """The second return value is the failure's evidence, so the caller can say WHICH ROM."""
    got = entry.detect_title(_reader(_header_image("POKEMON CRYSTA")))
    # Two Lua returns arrive as a tuple under unpack_returned_tuples; the nil is the first,
    # so the tuple is (None, name) rather than a trailing-nil list.
    assert isinstance(got, tuple) and len(got) == 2, f"expected (nil, name), got {got!r}"
    assert got[0] is None
    assert got[1] == "POKEMON CRYSTA", f"the header was not reported back: {got[1]!r}"


@pytest.mark.parametrize("title", sorted(_ROM_FILES))
def test_the_real_dumps_report_their_own_title(entry, title):
    """A synthetic header proves the comparison; this proves the dumps say what we ship."""
    rom = _dump(title)
    assert title.upper().encode() in rom[_TITLE_OFFSET:_TITLE_OFFSET + _TITLE_BYTES]
    assert entry.detect_title(_reader(rom)) == title


def test_rom_type_strings_are_the_ones_the_server_routes_on(entry):
    """The Lua table is not allowed to be a third opinion about capitalisation."""
    got = dict(entry.ROM_TYPE.items())
    assert {k: got[k] for k in ("red", "blue", "yellow")} == {"red": "Red", "blue": "Blue", "yellow": "Yellow"}
    for value in ("Red", "Blue", "Yellow"):
        assert game_id_for_rom_type(value) == "gen1_rby", (
            f"{value!r} is not the rom_type the server registers for gen1_rby")
    # PLAN §4 row 2: the pureRGB strings the server's gen1_purergb adapter routes on
    assert {k: got[k] for k in ("purered", "pureblue", "puregreen")} == {
        "purered": "PureRed", "pureblue": "PureBlue", "puregreen": "PureGreen"}
    assert dict(entry.PACKS.gen1_purergb.rom_type.items()) == {
        "purered": "PureRed", "pureblue": "PureBlue", "puregreen": "PureGreen"}


def test_a_green_header_is_a_gen1_family_for_the_launcher_route(entry):
    """PureGreen's header is POKEMON GREEN; the header only narrows, admission is by sha1."""
    assert entry.detect_title(_reader(_header_image("POKEMON GREEN"))) == "green"
    assert entry.header_title(_reader(_header_image("POKEMON GREEN"))) == "POKEMON GREEN"


@pytest.mark.parametrize("dependency", ["write_permit", "gb_checkpoint", "token_scanner",
                                       "hook_registry", "gb_hook_binding", "hello_session", "reply_dispatch"])
def test_entry_build_requires_shared_modules_in_its_real_loading_graph(dependency):
    lua = lupa.LuaRuntime(unpack_returned_tuples=True)
    module = lua.eval(f'dofile("{_ENTRY_PATH}")')
    lua.globals().missing_dependency = dependency
    lua.execute("""
        local real_dofile = dofile
        dofile = function(path)
            if path:match('/lua/' .. missing_dependency .. '.lua$') then error('shared module unavailable') end
            return real_dofile(path)
        end
    """)
    with pytest.raises(lupa.LuaError, match="shared module unavailable"):
        module.build(lua.table(root=_REPO.replace("\\", "/")))


def test_entry_binds_one_persistent_permit_for_bus_and_cart_and_io_error_disarms():
    lua = lupa.LuaRuntime(unpack_returned_tuples=True)
    module = lua.eval(f'dofile("{_ENTRY_PATH}")')
    emitted, frame, fail = [], [1], [False]

    def write(address, value, domain):
        if fail[0]:
            raise RuntimeError("device unavailable")
        emitted.append((int(address), int(value), str(domain)))

    io = lua.table(read_u8=lambda *a: 0,
                   read_range=lambda _a, n, _d=None: lua.table(*([0] * int(n))),
                   write_u8=write, framecount=lambda: frame[0], register=lambda _n: 0,
                   domains=lambda: lua.table("System Bus", "ROM", "CartRAM"),
                   on_bus_exec=lambda *a: 1, unregister=lambda _id: None)
    net = lua.table(init=lambda *a: None, connected=lambda: False, pump=lambda: None,
                    send=lambda _line: None, receive=lambda: None)
    hud = lua.table(show=lambda *a: None, prompt=lambda *a: None, set_game_over=lambda: None,
                    set_rebuilding=lambda _t: None, clear_rebuilding=lambda: None)
    _client, parts = module.build(lua.table(root=_REPO.replace("\\", "/"), io=io, net=net,
                                           hud=hud, title="red", player="a", rom_sha1="model"))
    writes = parts.writes
    writes.arm(writes, "overworld")
    writes.write_bytes(writes, parts.profile.ram.wPartyMons, lua.table(7))
    frame[0] = 19
    parts.box_io.write_cart_bytes(0x100, lua.table(8))
    assert [event[2] for event in emitted] == ["System Bus", "CartRAM"]
    assert writes.armed == "overworld" and writes.log[2].frame == 19
    fail[0] = True
    with pytest.raises((RuntimeError, lupa.LuaError), match="device unavailable"):
        parts.box_io.write_cart_bytes(0x101, lua.table(9))
    assert writes.armed is None
    assert writes.log[3].status == "error" and writes.log[3].completed == 0


def test_entry_passes_loaded_hello_and_reply_factories_to_client_without_owning_policy():
    lua = lupa.LuaRuntime(unpack_returned_tuples=True)
    module = lua.eval(f'dofile("{_ENTRY_PATH}")')
    lua.execute("""
        local original=dofile
        local loaded={}
        dofile=function(path)
            if path:match('/lua/gen1/client.lua$') then
                return {new=function(p)
                    assert(p.hello_session==loaded.hello_session, 'hello factory not injected')
                    assert(p.reply_dispatch==loaded.reply_dispatch, 'reply factory not injected')
                    assert(type(p.hello_session.new)=='function', 'hello factory missing')
                    assert(type(p.reply_dispatch.new)=='function', 'reply factory missing')
                    return {injected=true}
                end}
            end
            local value=original(path)
            if path:match('/lua/hello_session.lua$') then loaded.hello_session=value end
            if path:match('/lua/reply_dispatch.lua$') then loaded.reply_dispatch=value end
            return value
        end
    """)
    io = lua.table(read_u8=lambda *_args: 0,
                   read_range=lambda _a, n, _d=None: lua.table(*([0] * int(n))),
                   write_u8=lambda *_args: None, framecount=lambda: 0, register=lambda _n: 0,
                   on_bus_exec=lambda *_args: 1, unregister=lambda _id: None)
    client, _parts = module.build(lua.table(root=_REPO.replace("\\", "/"), io=io,
                                            net=lua.table(), hud=lua.table(), title="red", player="a"))
    assert client.injected is True


def _admission_model():
    lua = lupa.LuaRuntime(unpack_returned_tuples=True)
    entry = lua.eval(f'dofile("{_ENTRY_PATH}")')
    # Yellow, not Red: the companion is REQUIRED for Red/Blue, so Entry.admit never admits a clean Red
    # (patch-first, owner 2026-10-02) and could not exercise the shared admission mechanism with it.
    image = _header_image("POKEMON YELLOW") + b"source-model"
    digest = hashlib.sha1(image).hexdigest()
    entry.PACKS = lua.table(gen1_rby=lua.table(rom_type=lua.table(yellow="Yellow")))
    entry.PACK_FILES = lua.table(gen1_rby=lua.table(profile="data/games/gen1_rby/profile.json"))
    catalog = lua.table(titles=lua.table(yellow=lua.table(rom_sha1=digest)))
    decoder = lua.table(decode=lambda _text: catalog)
    args = lua.table(root=_REPO.replace("\\", "/"), json=decoder, rom_sha1=digest,
                     header="POKEMON YELLOW", rom_size=len(image), read_rom_u8=lambda i: image[int(i)])
    return lua, entry, args, image, digest


def test_admission_rehashes_known_reported_hash_and_refuses_forged_identity():
    _lua, entry, args, image, _digest = _admission_model()
    changed = bytes([image[0] ^ 1]) + image[1:]
    args.read_rom_u8 = lambda i: changed[int(i)]
    got = entry.admit(args)
    assert isinstance(got, tuple) and got[0] is None
    assert hashlib.sha1(changed).hexdigest() in got[1]


def test_actual_byte_admission_is_immutable_and_missing_acquisition_refuses():
    lua, entry, args, _image, digest = _admission_model()
    admitted = entry.admit(args)
    assert admitted["title"] == "yellow" and admitted["rom_sha1"] == digest
    assert admitted["rehashed"] is True and admitted["admitted_by"] == "sha1"
    mutate = lua.eval("function(value) return pcall(function() value.kind='forged' end) end")
    assert mutate(admitted)[0] is False
    args.read_rom_u8 = None
    refused = entry.admit(args)
    assert isinstance(refused, tuple) and refused[0] is None


def test_actual_artifact_change_during_admission_refuses():
    _lua, entry, args, image, _digest = _admission_model()
    reads = [0]

    def changing_byte(index):
        reads[0] += 1
        value = image[int(index)]
        return value ^ 1 if reads[0] > len(image) and index == 0 else value

    args.read_rom_u8 = changing_byte
    refused = entry.admit(args)
    assert isinstance(refused, tuple) and refused[0] is None
    assert "changed during admission" in refused[1]


def test_duplicate_known_identity_refuses_in_shared_unique_match():
    lua, entry, args, _image, digest = _admission_model()
    entry.PACKS.gen1_rby.rom_type.twin = "Twin"
    catalog = lua.table(titles=lua.table(yellow=lua.table(rom_sha1=digest), twin=lua.table(rom_sha1=digest)))
    args.json.decode = lambda _text: catalog
    refused = entry.admit(args)
    assert isinstance(refused, tuple) and refused[0] is None and "ambiguous" in refused[1]


def test_repeated_production_builds_share_hook_ownership_and_release_it_on_close():
    lua = lupa.LuaRuntime(unpack_returned_tuples=True)
    entry = lua.eval(f'dofile("{_ENTRY_PATH}")')
    rom = _dump("red")
    registrations, removals = [], []

    def read(address, domain=None):
        return rom[int(address)] if domain == "ROM" or address < 0x4000 else 0

    def register(_callback, address, name, _domain=None):
        registrations.append((int(address), str(name)))
        return len(registrations)

    io = lua.table(read_u8=read, read_range=lambda a, n, d=None: lua.table_from([read(a + i, d) for i in range(n)]),
                   write_u8=lambda *_args: None, register=lambda _name: 0,
                   domains=lambda: lua.table("ROM", "System Bus"), framecount=lambda: 0,
                   on_bus_exec=register, unregister=lambda handle: removals.append(handle))
    deps = lua.table(root=_REPO.replace("\\", "/"), title="red", player="a", io=io,
                     net=lua.table(), hud=lua.table(), rom_sha1="MODEL")
    first, _parts = entry.build(deps)
    second, _parts = entry.build(deps)
    first.start(first)
    registered = len(registrations)
    assert registered > 0 and all(name.startswith("SLink-gen1-") for _address, name in registrations)
    with pytest.raises(lupa.LuaError, match="owner namespace already active"):
        second.start(second)
    assert len(registrations) == registered
    first.signals.close(first.signals)
    assert len(removals) == registered
    second.start(second)
    assert len(registrations) == 2 * registered
    second.signals.close(second.signals)


def test_a_retry_through_a_second_build_keeps_the_failed_cleanup_authority():
    """Red control: a second Entry.build must not replace the failed service that still owns a handle."""
    lua = lupa.LuaRuntime(unpack_returned_tuples=True)
    entry = lua.eval(f'dofile("{_ENTRY_PATH}")')
    rom = _dump("red")
    state = {"calls": 0, "can_remove": False, "removed": []}

    def read(address, domain=None):
        return rom[int(address)] if domain == "ROM" or address < 0x4000 else 0

    def register(_callback, _address, _name, _domain=None):
        state["calls"] += 1
        return "00000000-0000-0000-0000-000000000000" if state["calls"] == 2 else f"owned-{state['calls']}"

    def unregister(handle):
        if not state["can_remove"]:
            return False
        state["removed"].append(str(handle))
        return True

    io = lua.table(read_u8=read, read_range=lambda a, n, d=None: lua.table_from([read(a + i, d) for i in range(n)]),
                   write_u8=lambda *_args: None, register=lambda _name: 0,
                   domains=lambda: lua.table("ROM", "System Bus"), framecount=lambda: 0,
                   on_bus_exec=register, unregister=unregister)
    deps = lua.table(root=_REPO.replace("\\", "/"), title="red", player="a", io=io,
                     net=lua.table(), hud=lua.table(), rom_sha1="MODEL")
    first, _first_parts = entry.build(deps)
    with pytest.raises(lupa.LuaError, match="cleanup failed"):
        first.start(first)
    second, parts = entry.build(deps)
    with pytest.raises(lupa.LuaError, match="outstanding failed hook cleanup"):
        second.start(second)
    failed = parts.signals.failed_service
    assert failed.status(failed).registered == 1 and state["calls"] == 2
    state["can_remove"] = True
    assert failed.close(failed) is True and state["removed"] == ["owned-1"]
    second.start(second)
    assert parts.signals.failed_service is None
    second.signals.close(second.signals)


def test_an_earlier_stamp_of_the_pure_overlay_is_admitted_as_that_overlay(entry):
    """A release stamp changes only the menu-version field, so cartridges patched before it are the same canonical build.
    Each overlay admission row lists those exact sha1s (equivalent_sha1s); the client admits them as the overlay itself,
    not via anchors as a randomized cartridge."""
    lua = lupa.LuaRuntime(unpack_returned_tuples=True)
    module = lua.eval(f'dofile("{_ENTRY_PATH}")')
    codec = lua.eval(f'dofile("{_REPO.replace(chr(92), "/")}/lua/json_codec.lua")')
    table = module.admission_table(_REPO.replace("\\", "/"), codec)
    with open(os.path.join(_REPO, "data", "games", "gen1_purergb", "admission_overlay.json"), encoding="utf-8") as stream:
        rows = json.load(stream)
    checked = 0
    for row in rows.values():
        for old in row.get("equivalent_sha1s") or []:
            hit = table[old]
            assert hit is not None, old
            assert (hit.pack, hit.title, hit.kind) == ("gen1_purergb", row["title"], "overlay"), old
            checked += 1
    assert checked >= 3, "PureRed, PureBlue and PureGreen each list the build v0.3.0 replaced"


@pytest.mark.parametrize("title", ["purered", "pureblue", "puregreen"])
@pytest.mark.parametrize("randomized", [False, True])
def test_runtime_admission_recognizes_equivalent_overlay_bytes(title, randomized):
    lua = lupa.LuaRuntime(unpack_returned_tuples=True)
    module = lua.eval(f'dofile("{_ENTRY_PATH}")')
    header = f"POKEMON {title[4:].upper()}"
    image = _header_image(header) + b"earlier-release-stamp"
    old_digest = hashlib.sha1(image).hexdigest()
    if randomized:
        image += b"changed-game-data"
    files = module.PACK_FILES.gen1_purergb
    module.PACKS = lua.table(gen1_purergb=lua.table(
        rom_type=lua.table_from({title: f"Pure{title[4:].capitalize()}"}),
    ))
    module.PACK_FILES = lua.table(gen1_purergb=lua.table(
        admission=files.admission_overlay,
        sites_overlay=files.sites_overlay,
        checkpoint_overlay=files.checkpoint_overlay,
    ))
    catalog = lua.table_from({"a" * 40: lua.table(
        title=title, kind="overlay", header_title=header,
        equivalent_sha1s=lua.table(old_digest.upper()),
    )})
    sites = lua.table(titles=lua.table_from({title: lua.table(sites=lua.table(
        witness=lua.table(rom_offset=0, expected_hex="00"),
    ))}))
    checkpoint = lua.table_from({title: lua.table(
        write_safe=lua.table(expected_hex=lua.table()),
    )})
    documents = iter([catalog, sites, checkpoint])
    args = lua.table(
        root=_REPO.replace("\\", "/"),
        json=lua.table(decode=lambda _text: next(documents)),
        rom_sha1=old_digest, header=header, rom_size=len(image),
        read_rom_u8=lambda index: image[int(index)],
    )
    admitted = module.admit(args)
    assert not isinstance(admitted, tuple), admitted
    assert (admitted.pack, admitted.title) == ("gen1_purergb", title)
    assert admitted.kind == ("rand_overlay" if randomized else "overlay")
    assert admitted.admitted_by == ("anchors" if randomized else "sha1")
    assert admitted.rom_sha1 == hashlib.sha1(image).hexdigest()
