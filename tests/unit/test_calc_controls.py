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
