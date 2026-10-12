"""Real calculator-page regressions; requires Python Playwright, Edge and a calc build."""
import os
from pathlib import Path
from urllib.parse import urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[2]
DIST = Path(os.environ.get("SLINK_CALC_DIST", ROOT / "calc/dist"))


@pytest.fixture(scope="module", params=["normal", "hardcore"])
def calc_page(request):
    playwright = pytest.importorskip("playwright.sync_api")
    if not (DIST / "calc/index.js").is_file():
        pytest.skip("calc build absent: build calc or set SLINK_CALC_DIST")

    def serve(route):
        url = urlsplit(route.request.url)
        if url.hostname != "calc.test":
            return route.abort()
        if url.path.endswith("/api/calc/mons"):
            return route.fulfill(json={
                "calc": {"gen": 9, "dex": "rr"},
                "a": {"party": []}, "b": {"party": []},
            })
        relative = url.path.removeprefix("/calc/")
        source = ROOT / "calc/src" / relative
        file = source if source.is_file() else DIST / relative
        if not file.is_file():
            return route.fulfill(status=404, body="missing " + relative)
        return route.fulfill(path=file)

    with playwright.sync_playwright() as runtime:
        executable = os.environ.get("SLINK_CHROMIUM")
        options = {"executable_path": executable} if executable else {"channel": "msedge"}
        browser = runtime.chromium.launch(headless=True, **options)
        try:
            page = browser.new_page()
            page.route("**/*", serve)
            page.add_init_script("window.EventSource = class extends EventTarget { close() {} };")
            page.goto(f"http://calc.test/calc/{request.param}.html?gen=9")
            page.wait_for_function("typeof createPokemon === 'function' && gen === 9")
            yield page
        finally:
            browser.close()


@pytest.mark.parametrize("panel_status,code,counter", [
    ("Badly Poisoned", "tox", 1),
    ("Badly Poisoned", "tox", 5),
    ("Badly Poisoned", "tox", 15),
    ("Healthy", "", 0),
    ("Burned", "brn", 0),
    ("Poisoned", "psn", 0),
])
@pytest.mark.parametrize("window_status", ["", "Badly Poisoned"])
def test_toxic_counter_uses_its_panel_status(calc_page, panel_status, code, counter, window_status):
    result = calc_page.evaluate("""args => {
        window.status = args.windowStatus;
        const panel = $('#p1');
        panel.find('.status').val(args.panelStatus);
        panel.find('.toxic-counter').val(args.counter || 7);
        const pokemon = createPokemon(panel);
        return {status: pokemon.status, counter: pokemon.toxicCounter};
    }""", {"panelStatus": panel_status, "counter": counter, "windowStatus": window_status})
    assert result == {"status": code, "counter": counter}


@pytest.mark.parametrize("type1,type2,ability,item,gravity,grounded", [
    ("Flying", "", "Prankster", "", False, False),
    ("Flying", "Dragon", "Pressure", "", False, False),
    ("Normal", "Flying", "Pressure", "", False, False),
    ("Normal", "", "Levitate", "", False, False),
    ("Normal", "", "Pressure", "Air Balloon", False, False),
    ("Normal", "", "Pressure", "", False, True),
    ("Flying", "", "Prankster", "", True, True),
    ("Normal", "Flying", "Levitate", "Air Balloon", True, True),
])
@pytest.mark.parametrize("terrain", ["Electric", "Misty"])
def test_terrain_respects_both_types_and_airborne_exemptions(
    calc_page, type1, type2, ability, item, gravity, grounded, terrain,
):
    result = calc_page.evaluate("""args => {
        const panel = $('#p1');
        panel.find('.type1').val(args.type1);
        panel.find('.type2').val(args.type2);
        panel.find('.ability').val(args.ability);
        panel.find('.item').val(args.item);
        $('#gravity').prop('checked', args.gravity);
        $("input[name='terrain']").prop('checked', false);
        $("input[name='terrain']").filter(function () {
            return this.value === args.terrain;
        }).prop('checked', true).change();
        return {
            grounded: isPokeInfoGrounded(panel),
            disabled: args.terrain === 'Electric'
                ? panel.find("[value='Asleep']").prop('disabled')
                : panel.find('.status').prop('disabled')
        };
    }""", {"type1": type1, "type2": type2, "ability": ability, "item": item,
           "gravity": gravity, "terrain": terrain})
    assert result == {"grounded": grounded, "disabled": grounded}


@pytest.mark.parametrize("spread", [
    "EVs: 252 SpA",
    "IVs: 0 Atk",
    "EVs: 252 SpA\nIVs: 0 Atk",
])
def test_imports_do_not_inherit_previous_spreads(calc_page, spread):
    result = calc_page.evaluate("""spread => {
        const before = JSON.stringify(calc.SPECIES[9].Pikachu);
        localStorage.removeItem('customsets');
        addSets('Pikachu\\nLevel: 25\\n' + spread + '\\n- Thunderbolt', 'with spread');
        addSets('Pikachu\\nLevel: 40\\n- Thunderbolt', 'without spread');
        const stored = JSON.parse(localStorage.customsets).Pikachu;
        $('#p1 input.set-selector').val('Pikachu (without spread)').change();
        const pokemon = createPokemon($('#p1'));
        return {stored, unchanged: before === JSON.stringify(calc.SPECIES[9].Pikachu),
                attackIV: pokemon.ivs.atk, specialAttackEV: pokemon.evs.spa};
    }""", spread)
    assert result["unchanged"]
    assert result["stored"]["with spread"]["level"] == 25
    assert result["stored"]["without spread"]["level"] == 40
    if "EVs:" in spread:
        assert result["stored"]["with spread"]["evs"] == {"sa": 252}
    if "IVs:" in spread:
        assert result["stored"]["with spread"]["ivs"] == {"at": 0}
    assert "evs" not in result["stored"]["without spread"]
    assert "ivs" not in result["stored"]["without spread"]
    assert result["attackIV"] == 31
    assert result["specialAttackEV"] == 0
