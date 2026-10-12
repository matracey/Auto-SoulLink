"""The calc PAGE (calc/src/normal.template.html, hardcore.template.html) does not load
production.min.js. It loads each compiled module (calc/util.js, calc/data/species.js, ...
calc/index.js) as a separate classic <script>, behind one inline shim:

    var calc = exports = {}; function require() { return exports };

so every compiled module writes onto ONE shared `exports`/`calc` object, and `require()` (whatever
path it's given) just hands that object back. That shared-scope trick means every module's
top-level `var` declarations - not just its exports - land in the same global scope too. This
test runs the SAME files, in the SAME order, behind the SAME shim - not production.min.js, which
the page never loads - so a bug that only shows up from that scope-sharing (a same-named top-level
`var` in two files, a script the template forgot to list) is caught here instead of only in a
browser. It's what originally caught: species.ts/moves.ts/types.ts each compile their Gen 1 table
to a same-named top-level `var RBY`; setGen1Species/setGen1Moves/setGen1TypeChart used to fall back
to a lazy `data ?? RBY` read at useDex('vanilla') call time, long after every file had loaded, so
it silently read whichever file's RBY happened to load last instead of its own (see species.ts's
VANILLA_GEN1_SPECIES and its sibling captures in moves.ts/types.ts).

The script list is parsed out of calc/src/normal.template.html itself (the same way
tests/unit/test_calc_preview.py holds calc-preview.js's ENGINE list to it), so this test can't
silently drift from what the page actually loads.

Skips (not fails) when node isn't on PATH, or calc/dist doesn't exist yet (run `npm run build` in
calc/ first).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess

import pytest

_REPO = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_CALC_DIST = os.path.join(_REPO, "calc", "dist")
_TEMPLATE = os.path.join(_REPO, "calc", "src", "normal.template.html")
_DATA_BUNDLE = os.path.join(_CALC_DIST, "calc", "data", "production.min.js")
_MECH_BUNDLE = os.path.join(_CALC_DIST, "calc", "production.min.js")

_SHIM_MARKER = "function require() { return exports };"

_HARNESS = r"""
const vm = require('vm');
const fs = require('fs');

const shim = process.argv[2];
const scriptPaths = JSON.parse(process.argv[3]);

const window = {};
const ctx = {window, console};
vm.createContext(ctx);
vm.runInContext(shim, ctx, {filename: 'shim.js'});
for (const p of scriptPaths) {
  vm.runInContext(fs.readFileSync(p, 'utf8'), ctx, {filename: p});
}
const calc = ctx.calc || ctx.window.calc;

function keys(obj) { return Object.keys(obj).sort(); }

function thunderboltRange() {
  const gen1 = calc.Generations.get(1);
  const r = calc.calculate(
    gen1,
    new calc.Pokemon(gen1, 'Gengar'),
    new calc.Pokemon(gen1, 'Chansey'),
    new calc.Move(gen1, 'Thunderbolt')
  );
  return r.range();
}

const out = {};

// (a) vanilla Gen 1 reference range (calc/calc/src/test/calc.test.ts 'Gengar vs. Chansey').
out.vanillaRange = thunderboltRange();

// (b) Gen 9 calc runs at all (exercises the modern-gen mechanics path).
const gen9 = calc.Generations.get(9);
const r9 = calc.calculate(
  gen9,
  new calc.Pokemon(gen9, 'Pikachu'),
  new calc.Pokemon(gen9, 'Bulbasaur'),
  new calc.Move(gen9, 'Thunderbolt')
);
out.gen9Range = r9.range();

// Pre-swap snapshot, to check useDex('vanilla') restores EXACTLY (not just the reference range).
const preSpeciesKeys = keys(calc.SPECIES[1]);
const preMovesKeys = keys(calc.MOVES[1]);
const preTypeKeys = keys(calc.TYPE_CHART[1]);
const preGengar = JSON.stringify(calc.SPECIES[1]['Gengar']);
const preThunderbolt = JSON.stringify(calc.MOVES[1]['Thunderbolt']);
const preGhostType = JSON.stringify(calc.TYPE_CHART[1]['Ghost']);

// (c) pureRGB swap (calc/calc/src/data/purergb.ts) + alt-STAB reference
// (calc/calc/src/test/purergb.test.ts 'Porygon Tri Attack vs Chansey').
calc.useDex('purergb');
out.hasHardenedOnix = !!calc.SPECIES[1]['Hardened Onix'];
out.hasCrystalType = !!calc.TYPE_CHART[1]['Crystal'];
const gen1p = calc.Generations.get(1);
const triAttack = new calc.Move(gen1p, 'Tri Attack');
out.triAttackType = triAttack.type;
const porygon = new calc.Pokemon(gen1p, 'Porygon', {level: 50});
const chansey50 = new calc.Pokemon(gen1p, 'Chansey', {level: 50});
out.pureRGBRange = calc.calculate(gen1p, porygon, chansey50, triAttack).range();

// (d) useDex('vanilla') restores (a) exactly - species/moves/type-chart key sets and sample
// entries, not just the one reference range.
calc.useDex('vanilla');
out.restoredRange = thunderboltRange();
out.restoredSpeciesKeysMatch = JSON.stringify(keys(calc.SPECIES[1])) === JSON.stringify(preSpeciesKeys);
out.restoredMovesKeysMatch = JSON.stringify(keys(calc.MOVES[1])) === JSON.stringify(preMovesKeys);
out.restoredTypeKeysMatch = JSON.stringify(keys(calc.TYPE_CHART[1])) === JSON.stringify(preTypeKeys);
out.restoredGengarMatch = JSON.stringify(calc.SPECIES[1]['Gengar']) === preGengar;
out.restoredThunderboltMatch = JSON.stringify(calc.MOVES[1]['Thunderbolt']) === preThunderbolt;
out.restoredGhostTypeMatch = JSON.stringify(calc.TYPE_CHART[1]['Ghost']) === preGhostType;
out.hasCrystalTypeAfterRestore = !!calc.TYPE_CHART[1]['Crystal'];

// (e) Stats.calcStat honors `ev` as raw Gen 1/2 stat exp (calc/calc/src/stats.ts calcStatRBY).
out.calcStatEv0 = calc.Stats.calcStat(calc.Generations.get(2), 'atk', 65, 17, 0, 50);
out.calcStatEvMax = calc.Stats.calcStat(calc.Generations.get(2), 'atk', 65, 17, 65535, 50);

console.log(JSON.stringify(out));
"""


def _page_shim_and_script_paths() -> tuple[str, list[str]]:
    """The page's inline shim script (verbatim) and the 'calc/'-prefixed <script src> list from
    normal.template.html, in document order - exactly what the browser loads behind that shim
    (the js/vendor and js/data/sets/* scripts aren't needed to exercise the calc engine itself
    and can't run outside a DOM)."""
    with open(_TEMPLATE, encoding="utf-8") as fh:
        tpl = fh.read()
    blocks = re.findall(r"<script type=\"text/javascript\">(.*?)</script>", tpl, re.S)
    shim = next((b for b in blocks if _SHIM_MARKER in b), None)
    assert shim is not None, "normal.template.html's shim script changed - update _SHIM_MARKER"
    srcs = re.findall(r'src="\./([^"?]+)', tpl)
    calc_srcs = [s for s in srcs if s.startswith("calc/")]
    assert "calc/data/purergb.js" in calc_srcs, "purergb.js missing from the page's script list"
    return shim, [os.path.join(_CALC_DIST, s) for s in calc_srcs]


def _skip_unless_buildable() -> None:
    if not shutil.which("node"):
        pytest.skip("node not found on PATH")
    if not os.path.isdir(_CALC_DIST):
        pytest.skip("calc/dist not built - run `npm run build` in calc/ first")


def _run_page_probe(tmp_path, *, bundled: bool = False) -> dict:
    _skip_unless_buildable()
    shim, paths = ("", [_DATA_BUNDLE, _MECH_BUNDLE]) if bundled else _page_shim_and_script_paths()
    missing = [p for p in paths if not os.path.isfile(p)]
    if missing:
        pytest.skip(f"calc/dist not built - missing {missing[0]} - run `npm run build` in calc/")
    script = tmp_path / "page_probe.js"
    script.write_text(_HARNESS, encoding="utf-8")
    result = subprocess.run(
        [shutil.which("node"), str(script), shim, json.dumps(paths)],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_bundled_api_matches_the_page_modules(tmp_path):
    assert _run_page_probe(tmp_path, bundled=True) == _run_page_probe(tmp_path)


def test_vanilla_gen1_damage_range_matches_jest(tmp_path):
    out = _run_page_probe(tmp_path)
    assert out["vanillaRange"] == [79, 94]


def test_gen9_calc_runs(tmp_path):
    out = _run_page_probe(tmp_path)
    assert len(out["gen9Range"]) == 2
    assert 0 < out["gen9Range"][0] <= out["gen9Range"][1]


def test_puregb_use_dex_swaps_species_and_types_and_matches_jest_alt_stab(tmp_path):
    out = _run_page_probe(tmp_path)
    assert out["hasHardenedOnix"] is True
    assert out["hasCrystalType"] is True
    assert out["triAttackType"] == "Tri"
    assert out["pureRGBRange"] == [57, 67]


def test_use_dex_vanilla_restores_gen1_exactly(tmp_path):
    out = _run_page_probe(tmp_path)
    assert out["restoredRange"] == [79, 94]
    assert out["restoredSpeciesKeysMatch"] is True
    assert out["restoredMovesKeysMatch"] is True
    assert out["restoredTypeKeysMatch"] is True
    assert out["restoredGengarMatch"] is True
    assert out["restoredThunderboltMatch"] is True
    assert out["restoredGhostTypeMatch"] is True
    assert out["hasCrystalTypeAfterRestore"] is False


def test_calc_stat_honors_gen1_2_ev_as_raw_stat_exp(tmp_path):
    # calc/calc/src/stats.ts calcStat(gen, stat, base, iv, ev, level): Gen < 3 treats `ev` as raw
    # stat exp (0-65535), not a Gen 3+ EV, so the UI's stat totals match the damage calc.
    out = _run_page_probe(tmp_path)
    assert out["calcStatEv0"] == 78
    assert out["calcStatEvMax"] == 109


def test_production_bundle_also_loads():
    """production.min.js isn't what the page loads, but server/static/calc-preview.js and any
    other embedder might still use it - a cheap sanity check that it still builds clean and
    exposes useDex."""
    _skip_unless_buildable()
    if not os.path.isfile(_DATA_BUNDLE) or not os.path.isfile(_MECH_BUNDLE):
        pytest.skip("calc/dist bundles not built - run `npm run build` in calc/ first")
    for path in (_DATA_BUNDLE, _MECH_BUNDLE):
        with open(path, encoding="utf-8") as f:
            text = f.read()
        assert "require(" not in text, f"{path} contains an unresolved CommonJS import"
    result = subprocess.run(
        [shutil.which("node"), "-e", (
            "const vm=require('vm'),fs=require('fs');"
            "const ctx={window:{},console};vm.createContext(ctx);"
            f"vm.runInContext(fs.readFileSync({_DATA_BUNDLE!r},'utf8'),ctx);"
            f"vm.runInContext(fs.readFileSync({_MECH_BUNDLE!r},'utf8'),ctx);"
            "if (typeof ctx.window.calc.useDex !== 'function') throw new Error('no useDex');"
        )],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
