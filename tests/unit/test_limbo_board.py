import copy
import json
import shutil
import subprocess
from pathlib import Path

import jinja2
import pytest

from server.board import board_context, build_board
from server.status_payload import empty_status_payload
from tests.unit.test_dashboard_contract import parse
from tests.unit.test_dashboard_js import STUB

ROOT = Path(__file__).resolve().parents[2]


def missing_status():
    status = empty_status_payload()
    status["links"] = [{
        "area_id": "route_1", "a_key": "A:1", "b_key": "B:1", "status": "alive",
        "a_nickname": "Missing", "b_nickname": "Here",
    }]
    status["players"]["a"]["party_details"] = {"A:1": {"hp": 20, "maxHP": 20}}
    status["players"]["b"]["party_details"] = {"B:1": {"hp": 20, "maxHP": 20}}
    status["limbo"] = {"a": {"A:1": {"revision": "incident-1"}}, "b": {}}
    status["recovery_candidates"] = {"a": [{"key": "A:2", "nickname": "<script>"}], "b": []}
    return status


def test_limbo_moves_only_affected_pair_and_preserves_source_data():
    status = missing_status()
    before = copy.deepcopy(status)
    board = build_board(status)
    section, rows = board["sections"][0]
    assert section == "limbo"
    assert rows[0]["a"]["hp"] is None
    assert rows[0]["a"]["where"] is None
    assert rows[0]["b"]["hp"] == 20
    assert board["counts"] == {"alive": 1, "fallen": 0}
    assert status == before


def test_explicit_death_does_not_hide_an_unresolved_missing_half():
    status = missing_status()
    status["links"][0]["status"] = "dead"
    board = build_board(status)
    assert board["sections"][0][0] == "limbo"
    assert board["counts"] == {"alive": 0, "fallen": 1}


def test_missing_bonus_without_a_link_is_still_resolvable():
    status = missing_status()
    status["links"] = []
    status["limbo"]["a"]["A:1"].update({"area_id": "_bonus_pending_A:1", "nickname": "Missing"})
    section, rows = build_board(status)["sections"][0]
    assert section == "limbo"
    assert rows[0]["a"]["key"] == "A:1"

@pytest.mark.parametrize("admin,live,visible", [(True, True, True), (False, True, False), (True, False, False)])
def test_resolution_is_per_mon_and_only_shown_to_live_admin(admin, live, visible):
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(ROOT / "server/templates"), autoescape=True)
    markup = env.get_template("_board.html").render(
        **board_context(missing_status(), live=live), is_admin=admin)
    dom = parse(markup)
    forms = list(dom.find_all("form", **{"class": "mon-recovery"}))
    assert bool(forms) is visible
    assert "sync paused" in dom.text_content()
    if visible:
        assert forms[0].get("data-player") == "a"
        assert forms[0].get("data-key") == "A:1"
        assert forms[0].get("data-revision") == "incident-1"
        assert "required" not in forms[0].find("select", name="action").attrs
        details = dom.find("details", **{"data-details-key": "limbo:a:incident-1"})
        assert details.get("id") == "d-limbo:a:incident-1"
        assert "&lt;script&gt;" in markup


_SCRIPT = r"""
const fs = require('node:fs');
const mode = process.argv[2];
let submit, requests = [], confirmations = [];
const output = {textContent: ''}, button = {disabled: false};
const form = {
  elements: {action: {value: mode === 'leave' ? '' : mode === 'retire' ? 'retire' : 'rebind'},
             target_key: {value: mode === 'missing' ? '' : 'A:2'}},
  dataset: {player: 'a', key: 'A:1', revision: 'incident-1', name: 'Missing'},
  querySelector: s => s === 'output' ? output : button
};
global.document = {addEventListener: (name, fn) => {submit = fn;}};
global.location = {pathname: mode === 'standalone' ? '/' : '/runs/run-1'};
global.window = {confirm: message => {confirmations.push(message); return mode !== 'cancel';}};
global.fetch = async (url, options) => {
  requests.push({url, options});
  if (mode === 'network') throw Error('connection lost');
  return {ok: mode !== 'stale', status: mode === 'stale' ? 409 : 200,
          json: async () => ({ok: mode !== 'stale', error: 'incident changed'})};
};
new Function(fs.readFileSync(process.argv[1], 'utf8'))();
(async () => {
  await submit({target: {closest: () => form}, preventDefault() {}});
  console.log(JSON.stringify({requests, confirmations, message: output.textContent, disabled: button.disabled}));
})();
"""


@pytest.mark.parametrize("mode", ["standalone", "manager", "leave", "missing", "cancel", "retire", "network", "stale"])
def test_resolution_form_confirms_and_reports_outcome(mode):
    bun = shutil.which("bun")
    if not bun:
        pytest.skip("Bun is required for browser event regression")
    process = subprocess.run([bun, "-e", _SCRIPT, str(ROOT / "server/static/mon-recovery.js"), mode],
                             capture_output=True, text=True, check=True, timeout=30)
    result = json.loads(process.stdout)
    assert result["disabled"] is False
    if mode in ("leave", "missing", "cancel"):
        assert result["requests"] == []
    else:
        request, = result["requests"]
        assert request["url"] == ("/api/recovery" if mode == "standalone" else "/runs/run-1/api/recovery")
        assert json.loads(request["options"]["body"])["revision"] == "incident-1"
        assert result["confirmations"]
    if mode == "network":
        assert "Could not confirm" in result["message"]
    elif mode == "stale":
        assert "incident changed" in result["message"]
    elif mode in ("standalone", "manager", "retire"):
        assert result["message"].startswith("Resolved.")


_MORPH = STUB.split("new Function(src)();", 1)[0] + r"""
global.Idiomorph = win.Idiomorph = {defaults: {callbacks: {}}};
new Function(src)();
function option(value) { return {value, cloneNode() {return option(value);}}; }
function select(name, values, value) {
  return {name, value, innerHTML: JSON.stringify(values), childNodes: values.map(option),
    get options() {return this.childNodes;},
    replaceChildren(...nodes) {this.childNodes = nodes; this.innerHTML = JSON.stringify(nodes.map(n => n.value));}};
}
const scenario = JSON.parse(process.argv[3]);
const action = select('action', ['', 'rebind', 'retire'], 'rebind');
const target = select('target_key', ['', 'A:2'], scenario.selected);
const fresh = {action: select('action', scenario.actions, ''),
               target_key: select('target_key', scenario.candidates, '')};
const form = {hasAttribute: () => true, classList: {contains: c => c === 'mon-recovery'},
              querySelectorAll: () => [action, target], disabled: true, output: 'Applying resolution...'};
const before = [form.disabled, form.output];
const result = Idiomorph.defaults.callbacks.beforeNodeMorphed(form, {
  querySelector: s => fresh[/name="([^"]+)"/.exec(s)[1]]
});
const tradeKept = Idiomorph.defaults.callbacks.beforeNodeMorphed({
  hasAttribute: () => true, classList: {contains: () => false}
}, {});
console.log(JSON.stringify({result, tradeKept, action: action.value, selected: target.value,
  options: target.options.map(o => o.value), before, after: [form.disabled, form.output]}));
"""


@pytest.mark.parametrize("selected,candidates,actions,want_selected,want_action", [
    ("", ["", "A:2", "A:3"], ["", "rebind", "retire"], "", "rebind"),
    ("A:2", ["", "A:2", "A:3"], ["", "rebind", "retire"], "A:2", "rebind"),
    ("A:2", ["", "A:3"], ["", "rebind", "retire"], "", "rebind"),
    ("A:2", ["", "A:2"], ["", "retire"], "A:2", ""),
])
def test_morph_refreshes_recovery_choices_without_losing_valid_input(
        selected, candidates, actions, want_selected, want_action):
    bun = shutil.which("bun")
    if not bun:
        pytest.skip("Bun is required for browser event regression")
    scenario = json.dumps({"selected": selected, "candidates": candidates, "actions": actions})
    process = subprocess.run([bun, "-e", _MORPH, str(ROOT / "server/static/dashboard.js"), "/", scenario],
                             capture_output=True, text=True, check=True, timeout=30)
    result = json.loads(process.stdout)
    assert result["options"] == candidates
    assert result["selected"] == want_selected and result["action"] == want_action
    assert result["result"] is False and result["tradeKept"] is False
    assert result["before"] == result["after"]
