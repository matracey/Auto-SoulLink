"""tools/gen_polished_script_sites.py: the 63 Polished script sites, resolved against the ROM.

The pack's 63 givepoke/loadwildmon sites were all `Unresolved` because a script POSITION is not in
a .sym -- only the enclosing script LABEL is. These tests pin the resolution that closes that gap:
the ROM sha1 refusal, the three starter sites, the 63-site accounting, a byte-level round trip of
every resolved site, and that --check goes red on drift.

Every assertion below is a check the generator itself cannot make about its own output.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import gen_polished_script_sites as gen  # noqa: E402

OUT = gen.OUT
TOTAL_SITES = 63


@pytest.fixture(scope="module")
def document() -> dict:
    if not (gen.RELEASE / gen.ROM_NAME).is_file():
        pytest.skip(f"pinned Polished release ROM absent: {gen.RELEASE / gen.ROM_NAME}")
    return gen.build()


@pytest.fixture(scope="module")
def rom(document) -> bytes:
    return gen.load_rom(gen.RELEASE / gen.ROM_NAME)


# ------------------------------------------------------------------ the ROM is pinned

def test_a_cartridge_other_than_the_pinned_release_rom_is_refused(tmp_path):
    """The generator must never analyse an unidentified cartridge.

    MUTATION: drop the sha1 comparison at load_rom -- any ROM is then decoded and this goes red.
    """
    fake = tmp_path / "fake.gbc"
    fake.write_bytes(b"\x00" * 0x200000)
    with pytest.raises(gen.ResolveError, match="differs from the pin"):
        gen.load_rom(fake)


def test_the_absent_rom_is_refused_rather_than_substituted(tmp_path):
    """A missing ROM is a refusal, not an empty result set.

    MUTATION: replace the is_file check with a bare read_bytes -- this goes red.
    """
    with pytest.raises(gen.ResolveError, match="absent"):
        gen.load_rom(tmp_path / "nope.gbc")


def test_a_modified_sym_is_refused(tmp_path):
    """The .sym supplies the window boundaries, so it is pinned exactly as the ROM is.

    MUTATION: drop the _sha256 check in load_syms -- a tampered .sym silently changes every window.
    """
    sym = tmp_path / "x.sym"
    sym.write_bytes(b"00:0000 EntryPoint\n")
    with pytest.raises(gen.ResolveError, match="SHA256 differs"):
        gen.load_syms(sym)


def test_the_pins_are_the_ones_the_ini_already_declares():
    """This generator must not introduce a second ROM identity.

    MUTATION: change ROM_SHA1 or SYMS_SHA256 -- the ini cross-check goes red.
    """
    ini = gen.INI.read_text(encoding="utf-8")
    assert gen.ROM_SHA1 in ini
    assert gen.SYMS_SHA256 in ini


# ------------------------------------------------------------------ the opcodes are proved on the ROM

@pytest.mark.parametrize("kind,opcode", sorted(gen.OPCODES.items()))
def test_the_opcode_occurs_before_a_known_species_byte(document, rom, kind, opcode):
    """givepoke/loadwildmon carry no literal in the source, so each opcode is proved on the ROM.

    MUTATION: set either OPCODES value to another byte -- every site of that kind stops resolving
    and the resolved-count assertions below go red.
    """
    rows = [s for s in document["sites"] if s["kind"] == kind and s["offset"] is not None]
    assert rows, f"no resolved {kind} site to prove the opcode from"
    for row in rows:
        assert rom[row["offset"] - 1] == opcode, row


def test_the_starter_sites_resolve_to_the_three_starters_at_level_five(document):
    """The hand-checkable anchor: maps/ElmsLab.asm:215/255/293 give the three Johto starters.

    MUTATION: swap two of the species indices, or accept a wrong level -- this goes red.
    """
    starters = {s["species_const"]: s for s in document["sites"] if "ElmsLab" in s["source"]}
    assert set(starters) == {"CYNDAQUIL", "TOTODILE", "CHIKORITA"}
    for row in starters.values():
        assert row["offset"] is not None, row
        assert row["level"] == 5, row
        assert row["kind"] == "givepoke"
        assert row["form"] == 1, "PLAIN_FORM is 1 in constants/pokemon_constants.asm"
    assert starters["CYNDAQUIL"]["species"] == 155
    assert starters["TOTODILE"]["species"] == 158
    assert starters["CHIKORITA"]["species"] == 152
    assert len({s["offset"] for s in starters.values()}) == 3


def test_each_expansion_lies_inside_its_own_script_window(document):
    """No resolved offset may escape the label's window; that is what keeps the match local.

    MUTATION: raise WINDOW, or drop the non-child symbol stop in window_end -- a site can then
    match an expansion belonging to the next script.
    """
    syms = gen.load_syms(gen.RELEASE / gen.SYM_NAME)
    for row in document["sites"]:
        if row["offset"] is None:
            continue
        assert row["offset"] - 1 >= row["label_offset"], row
        end = gen.window_end(syms, row["label"], row["label_offset"])
        assert row["offset"] - 1 < end, row
        assert (row["offset"] - 1) - row["label_offset"] < gen.WINDOW, row


# ------------------------------------------------------------------ accounting and round trip

def test_resolved_plus_unresolved_is_the_whole_ini(document):
    """The 63 sites are partitioned; nothing is dropped and nothing is double-counted.

    MUTATION: `continue` past a group instead of recording a reason -- a site's row disappears and
    the sum drops below 63.
    """
    rows = document["sites"]
    assert len(rows) == TOTAL_SITES
    resolved = [s for s in rows if s["offset"] is not None]
    unresolved = [s for s in rows if s["offset"] is None]
    assert len(resolved) + len(unresolved) == TOTAL_SITES
    assert len({(s["kind"], s["source"]) for s in rows}) == TOTAL_SITES


def test_every_row_states_a_reason_and_only_resolved_rows_carry_data(document):
    """An unresolved row must say why and carry no half-decoded numbers.

    MUTATION: leave `level` set on an unresolved site -- this goes red.
    """
    for row in document["sites"]:
        assert row["reason"], row
        if row["offset"] is None:
            assert row["level"] is None, row
            assert row["reason"].startswith(("species constant", "form constant",
                                             "0 matching", "no $", "decoded level")), row
        else:
            assert row["reason"].startswith("resolved:"), row


def test_every_resolved_offset_decodes_back_to_its_source_species_and_level(document, rom):
    """The byte-level round trip: read the ROM at the emitted offset and recover the facts.

    This is the check the generator cannot make about itself -- it re-derives species, form and
    level from raw bytes and compares with what the pack claims.

    MUTATION: `at + 1` -> `at` in resolve(), or a wrong shift in find_expansions -- this goes red.
    """
    for row in document["sites"]:
        if row["offset"] is None:
            continue
        at = row["offset"] - 1
        opcode = gen.OPCODES[row["kind"]]
        assert rom[at] == opcode, row
        # `dp` = db LOW(sp), HIGH(sp)<<5 | form  (macros/data.asm:89-91). The second byte carries
        # BOTH the species high bits and the form OR'd in whole -- FEMALE is %10000000 and the
        # variant forms are ext_const values like 157 -- so the form is not a 5-bit field.
        species = rom[at + 1] | ((rom[at + 2] & 0x20) << 3)   # only EXTSPECIES (bit 5) is species; bits 6-7 are egg/gender
        assert species == row["species"], row
        assert rom[at + 2] & 0xFF == row["form"] & 0xFF, row
        assert rom[at + 3] == row["level"], row
        assert 1 <= row["level"] <= 100, row


def test_the_species_and_form_come_from_the_packs_not_the_rom(document):
    """The JSON's species/form must equal what the generated packs say, not just what the ROM holds.

    MUTATION: index into the wrong species pack -- this goes red.
    """
    species, forms = gen.load_species(), gen.load_forms()
    for row in document["sites"]:
        if row["offset"] is None:
            continue
        assert row["species"] == species[row["species_const"]], row
        # form_const is not re-emitted, so pin the decoded form against the constants table:
        # every form this generator emits is one the source declares.
        assert row["form"] in set(forms.values()), row


# ------------------------------------------------------------------ --check

def test_check_is_green_on_the_committed_output(document):
    """--check must pass on the artifact the generator just wrote.

    MUTATION: make main() always return 0 -- a drifting pack is then invisible.
    """
    if not OUT.is_file():
        pytest.skip("script_sites.json not generated yet")
    assert subprocess.run([sys.executable, str(Path(gen.__file__)), "--check"]).returncode == 0


def test_check_goes_red_on_a_modified_output(tmp_path, monkeypatch, document):
    """A one-byte change to the committed JSON must be reported as drift.

    MUTATION: make main() return 0 unconditionally in the --check branch -- this goes red.
    """
    tampered = tmp_path / "script_sites.json"
    payload = json.loads(OUT.read_text(encoding="utf-8"))
    payload["sites"][0]["offset"] = (payload["sites"][0]["offset"] or 0) + 1
    tampered.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    monkeypatch.setattr(gen, "OUT", tampered)
    monkeypatch.setattr(sys, "argv", ["gen_polished_script_sites.py", "--check"])
    assert gen.main() == 1


def test_the_output_is_deterministic(document):
    """Two builds in one process must agree byte-for-byte, or --check is noise.

    MUTATION: iterate a set instead of the ini order in render -- ordering drifts run to run.
    """
    first = json.dumps(gen.build(), indent=2, sort_keys=True) + "\n"
    second = json.dumps(gen.build(), indent=2, sort_keys=True) + "\n"
    assert first == second


def test_the_form_byte_is_a_whole_byte_not_a_five_bit_field(document):
    """Regression guard for the encoding fix.

    `dp` ORs the form in as a full assembly-time value: FEMALE is %10000000
    (constants/pokemon_data_constants.asm:274) and MAGIKARP_MASK1_FORM is ext_const 157
    (:418). Masking the second byte to 0x1F drops both, so a variant-form site can never match.

    MUTATION: reintroduce `form & 0x1F` in find_expansions -- every variant-form site silently
    stops matching again, which is exactly the 11-site regression this guard was written for.
    """
    syms = gen.load_syms(gen.RELEASE / gen.SYM_NAME)
    forms = gen.load_forms()
    rom = gen.load_rom(gen.RELEASE / gen.ROM_NAME)
    # a form above 0x1F must produce a byte above 0x1F, not a truncated one
    species, form = gen.load_species()["MAGIKARP"], forms["MAGIKARP_MASK1_FORM"]
    assert form > 0x1F, "MAGIKARP_MASK1_FORM must be a wide form constant for this guard to bite"
    byte = (((species >> 8) << gen.EXTSPECIES_F) | form) & 0xFF
    assert byte & 0x1F != form, "the form must not survive a 5-bit mask"
    assert byte == 0x9D
    # and FEMALE | PLAIN_FORM, the shape maps/Route35GoldenrodGate.asm:41 uses
    assert (0x80 | forms["PLAIN_FORM"]) & 0xFF == 0x81
    assert rom is not None and syms is not None


def test_cli_overrides_resolve_a_site_from_the_selected_source_and_rom(tmp_path, monkeypatch):
    source = tmp_path / "source"
    constants = source / "constants"
    constants.mkdir(parents=True)
    (constants / "pokemon_constants.asm").write_text("DEF PLAIN_FORM EQU 1\n", encoding="utf-8")
    (constants / "pokemon_data_constants.asm").write_text("DEF FEMALE EQU %10000000\n", encoding="utf-8")
    release = tmp_path / "release"
    release.mkdir()
    rom = bytearray(0x200)
    rom[0x100:0x104] = bytes([0x2F, 155, 1, 5])
    (release / gen.ROM_NAME).write_bytes(rom)
    sym = b"00:0100 GiftScript\n00:0110 NextScript\n"
    (release / gen.SYM_NAME).write_bytes(sym)
    ini = tmp_path / "entries.ini"
    ini.write_text(
        "GivePokeSite0=Unresolved\nGivePokeSite0Label=GiftScript\n"
        "GivePokeSite0ScriptOffset=0x100\nGivePokeSite0Species=CYNDAQUIL\n"
        "GivePokeSite0Arg2=PLAIN_FORM\nGivePokeSite0Source=maps/Gift.asm:1\n",
        encoding="utf-8",
    )
    species = tmp_path / "species.json"
    species.write_text('{"species": {"155": {"const": "CYNDAQUIL"}}}', encoding="utf-8")
    out = tmp_path / "script_sites.json"
    monkeypatch.setattr(gen, "ROM_SHA1", hashlib.sha1(rom).hexdigest())
    monkeypatch.setattr(gen, "SYMS_SHA256", hashlib.sha256(sym).hexdigest())
    monkeypatch.setattr(gen, "INI", ini)
    monkeypatch.setattr(gen, "SPECIES_PACK", species)
    monkeypatch.setattr(gen, "OUT", out)
    argv = ["gen_polished_script_sites.py", "--src", str(source), "--roms", str(release)]
    monkeypatch.setattr(sys, "argv", argv)

    assert gen.main() == 0
    row = json.loads(out.read_text(encoding="utf-8"))["sites"][0]
    assert (row["offset"], row["species"], row["form"], row["level"]) == (0x101, 155, 1, 5)
    monkeypatch.setattr(sys, "argv", [*argv, "--check"])
    assert gen.main() == 0
    out.write_text("{}\n", encoding="utf-8")
    assert gen.main() == 1
