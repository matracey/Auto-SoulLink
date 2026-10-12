#!/usr/bin/env python3
"""Build the SLink companion patch into the Radical Red ROM (C toolchain).

Pipeline (plan §3):
  1. arm-none-eabi-gcc  -> handlers.o   (Thumb, freestanding)
  2. arm-none-eabi-ld   -> handlers.elf (linked at CODE_BASE via slink.ld)
  3. verify slink_hook == CODE_BASE (nm)
  4. objcopy -O binary  -> handlers.bin
  5. copy RR -> build/slink_RR.gba ; inject handlers.bin at CODE_BASE
  6. write the 4-byte Thumb `bl slink_hook` over the CallCallbacks NOP sled
  7. verify by disassembly ; emit UPS/IPS (round-trip self-checked)

Usage: python patch/tools/build.py [--rom <Radical Red.gba>]
       python patch/tools/build.py --check     # reproducibility gate (see main)
"""
import argparse
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
PATCH = os.path.dirname(HERE)
BUILD = os.path.join(PATCH, "build")
DIST = os.path.join(PATCH, "dist")
SRC = os.path.join(PATCH, "src")
EXE = ".exe" if os.name == "nt" else ""
TARGET_NAMES = ("firered", "leafgreen", "emerald", "radical_red")


def target_spec(title):
    """Read the per-title C header shared with the payload, never another target's facts."""
    if title not in TARGET_NAMES:
        raise ValueError(f"unknown companion target: {title}")
    path = Path(SRC) / "trade_targets" / f"{title}.h"
    fields = {}
    for key, value in re.findall(r'^#define SLINK_TARGET_(\w+) (.+)$', path.read_text(), re.M):
        fields[key] = json.loads(value) if value.startswith('"') else int(value.rstrip("uU"), 0)
    return fields


def validate_detour(data, address, expected):
    offset = address - 0x08000000
    if offset < 0 or offset + len(expected) > len(data) or data[offset:offset + len(expected)] != expected:
        raise ValueError(f"detour bytes mismatch at {address:#010x}")


def validate_base(title, data):
    spec = target_spec(title)
    if (len(data) != spec["ROM_SIZE"] or hashlib.sha1(data).hexdigest() != spec["ROM_SHA1"]
            or data[0xAC:0xB0] != spec["HEADER"].encode("ascii") or data[0xBC] != 0):
        raise ValueError(f"base ROM identity mismatch for {title}")
    validate_detour(data, spec["DETOUR_CANDIDATE"], bytes.fromhex(spec["DETOUR_BYTES"]))
    return spec


def validate_arena(title, start, size, *, heap_size=None):
    """Static exclusion, NOT physical qualification. Reservation proofs are separate."""
    if size <= 0 or start < 0x02000000 or start + size > 0x02040000:
        raise ValueError("arena outside EWRAM")
    spec = target_spec(title)
    # FR/LG's linker emits gHeap with size zero in .sym. The reservation is
    # nevertheless real: pret include/malloc.h:6 and src/main.c:154. A future
    # carve-out needs explicit ROM mutation + runtime proof; zero symbol size
    # cannot silently authorize it.
    if max(start, spec["HEAP_BASE"]) < min(start + size, spec["HEAP_BASE"] + (spec["HEAP_SIZE"] if heap_size is None else heap_size)):
        raise ValueError("arena overlap with gHeap reservation (heap clamp not qualified)")
    if not spec["SYMBOLS"]:
        raise ValueError("RR has no matching source symbols; its arena needs a binary/physical proof")
    path = Path(PATCH).parent / "data/gen3/pret" / spec["SYMBOLS"]
    for line in path.read_text().splitlines():
        fields = line.split()
        if len(fields) != 4:
            continue
        address, extent = int(fields[0], 16), int(fields[2], 16)
        if fields[3] == "gHeap" and address == spec["HEAP_BASE"] and heap_size is not None:
            extent = heap_size  # runtime InitHeap detour reserves the published tail
        if extent and max(start, address) < min(start + size, address + extent):
            raise ValueError(f"arena overlap with {fields[3]} [{address:#x}, {address + extent:#x})")


def require_ready(title):
    spec = target_spec(title)
    if not spec["READY"]:
        raise ValueError(f"{title} ABI v2 not qualified: arena, detour and payload evidence required")
    if spec["ARENA_BASE"] != spec["HEAP_BASE"] + spec["HEAP_SIZE"] - spec["ARENA_SIZE"]:
        raise ValueError("published arena must match the native heap reservation")
    validate_arena(title, spec["ARENA_BASE"], spec["ARENA_SIZE"],
                   heap_size=spec["HEAP_SIZE"]-spec["ARENA_SIZE"])
    return spec


def thumb_entry_jump(address, destination):
    """Aligned eight-byte Thumb-1 entry veneer: ldr r3,[pc]; bx r3; target|1.

    Only valid when replacing the whole callee's behavior, not returning into
    overwritten PC-relative instructions. Caller LR is preserved.
    """
    if address & 3 or destination & 1:
        raise ValueError("entry/destination must be aligned")
    return bytes.fromhex("004b1847") + (destination | 1).to_bytes(4, "little")


def validate_frame_replay(spec, facts, clean):
    tails=[row for row in facts["continuations"] if row.get("symbol")=="CallCallbacks"]
    if len(tails)!=1:
        raise ValueError("callback continuation must name CallCallbacks exactly once")
    tail=tails[0]
    entry=spec["FRAME_ENTRY"]
    if (tail["symbol_address"]!=entry or tail["offset"]!=8 or tail["address"]!=entry+8
            or tail["thumb_address"]!=(tail["address"]|1) or spec["FRAME_RESUME"]!=tail["thumb_address"]):
        raise ValueError("callback continuation differs from the relocated eight-byte entry")
    expected=(f"push {{r4,lr}}\n ldr r4,=0x{spec['GMAIN']:08x}\n ldr r0,[r4]\n cmp r0,#0\n"
              f" ldr r3,=0x{spec['FRAME_RESUME']:08x}\n bx r3\n")
    if spec["FRAME_REPLAY_ASM"]!=expected:
        raise ValueError("callback replay assembly disagrees with GMAIN/FRAME_RESUME")
    instruction=int.from_bytes(clean[entry-ROM_BASE+2:entry-ROM_BASE+4],"little")
    if instruction&0xff00!=0x4c00 or ((entry+6)&~3)+4*(instruction&255)!=spec["FRAME_GMAIN_LITERAL"]:
        raise ValueError("callback PC-relative gMain load disagrees with its pinned literal")
    validate_detour(clean,tail["address"],bytes.fromhex(tail["bytes"]))
    validate_detour(clean,spec["FRAME_GMAIN_LITERAL"],spec["GMAIN"].to_bytes(4,"little"))
    return {"address":spec["FRAME_RESUME"],"gmain_literal":spec["FRAME_GMAIN_LITERAL"],
            "gmain":spec["GMAIN"],"original_tail":tail["bytes"]}


def build_arena_probe(title, rom_path, mode, *, trade_candidate=False, production=False, version=None):
    """Private ROM only; candidate advertises implemented trade but cannot publish UPS."""
    if trade_candidate and mode != "trade":
        raise ValueError("trade candidate requires trade composition")
    if title != "firered" and not (title in ("leafgreen", "emerald") and trade_candidate):
        raise ValueError("diagnostics require FireRed; private candidates support FireRed/LeafGreen/Emerald")
    clean = Path(rom_path).read_bytes()
    spec = validate_base(title, clean)
    frame_replay=None
    if spec.get("FRAME_REPLAY_REQUIRED"):
        facts=json.loads((Path(SRC)/"trade_targets"/f"{title}_lifecycle.json").read_text())
        frame_replay=validate_frame_replay(spec,facts,clean)
    if production:
        if not trade_candidate:
            raise ValueError("production requires the complete native composition")
        require_ready(title)
    carrier_bindings, sound_bindings, rival_bindings = {}, {}, {}
    if trade_candidate:
        for key in ("CARRIER_SPAWN", "CARRIER_REMOVE", "CARRIER_CHOOSE"):
            validate_detour(clean, spec[key], bytes.fromhex(spec[key+"_BYTES"]))
            carrier_bindings[key] = {"address": spec[key], "bytes": spec[key+"_BYTES"]}
        for key in ("SOUND_SE", "SOUND_FANFARE"):
            validate_detour(clean, spec[key], bytes.fromhex(spec[key+"_BYTES"]))
            sound_bindings[key] = {"address": spec[key], "bytes": spec[key+"_BYTES"]}
        for key in ("RIVAL_START", "RIVAL_DUMMY"):
            validate_detour(clean, spec[key], bytes.fromhex(spec[key+"_BYTES"]))
            rival_bindings[key] = {"address": spec[key], "bytes": spec[key+"_BYTES"]}
    validate_detour(clean, spec["HEAP_INIT"], bytes.fromhex(spec["HEAP_INIT_BYTES"]))
    if mode in ("census", "trade"):
        validate_detour(clean, spec["FRAME_ENTRY"], bytes.fromhex(spec["FRAME_BYTES"]))
    if mode == "trade":
        for key in ("TRADE_MON", "EVO_GETTER"):
            validate_detour(clean, spec[key], bytes.fromhex(spec[key+"_BYTES"]))
    out = Path(BUILD) / (f"production-{title}" if production else f"candidate-{title}-trade" if trade_candidate else f"arena-{title}-{mode}")
    out.mkdir(parents=True, exist_ok=True)
    header = Path(SRC) / "trade_targets" / f"{title}.h"
    obj, elf, binary = out / "probe.o", out / "probe.elf", out / "probe.bin"
    flag = "-DSLINK_NATIVE_TRADE_PROBE=1" if mode == "trade" else (
        f"-DSLINK_ARENA_PROBE={ {'positive':1,'negative':2,'exhaustion':3,'census':4}[mode]}")
    if trade_candidate:
        flag = "-DSLINK_NATIVE_COMPANION=1"
    entry = "slink_native_heap" if mode == "trade" else "slink_heap_probe"
    # The companion prints "SoulLink <version>" on the main menu (native_menu.h): the charmap bytes are a compile-time define
    menu = ["-D" + gen3_title.menu_define(version or gen3_title.DEFAULT_VERSION)] if trade_candidate else []
    run([GCC, *CFLAGS, flag, *menu,
         "-include", str(header), "-c", os.path.join(SRC, "handlers.c"), "-o", str(obj)])
    run([LD, "-T", str(header.with_suffix(".ld")), "-e", entry,
         "--no-warn-rwx-segments", str(obj), "-o", str(elf)])
    symbol_text = run([NM, str(elf)])
    found = [line.split()[0] for line in symbol_text.splitlines()
             if line.split()[-1:] == [entry]]
    if found != [f"{spec['CODE_CANDIDATE']:08x}"]:
        raise ValueError("probe entry is not at the verified payload candidate")
    run([OBJCOPY, "-O", "binary", str(elf), str(binary)])
    blob = bytearray(binary.read_bytes())
    panel_detours, panel_tables = [], {}
    if trade_candidate:
        symbols = {line.split()[-1]: int(line.split()[0], 16)
                   for line in symbol_text.splitlines() if len(line.split()) == 3}
        tables = [
            ("actions", spec.get("PANEL_STOCK_ACTIONS", 9)*8, "PANEL_ACTION_TABLE", "slink_panel_actions",
             (symbols["slink_panel_label"], symbols["slink_panel_menu_callback"] | 1)),
        ]
        if "PANEL_DESC_TABLE" in spec:
            tables.append(("descriptions", 36, "PANEL_DESC_TABLE", "slink_panel_descriptions",
                           (symbols["slink_panel_description"],)))
        for key, count, table_key, symbol, additions in tables:
            offset = symbols[symbol] - spec["CODE_CANDIDATE"]
            length = count + 4 * len(additions)
            if offset < 0 or offset + length > len(blob) or any(blob[offset:offset+length]):
                raise ValueError("panel table placeholder outside payload or not empty")
            original = spec[table_key] - ROM_BASE
            blob[offset:offset+count] = clean[original:original+count]
            for index, word in enumerate(additions):
                blob[offset+count+4*index:offset+count+4*index+4] = word.to_bytes(4,"little")
            panel_tables[key] = {"address": symbols[symbol], "original_address": spec[table_key]}
            refs = spec["PANEL_ACTION_REFS" if key == "actions" else "PANEL_DESC_REFS"]
            for ref in refs.split(","):
                address = int(ref, 0)
                expected = spec[table_key].to_bytes(4,"little")
                validate_detour(clean,address,expected)
                panel_detours.append({"address":address,"original":expected.hex(),
                                      "replacement":symbols[symbol].to_bytes(4,"little").hex()})
        address = spec["PANEL_NORMAL_MENU"]
        validate_detour(clean,address,bytes.fromhex(spec["PANEL_NORMAL_BYTES"]))
        panel_detours.append({"address":address,"original":spec["PANEL_NORMAL_BYTES"],
                              "replacement":thumb_entry_jump(address,symbols["slink_panel_normal_menu"]).hex()})
    offset = spec["CODE_CANDIDATE"] - ROM_BASE
    if not blob or len(blob) > 0x14000 or clean[offset:offset + len(blob)] != b"\xff" * len(blob):
        raise ValueError("probe payload candidate not free/within linker bound")
    data = bytearray(clean)
    data[offset:offset + len(blob)] = blob
    for detour in panel_detours:
        start = detour["address"] - ROM_BASE
        replacement = bytes.fromhex(detour["replacement"])
        data[start:start+len(replacement)] = replacement
    hook = spec["HEAP_INIT"] - ROM_BASE
    data[hook:hook + 8] = thumb_entry_jump(spec["HEAP_INIT"], spec["CODE_CANDIDATE"])
    frame_receipt = None
    if mode in ("census", "trade"):
        frame = next(int(line.split()[0], 16) for line in symbol_text.splitlines()
                     if line.split()[-1:] == ["slink_native_frame" if mode=="trade" else "slink_frame_probe"])
        offset = spec["FRAME_ENTRY"] - ROM_BASE
        data[offset:offset+8] = thumb_entry_jump(spec["FRAME_ENTRY"], frame)
        frame_receipt = {"address": spec["FRAME_ENTRY"], "original": spec["FRAME_BYTES"],
                         "replacement": data[offset:offset+8].hex()}
        if spec.get("FRAME_REPLAY_REQUIRED"):
            frame_receipt["continuation"] = frame_replay
    trade_detours = []
    if mode == "trade":
        for key, symbol in (("TRADE_MON","slink_native_trade_gate"), ("EVO_GETTER","slink_native_evolution_gate")):
            destination = next(int(line.split()[0],16) for line in symbol_text.splitlines()
                               if line.split()[-1:] == [symbol])
            offset = spec[key]-ROM_BASE
            data[offset:offset+8] = thumb_entry_jump(spec[key],destination)
            trade_detours.append({"address":spec[key],"original":spec[key+"_BYTES"],
                                  "replacement":data[offset:offset+8].hex(),"symbol":symbol})
    title_spans = []
    if production:
        # The SoulLink title wordmark: static graphics in the ROM's free tail past the payload's linker bound
        if gen3_title.TARGETS[title]["base"] < spec["CODE_CANDIDATE"] + 0x14000:
            raise ValueError("title assets overlap the payload's linker region")
        title_spans = gen3_title.apply_title(data, title)
    rom = out / "probe.gba"
    rom.write_bytes(data)
    receipt = {"status": "PRODUCTION_COMPANION" if production else "UNQUALIFIED_TRADE_CANDIDATE" if trade_candidate else "UNQUALIFIED_DIAGNOSTIC_ONLY",
               "target": title, "mode": mode, "ready": spec["READY"] if production else 0,
               "production": production,
               "arena_static_check": "native heap reservation" if production else "skipped: heap clamp unqualified",
               "capabilities": (87 if spec.get("CALL_FEATURE") else 23) if trade_candidate else 0,
               "panel_detours": panel_detours, "panel_tables": panel_tables,
               "carrier_bindings": carrier_bindings,
               "sound_bindings": sound_bindings,
               "rival_bindings": rival_bindings,
               "base_sha1": hashlib.sha1(clean).hexdigest(), "sha1": hashlib.sha1(data).hexdigest(),
               "payload_sha256": hashlib.sha256(blob).hexdigest(), "payload_bytes": len(blob),
               "detour": spec["HEAP_INIT"], "original": spec["HEAP_INIT_BYTES"],
               "replacement": data[hook:hook + 8].hex(), "arena_candidate": spec["ARENA_CANDIDATE"],
               "frame_detour": frame_receipt,
               "trade_detours": trade_detours,
               "title": {"spans": title_spans} if production else None,
               # the version the payload prints on the main menu (native_menu.h), from the same --version
               "menu": {"version": version or gen3_title.DEFAULT_VERSION,
                        "text": gen3_title.menu_text(version or gen3_title.DEFAULT_VERSION)} if trade_candidate else None,
               "compiler": run([GCC, "--version"])}
    if trade_candidate:
        # Version-masked identity (owner ruling 2026-10-02, patch/tools/rom_identity.py): the menu version is a FIXED-WIDTH field, so a
        # stamped build differs from this one only inside it. Record where the field is and the hashes with it zeroed.
        field = gen3_title.menu_field(version or gen3_title.DEFAULT_VERSION)
        payload_slot = rom_identity.slot_from_text(bytes(blob), field)
        rom_slot = {"offset": spec["CODE_CANDIDATE"] - ROM_BASE + payload_slot["offset"], "length": payload_slot["length"]}
        if bytes(data[rom_slot["offset"]:rom_slot["offset"] + rom_slot["length"]]) != field:
            raise ValueError("the version field is not where the payload says it is")
        receipt.update(canonical_sha1=rom_identity.canonical_sha1(bytes(data), [rom_slot]),
                       canonical_payload_sha256=rom_identity.canonical_sha256(bytes(blob), [payload_slot]),
                       version_slot=rom_slot, payload_version_slot=payload_slot)
    (out / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(f"{'PRODUCTION' if production else 'DIAGNOSTIC ONLY'}: {rom}")
    return out, receipt


PUBLISHED_TARGETS = {"firered":"FireRed", "leafgreen":"LeafGreen", "emerald":"Emerald"}


def equivalents_after_rebuild(old, new):
    """The equivalent_* lists a republished record carries: earlier exact builds that are canonical-equal to the new one (same
    canonical ROM and payload identity, so a version stamp only). A real change to the code retires them: they would vouch for
    bytes that no longer match."""
    if not old or old.get("canonical_sha1") != new["canonical_sha1"] \
            or old.get("canonical_payload_sha256") != new["canonical_payload_sha256"]:
        return {}
    out = {}
    sha1s = sorted({*old.get("equivalent_sha1s", ()), old["rom_sha1"]} - {new["rom_sha1"]})
    payloads = sorted({*old.get("equivalent_payload_sha256", ()), old["payload_sha256"]} - {new["payload_sha256"]})
    if sha1s:
        out["equivalent_sha1s"] = sha1s
    if payloads:
        out["equivalent_payload_sha256"] = payloads
    return out


def publish_native(title, rom_path, *, check=False, version=None):
    """Build, round-trip and pin one vanilla ABI2 UPS; RR keeps its ABI1 pipeline."""
    out, receipt = build_arena_probe(title, rom_path, "trade", trade_candidate=True, production=True, version=version)
    clean, patched = Path(rom_path).read_bytes(), (out/"probe.gba").read_bytes()
    patch = make_ups.ups_create(clean, patched)
    if make_ups.ups_apply(clean, patch) != patched:
        raise ValueError("production UPS round-trip differs")
    name = f"SLink-{PUBLISHED_TARGETS[title]}.ups"
    row = {"patch":name,"abi":2,"capabilities":receipt["capabilities"],"production":True,
           "base_sha1":hashlib.sha1(clean).hexdigest(),"base_md5":hashlib.md5(clean).hexdigest(),
           "rom_sha1":hashlib.sha1(patched).hexdigest(),"rom_md5":hashlib.md5(patched).hexdigest(),
           "rom_sha256":hashlib.sha256(patched).hexdigest(),"ups_sha256":hashlib.sha256(patch).hexdigest(),
           "arena_base":require_ready(title)["ARENA_BASE"],"arena_size":0x1000,
           "payload_sha256":receipt["payload_sha256"],
           "protected_spans":[{"offset":require_ready(title)["CODE_CANDIDATE"]-ROM_BASE,"size":receipt["payload_bytes"]}]
               + [{"offset":item.get("address",item.get("detour"))-ROM_BASE,"size":len(bytes.fromhex(item["original"]))}
                  for item in [receipt,receipt["frame_detour"],*receipt["trade_detours"],*receipt["panel_detours"]]]
               + [{"offset":span["offset"],"size":span["size"]} for span in receipt["title"]["spans"]],
           "menu_version":receipt["menu"]["version"],
           "canonical_sha1":receipt["canonical_sha1"],"canonical_payload_sha256":receipt["canonical_payload_sha256"],
           "version_slot":receipt["version_slot"],"payload_version_slot":receipt["payload_version_slot"]}
    manifest = Path(DIST)/"gen3_companions.json"
    data = json.loads(manifest.read_text()) if manifest.exists() else {"schema":"slink-gen3-companions-v1","titles":{}}
    old = data["titles"].get(title) or {}
    if check:
        # equivalent_* lists name earlier exact builds proven canonical-equal; they are not part of what the source builds
        if (Path(DIST)/name).read_bytes() != patch or {k: v for k, v in old.items() if not k.startswith("equivalent_")} != row:
            raise ValueError(f"{title} published UPS/manifest differs from source")
        print(f"CHECK OK: {name} and manifest reproduce")
    else:
        Path(DIST).mkdir(parents=True,exist_ok=True)
        (Path(DIST)/name).write_bytes(patch)
        data["titles"][title]={**row, **equivalents_after_rebuild(old, row)}
        manifest.write_text(json.dumps(data,indent=2,sort_keys=True)+"\n")
        print(f"PUBLISHED {name}: sha1 {row['rom_sha1']}")


PINS_NAME = "companion_pins.json"


def pins_path():
    """The committed pin file, even while --check redirects DIST to a temp dir."""
    return companion_pins.PINS


def rr_pin_entry(patched, blob, version):
    """Radical Red's row for patch/dist/companion_pins.json: it has no gen3_companions.json record. The version field is found by
    its own bytes in the payload, so the slot cannot drift from what the compiler laid down."""
    field = gen3_title.menu_field(version)
    payload_slot = rom_identity.slot_from_text(blob, field)
    slot = {"offset": CODE_BASE - ROM_BASE + payload_slot["offset"], "length": payload_slot["length"]}
    if patched[slot["offset"]:slot["offset"] + slot["length"]] != field:
        raise ValueError("the version field is not where the payload says it is")
    return {"patched_md5": hashlib.md5(patched).hexdigest(), "rom_sha1": hashlib.sha1(patched).hexdigest(),
            "canonical_sha1": rom_identity.canonical_sha1(patched, [slot]),
            "version": version, "version_slot": slot}


def read_pins():
    return companion_pins.load(pins_path())


def write_pin(slug, entry):
    """Read-modify-write one slug of patch/dist/companion_pins.json (patch/tools/companion_pins.py); every other slug, and every key
    of this one that the build does not own, is left as found. The equivalent_* lists name builds canonical-equal to the OLD
    canonical identity, so a changed one retires them (update() merges and cannot delete, so they are emptied; absent = empty)."""
    old = read_pins()["pins"].get(slug) or {}
    fields = dict(entry)
    if old.get("canonical_sha1") != entry["canonical_sha1"]:
        fields.update({key: [] for key in old if key.startswith("equivalent_")})
    elif old.get("rom_sha1") and old["rom_sha1"] != entry.get("rom_sha1"):
        # a version stamp: the replaced build is still on players' cartridges, so it stays admissible
        fields["equivalent_sha1s"] = sorted({*old.get("equivalent_sha1s", ()), old["rom_sha1"]} - {entry.get("rom_sha1")})
    companion_pins.update(slug, fields, pins_path())


def pin_problems(slug, entry):
    """Why the committed pin file's `slug` row is not what this build produces (empty = reproduces)."""
    have = read_pins()["pins"].get(slug)
    if have is None:
        return [f"{slug}: no row in {PINS_NAME}"]
    return [f"{slug}.{key}: committed {have.get(key)!r}, built {value!r}" for key, value in entry.items() if have.get(key) != value]


def _toolchain_dir():
    """Locate the arm-none-eabi bin dir: $SLINK_ARMGCC, then patch/vendor/armgcc/*/bin
    (newest first), then PATH.  Globbed rather than version-pinned so bumping the
    vendored xPack release doesn't silently break the build."""
    cands = [os.environ.get("SLINK_ARMGCC", "")]
    cands += sorted(glob.glob(os.path.join(PATCH, "vendor", "armgcc", "*", "bin")),
                    reverse=True)
    for d in cands:
        if d and os.path.exists(os.path.join(d, "arm-none-eabi-gcc" + EXE)):
            return d
    return "" if shutil.which("arm-none-eabi-gcc") else None


GCCDIR = _toolchain_dir()


def _tool(name):
    return os.path.join(GCCDIR, name + EXE) if GCCDIR else name


GCC = _tool("arm-none-eabi-gcc")
LD = _tool("arm-none-eabi-ld")
OBJCOPY = _tool("arm-none-eabi-objcopy")
NM = _tool("arm-none-eabi-nm")

ROM_BASE = 0x08000000
# 0x08378F70, not 0x08378CA8: the bundled RR4.1_Custom Battle Calc occupies
# 0x08378CA8..0x08378F6F. SLink injects into the 0xFF run that resumes at 0x08378F70
# (~0x14638 free; keep in sync with ORIGIN in slink.ld).
CODE_BASE = 0x08378F70
HOOK_SITE = 0x0800051A
# CFRU BackupParty's two party->backup-buffer memcpy BL sites. We redirect them to
# slink_backup_wrap so the patch learns the EXACT frame a borrowed-party swap begins
# (the authoritative "Party Freeze" signal). Discovered live via
# lua/tests/probe_party_backup_writer.lua (writers of REAL_PARTY_BACKUP 0x02025564).
BACKUP_BL_SITES = (0x0804C10C, 0x0804C212)
BACKUP_MEMCPY = 0x081E5E78  # the engine memcpy the sites originally `bl`'d (sanity-checked pre-redirect)
# The Battle Calc detours BattlePutTextOnWindow's 2nd instruction (0x080D87BE) to this trampoline.
# We re-point that detour to our in-context shim, which falls through to this trampoline. See [6/7].
BATTLE_CALC_TRAMPOLINE = 0x08378CA8
# §6 SOULLINK start-menu entry. RR reads the menu's description and action arrays through one base
# literal with hardcoded offsets, and the two ranges abut (desc[13] IS act[0].text), so a 14th
# action id cannot own a description. We take over id 8 instead — a second PLAYER row only
# SetUpStartMenu_Link ever appends, proven absent from a real field menu by
# lua/tests/test_live_startmenu.lua. Five word writes, each verified against its expected current value
# so a different RR build fails the build instead of producing a subtly wrong ROM.
STARTMENU_TABLE = 0x09148FB4
STARTMENU_DESC8 = STARTMENU_TABLE + 8 + 4 * 8       # 0x09148FDC — desc[8]
STARTMENU_ACT8 = STARTMENU_TABLE + 0x3C + 8 * 8     # 0x09149030 — act[8] = {text, func}
STARTMENU_SETUP_LIT = 0x0806ED58                    # CFRU's SetUpStartMenu redirect literal
STARTMENU_REDRAW_LIT = 0x090BDD54                   # the page switch's rebuild callback literal
STARTMENU_EXPECT = {
    STARTMENU_DESC8: 0x0841A049,       # duplicate of desc[3] (PLAYER)
    STARTMENU_ACT8: 0x0841628E,        # duplicate of act[3].text (PLAYER)
    STARTMENU_ACT8 + 4: 0x0806F56D,    # id 8's own action func (NOT act[3].func)
    STARTMENU_SETUP_LIT: 0x090BE179,   # the original SetUpStartMenu
    STARTMENU_REDRAW_LIT: 0x090BE30D,  # RR's rebuild (direct `bl SetUpStartMenu`, bypasses the above)
}

RR_MD5 = "8529f3a45d32bce4da637976fcf269d4"
DEFAULT_RR = r"E:/Google Drive/SLink/Pokemon - Radical Red.gba"
# Committed base-RR -> RR4.1_Custom Battle Calc delta (the in-battle damage / type-
# effectiveness calculator), folded in before SLink injection. Regenerate with
# tools/make_battle_calc_patch.py. UPS because the Battle Calc code is > 16 MB.
BATTLE_CALC_UPS = os.path.join(SRC, "rr41_battle_calc.ups")

sys.path.insert(0, HERE)
import companion_pins  # noqa: E402
import gen3_title  # noqa: E402
import make_ups  # noqa: E402
import rom_identity  # noqa: E402

CFLAGS = ["-mthumb", "-mcpu=arm7tdmi", "-mtune=arm7tdmi", "-Os", "-ffreestanding",
          "-fno-builtin", "-fomit-frame-pointer", "-fno-toplevel-reorder",
          "-fno-jump-tables",  # avoid libgcc __gnu_thumb1_case_* switch helpers
          "-mno-unaligned-access", "-Wall", "-std=c11"]


def md5(p):
    with open(p, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(" ".join(cmd))
        print(r.stdout)
        print(r.stderr)
        sys.exit("command failed")
    return r.stdout


def thumb_bl(src, dst):
    """ARMv4T Thumb BL (two halfwords, 11+11 offset bits, +-4 MB)."""
    off = dst - (src + 4)
    if not -0x400000 <= off < 0x400000:
        sys.exit(f"BL out of range: {hex(src)}->{hex(dst)} ({off:#x})")
    hi = 0xF000 | ((off >> 12) & 0x7FF)
    lo = 0xF800 | ((off >> 1) & 0x7FF)
    return bytes([hi & 0xFF, (hi >> 8) & 0xFF, lo & 0xFF, (lo >> 8) & 0xFF])


def main():
    global BUILD, DIST
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", choices=TARGET_NAMES, default="radical_red")
    ap.add_argument("--abi-version", type=int, choices=(1, 2), default=1)
    ap.add_argument("--describe", action="store_true", help="print target candidates; does not build/admit")
    ap.add_argument("--trade-candidate", action="store_true",
                    help="private FR/LG ABI v2 candidate ROM; READY/UPS publication unchanged")
    ap.add_argument("--arena-probe", choices=("positive", "negative", "exhaustion", "census", "trade"),
                    help="private unqualified heap-reservation diagnostic; never publishes a patch")
    ap.add_argument("--rom", default=DEFAULT_RR)
    ap.add_argument("--version", default=None,
                    help="SoulLink version printed on the main menu: 'dev' (default) or vX.Y.Z[-dev]; a release re-stamps it")
    ap.add_argument("--no-verify-md5", action="store_true")
    ap.add_argument("--no-battle-calc", action="store_true",
                    help="skip folding in the RR4.1_Custom Battle Calc delta "
                         "(emit base-RR + SLink only)")
    ap.add_argument("--check", action="store_true",
                    help="reproducibility gate: build into a temp dir and assert the "
                         "emitted UPS is byte-identical to the committed dist/SLink-RR.ups. "
                         "Touches nothing in the tree.")
    args = ap.parse_args()
    if args.version is not None:
        try:
            gen3_title.check_version(args.version)
        except ValueError as error:
            ap.error(str(error))
    if args.trade_candidate:
        if args.arena_probe or args.check or args.describe or args.no_verify_md5:
            ap.error("trade candidate cannot combine with probes/check/describe/verification bypass")
        try:
            build_arena_probe(args.target, args.rom, "trade", trade_candidate=True, version=args.version)
        except (ValueError, OSError) as error:
            ap.error(str(error))
        return 0
    if args.describe:
        print(json.dumps(target_spec(args.target), indent=2))
        return 0
    if args.arena_probe:
        try:
            build_arena_probe(args.target, args.rom, args.arena_probe)
        except (ValueError, OSError) as error:
            ap.error(str(error))
        return 0
    if args.target in PUBLISHED_TARGETS:
        if args.no_verify_md5:
            ap.error("base verification bypass is not supported for pinned companion targets")
        try:
            publish_native(args.target,args.rom,check=args.check,version=args.version)
        except (ValueError,OSError) as error:
            ap.error(str(error))
        return 0
    if args.abi_version == 2:
        try:
            require_ready(args.target)
        except ValueError as error:
            ap.error(str(error))
        # This early T2 cut intentionally cannot compile RR addresses into another title.
        ap.error("ABI v2 target producer not implemented")
    if args.no_verify_md5:
        ap.error("base verification bypass is not supported for pinned companion targets")
    try:
        validate_base(args.target, Path(args.rom).read_bytes())
    except (ValueError, OSError) as error:
        ap.error(str(error))
    committed_ups = os.path.join(DIST, "SLink-RR.ups")
    tmp = None
    if args.check:
        parent = Path(PATCH) / "build"
        if parent.resolve() != Path(PATCH).resolve() / "build":
            ap.error("redirected build directory refused")
        parent.mkdir(parents=True, exist_ok=True)
        tmp = tempfile.mkdtemp(prefix="slink-build-", dir=parent)
    if args.check:
        if not os.path.exists(committed_ups):
            sys.exit(f"--check: no committed patch at {committed_ups}")
        BUILD = DIST = tmp
    os.makedirs(BUILD, exist_ok=True)
    os.makedirs(DIST, exist_ok=True)
    if GCCDIR is None:
        sys.exit("toolchain missing: no arm-none-eabi-gcc in $SLINK_ARMGCC, "
                 "patch/vendor/armgcc/*/bin, or PATH")

    obj = os.path.join(BUILD, "handlers.o")
    elf = os.path.join(BUILD, "handlers.elf")
    binf = os.path.join(BUILD, "handlers.bin")
    print("[1/7] compile")
    # "SoulLink <version>" on the main menu (native_menu.h): the charmap bytes are a compile-time define
    run([GCC, *CFLAGS, "-D" + gen3_title.menu_define(args.version or gen3_title.DEFAULT_VERSION),
         "-c", os.path.join(SRC, "handlers.c"), "-o", obj])
    print(f"[2/7] link @ {CODE_BASE:#x}")
    run([LD, "-T", os.path.join(SRC, "slink.ld"), "-e", "slink_hook",
         "--no-warn-rwx-segments", obj, "-o", elf])
    print("[3/7] verify slink_hook address")
    # Every symbol the ROM-side rewrites need to point at. Missing one is fatal: it would mean a
    # detour or table word silently keeping its old target.
    WANTED = ("slink_hook", "slink_battletext_hook", "slink_backup_wrap",
              "slink_startmenu_cb", "slink_setup_start_menu", "slink_start_menu_redraw",
               "sSoulLinkLabel", "sSoulLinkDesc")
    sym = {}
    for line in run([NM, elf]).splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[2] in WANTED:
            sym[parts[2]] = int(parts[0], 16)
    missing = [s for s in WANTED if s not in sym]
    if missing:
        sys.exit(f"symbols not found in handlers.elf: {', '.join(missing)}")
    if sym["slink_hook"] != CODE_BASE:
        sys.exit(f"slink_hook at {sym['slink_hook']:#x}, expected {CODE_BASE:#x}")
    bt_hook_addr = sym["slink_battletext_hook"]
    backup_wrap_addr = sym["slink_backup_wrap"]
    print(f"      slink_hook @ {sym['slink_hook']:#010x} OK")
    for s in WANTED[1:]:
        print(f"      {s} @ {sym[s]:#010x}")
    print("[4/7] objcopy -> bin")
    run([OBJCOPY, "-O", "binary", elf, binf])
    with open(binf, "rb") as f:
        blob = f.read()
    print(f"      handlers.bin = {len(blob)} bytes")
    MAX_CODE_SIZE = 0x14000  # slink.ld MEMORY rom LENGTH — keep the two in sync
    if len(blob) > MAX_CODE_SIZE:
        sys.exit(f"handlers.bin {len(blob)} B exceeds slink.ld region LENGTH {MAX_CODE_SIZE:#x}")

    print("[5/7] inject into ROM")
    src_md5 = md5(args.rom)
    if not args.no_verify_md5 and src_md5 != RR_MD5:
        sys.exit(f"RR md5 mismatch (expected {RR_MD5}, got {src_md5})")
    out_rom = os.path.join(BUILD, "slink_RR.gba")
    with open(args.rom, "rb") as f:
        clean = f.read()  # the user's apply target (base RR), kept un-calc'd
    if args.no_battle_calc:
        data = bytearray(clean)
        print("      Battle Calc SKIPPED (--no-battle-calc) -> base RR + SLink only")
    else:
        # Fold the RR4.1_Custom Battle Calc onto base RR first. ups_apply CRC-gates
        # source == base RR and target == RR4.1_Custom, so a wrong base ROM fails loudly.
        with open(BATTLE_CALC_UPS, "rb") as f:
            battle_calc = f.read()
        data = bytearray(make_ups.ups_apply(clean, battle_calc))
        print(f"      applied Battle Calc {os.path.relpath(BATTLE_CALC_UPS, PATCH)} "
              f"({len(battle_calc)} B) -> RR4.1_Custom")
    code_off = CODE_BASE - ROM_BASE
    region = data[code_off:code_off + len(blob)]
    if any(b != 0xFF for b in region):
        bad = code_off + next(i for i, b in enumerate(region) if b != 0xFF)
        sys.exit(f"CODE_BASE {CODE_BASE:#010x} not free for {len(blob)} B "
                 f"(non-0xFF at ROM {ROM_BASE + bad:#010x}) — would clobber ROM/Battle-Calc data")
    data[code_off:code_off + len(blob)] = blob
    print("[6/7] write hook BL")
    bl = thumb_bl(HOOK_SITE, CODE_BASE)
    hook_off = HOOK_SITE - ROM_BASE
    data[hook_off:hook_off + 4] = bl
    # Redirect CFRU BackupParty's memcpy BL sites -> slink_backup_wrap (Party Freeze begin signal).
    # Each site must currently be `BL BACKUP_MEMCPY`; bail loudly if the CFRU layout moved.
    for site in BACKUP_BL_SITES:
        s_off = site - ROM_BASE
        if bytes(data[s_off:s_off + 4]) != thumb_bl(site, BACKUP_MEMCPY):
            sys.exit(f"backup BL site {site:#x} is not `BL {BACKUP_MEMCPY:#x}` — CFRU layout changed; re-RE")
        data[s_off:s_off + 4] = thumb_bl(site, backup_wrap_addr)
    print(f"      redirected backup BL sites {', '.join(hex(s) for s in BACKUP_BL_SITES)} "
          f"-> slink_backup_wrap {backup_wrap_addr:#x}")
    # §6: take over start-menu action id 8 -> SOULLINK. Verify-then-write, same discipline as the
    # BL redirects above: if any word is not what we RE'd, the RR/CFRU layout moved and guessing
    # would corrupt a menu the player uses every session.
    def w32(addr, value):
        o = addr - ROM_BASE
        data[o:o + 4] = value.to_bytes(4, "little")

    for addr, expect in STARTMENU_EXPECT.items():
        got = int.from_bytes(bytes(data[addr - ROM_BASE:addr - ROM_BASE + 4]), "little")
        if got != expect:
            sys.exit(f"start-menu word {addr:#x} is {got:#010x}, expected {expect:#010x} — "
                     "RR/CFRU start-menu layout changed; re-run lua/tests/test_live_startmenu.lua and re-RE")
    w32(STARTMENU_DESC8, sym["sSoulLinkDesc"])
    w32(STARTMENU_ACT8, sym["sSoulLinkLabel"])
    w32(STARTMENU_ACT8 + 4, sym["slink_startmenu_cb"] | 1)          # Thumb bit
    w32(STARTMENU_SETUP_LIT, sym["slink_setup_start_menu"] | 1)
    w32(STARTMENU_REDRAW_LIT, sym["slink_start_menu_redraw"] | 1)
    print(f"      start-menu id 8 -> SOULLINK (label {sym['sSoulLinkLabel']:#x}, "
          f"cb {sym['slink_startmenu_cb'] | 1:#x}, setup wrapper "
          f"{sym['slink_setup_start_menu'] | 1:#x})")
    # Re-point the Battle Calc's BattlePutTextOnWindow detour (0x080D87BE: `BL 0x08378CA8`) to our in-context
    # shim, which swaps the text ptr when a notification is active then falls through to the calc trampoline.
    # Only meaningful when the Battle Calc is present (it installs that detour); skip if --no-battle-calc.
    if not args.no_battle_calc:
        BT_DETOUR = 0x080D87BE
        bt_off = BT_DETOUR - ROM_BASE
        if bytes(data[bt_off:bt_off + 4]) != bytes(thumb_bl(BT_DETOUR, BATTLE_CALC_TRAMPOLINE)):
            sys.exit(f"Battle Calc detour @ {BT_DETOUR:#x} not the expected `BL {BATTLE_CALC_TRAMPOLINE:#x}` "
                     "— Battle Calc layout changed; re-RE before re-pointing")
        data[bt_off:bt_off + 4] = thumb_bl(BT_DETOUR, bt_hook_addr)
        print(f"      re-pointed BattlePutTextOnWindow detour @ {BT_DETOUR:#x} -> shim {bt_hook_addr:#x}")
    # The SoulLink title wordmark, in the 1.6 MB 0xFF run at 0x08B71D04 (no payload or Battle Calc byte lives there)
    title_spans = gen3_title.apply_title(data, "radical_red")
    print(f"      title wordmark: {len(title_spans)} spans; main menu line {gen3_title.menu_text(args.version or gen3_title.DEFAULT_VERSION)!r}")
    with open(out_rom, "wb") as f:
        f.write(data)

    _verify(out_rom)
    print("[7/7] patches")
    patched = bytes(data)
    # --no-battle-calc is a different ROM from the published companion, so it neither writes nor checks the pin row
    pin = None if args.no_battle_calc else rr_pin_entry(patched, blob, args.version or gen3_title.DEFAULT_VERSION)
    ups = make_ups.ups_create(clean, patched)
    assert hashlib.md5(make_ups.ups_apply(clean, ups)).hexdigest() == hashlib.md5(patched).hexdigest()
    with open(os.path.join(DIST, "SLink-RR.ups"), "wb") as f:
        f.write(ups)
    print(f"      SLink-RR.ups ({len(ups)} B) round-trip OK")
    if pin and not args.check:
        write_pin("rr", pin)
        print(f"      {PINS_NAME}: rr md5 {pin['patched_md5']}, canonical sha1 {pin['canonical_sha1']}")
    if args.check:
        with open(committed_ups, "rb") as f:
            want = f.read()
        resolved = Path(tmp).resolve()
        if resolved.parent != Path(PATCH).resolve() / "build" or not resolved.name.startswith("slink-build-"):
            sys.exit(f"refusing cleanup outside owned build directory: {resolved}")
        shutil.rmtree(resolved)
        if ups != want:
            sys.exit(f"CHECK FAIL: rebuilt UPS ({len(ups)} B, md5 "
                     f"{hashlib.md5(ups).hexdigest()}) != committed "
                     f"({len(want)} B, md5 {hashlib.md5(want).hexdigest()}) — "
                     "handlers.c and dist/SLink-RR.ups are out of sync; rebuild and commit")
        problems = pin_problems("rr", pin) if pin else []
        if problems:
            sys.exit("CHECK FAIL: " + PINS_NAME + " differs from the rebuilt companion: " + "; ".join(problems))
        print(f"\nCHECK OK. dist/SLink-RR.ups reproduces from source "
              f"(patched md5 {hashlib.md5(patched).hexdigest()})")
        return 0
    ips_path = os.path.join(DIST, "SLink-RR.ips")
    try:
        ips = make_ups.ips_create(clean, patched)
        assert hashlib.md5(make_ups.ips_apply(clean, ips)).hexdigest() == hashlib.md5(patched).hexdigest()
        with open(ips_path, "wb") as f:
            f.write(ips)
        print(f"      SLink-RR.ips ({len(ips)} B) round-trip OK")
    except ValueError as e:
        # The bundled RR4.1_Custom Battle Calc code lives above 16 MB, which IPS's 24-bit
        # offsets cannot reach. Drop any stale IPS so dist/ never ships an inconsistent one.
        if os.path.exists(ips_path):
            os.remove(ips_path)
            print(f"      IPS removed (stale): {e}")
        else:
            print(f"      IPS skipped: {e}")
    print(f"\nDONE. patched md5 {md5(out_rom)}")
    return 0


def _disasm(rom, addr, count):
    return subprocess.run([sys.executable, os.path.join(HERE, "disasm.py"),
                           rom, hex(addr), str(count)],
                          capture_output=True, text=True).stdout


def _verify(rom):
    hook = _disasm(rom, HOOK_SITE, 3)
    print("   hook site:")
    for ln in hook.splitlines()[1:]:
        print("     " + ln.strip())
    if "bl" not in hook or f"{CODE_BASE:x}" not in hook:
        sys.exit("VERIFY FAIL: hook not a BL to CODE_BASE")
    code = _disasm(rom, CODE_BASE, 4)
    print("   code region:")
    for ln in code.splitlines()[1:]:
        print("     " + ln.strip())
    print("   injection verified OK")


if __name__ == "__main__":
    sys.exit(main())
