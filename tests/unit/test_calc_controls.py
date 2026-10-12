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


@pytest.mark.parametrize("header,species,set_name", [
    ("Eevee (Pikachu) @ Leftovers", ["Pikachu"], "Eevee"),
    ("Pikachu (Eevee) @ Leftovers", ["Eevee"], "Pikachu"),
    ("Sparky (Pikachu) (M) @ Leftovers", ["Pikachu"], "Sparky"),
    ("Aegislash (Pikachu) @ Leftovers", ["Pikachu"], "Aegislash"),
    ("Pikachu (M) @ Leftovers", ["Pikachu"], "header regression"),
    ("Pikachu @ Leftovers", ["Pikachu"], "header regression"),
    ("Aegislash @ Leftovers", ["Aegislash-Blade", "Aegislash-Shield"], "header regression"),
])
def test_import_header_resolves_one_species(calc_page, header, species, set_name):
    messages = []

    def accept(dialog):
        messages.append(dialog.message)
        dialog.accept()

    calc_page.once("dialog", accept)
    result = calc_page.evaluate("""header => {
        localStorage.removeItem('customsets');
        addSets(header + '\\nLevel: 50\\n- Thunderbolt', 'header regression');
        return JSON.parse(localStorage.customsets);
    }""", header)
    assert sorted(result) == species
    for value in result.values():
        assert list(value) == [set_name]
        assert value[set_name]["item"] == "Leftovers"
        assert value[set_name]["level"] == 50
    assert messages == ["Successfully imported 1 set(s)"]


@pytest.mark.parametrize("separator", ["\n\n", "\n \n", "\n"])
def test_import_metadata_stays_with_its_set(calc_page, separator):
    first = "Pikachu\nLevel: 50\nAbility: Static\nTimid Nature\nEVs: 252 SpA\nIVs: 0 Atk\n- Thunderbolt"
    second = "Eevee\nLevel: 5\nAbility: Adaptability\nAdamant Nature\nEVs: 252 Atk\nIVs: 0 Spe\n- Tackle"
    result = calc_page.evaluate("""text => {
        localStorage.removeItem('customsets');
        addSets(text, 'separate sets');
        return JSON.parse(localStorage.customsets);
    }""", first + separator + second)
    pikachu = result["Pikachu"]["separate sets"]
    eevee = result["Eevee"]["separate sets"]
    assert (pikachu["level"], pikachu["ability"], pikachu["nature"]) == (50, "Static", "Timid")
    assert (eevee["level"], eevee["ability"], eevee["nature"]) == (5, "Adaptability", "Adamant")
    assert (pikachu["evs"], pikachu["ivs"], pikachu["moves"]) == ({"sa": 252}, {"at": 0}, ["Thunderbolt"])
    assert (eevee["evs"], eevee["ivs"], eevee["moves"]) == ({"at": 252}, {"sp": 0}, ["Tackle"])


@pytest.mark.parametrize("text", [
    "Pikachu",
    "Pikachu\n",
    "Pikachu\n\nEevee\nLevel: 5\n- Tackle",
    "Pikachu\nEevee\nLevel: 5\n- Tackle",
    "Pikachu\n" + "Shiny: Yes\n" * 12 + "- Thunderbolt",
])
def test_minimal_or_long_sets_keep_their_own_defaults(calc_page, text):
    result = calc_page.evaluate("""text => {
        localStorage.removeItem('customsets');
        addSets(text, 'minimal set');
        return JSON.parse(localStorage.customsets);
    }""", text)
    pikachu = result["Pikachu"]["minimal set"]
    assert pikachu["level"] == 100
    assert pikachu["nature"] == "Serious"
    assert pikachu["moves"] == (["Thunderbolt"] if "Thunderbolt" in text else [])
    if "Eevee" in text:
        assert result["Eevee"]["minimal set"]["level"] == 5
        assert result["Eevee"]["minimal set"]["moves"] == ["Tackle"]
