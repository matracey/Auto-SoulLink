"""Gen 1 retries retain pairing/invocation-specific, immutable diagnostic evidence."""
from collections import Counter
from types import SimpleNamespace

import pytest

from tools import e2e_duo as duo


@pytest.mark.parametrize("game", (
    "gen1_new", "gen1_pure", "gen1_pure_green", "gen3_frlg", "gen3_emerald", "gen3_rr",
))
def test_retry_cleanup_retains_earlier_attempt_but_discards_live_results(tmp_path, monkeypatch, game):
    monkeypatch.setattr(duo, "BUILD", str(tmp_path))
    run = object.__new__(duo.DuoRun)
    run.scenario, run.game, run.attempt = "link_new", game, 2
    run.go_files = {side: str(tmp_path / f"go_{side}") for side in ("a", "b")}
    prior = tmp_path / "e2e_link_new_a_attempt1_result.txt"
    live = tmp_path / "e2e_link_new_a_result.txt"
    prior.write_text("RESULT: FAIL (hunt ended out-of-balls)\n", encoding="utf-8")
    live.write_text("RESULT: PASS stale\n", encoding="utf-8")
    run._clear_attempt_artifacts()
    assert prior.read_text(encoding="utf-8") == "RESULT: FAIL (hunt ended out-of-balls)\n"
    assert not live.exists()


def _recorded_invocations(tmp_path, monkeypatch, *, retry=False, failure=None):
    monkeypatch.setattr(duo, "BUILD", str(tmp_path))
    clear = duo.DuoRun._clear_attempt_artifacts
    observed = []

    class RecordedRun:
        artifact_name = "link_new"
        scenario = "link_new"

        def __init__(self, name, args, attempt):
            self.args, self.attempt, self.game = args, attempt, args.game
            self.go_files = {side: str(tmp_path / f"go_{side}") for side in ("a", "b")}

        def run(self):
            clear(self)
            for side in ("a", "b", "pydec"):
                token = f"game={self.game} invocation={self.args.invocation} attempt={self.attempt} side={side}\n"
                result = duo.RNG_OUT_OF_BALLS if retry and self.attempt == 1 and side == "a" else "RESULT: PASS"
                if failure is not None and side == "a":
                    result = failure
                text = token + result + "\n"
                (tmp_path / f"e2e_link_new_{side}_result.txt").write_text(text, encoding="utf-8")
                observed.append(text)
            return failure is None and not (retry and self.attempt == 1)

    monkeypatch.setattr(duo, "DuoRun", RecordedRun)
    return observed


def _archives(tmp_path):
    return list(tmp_path.rglob("*_attempt*_result.txt"))


def test_gen1_archives_do_not_collide_between_pairings_or_invocations(tmp_path, monkeypatch):
    observed = _recorded_invocations(tmp_path, monkeypatch)
    for invocation, game in enumerate(("gen1_pure", "gen1_pure_green", "gen1_pure"), start=1):
        args = SimpleNamespace(game=game, idle_jitter=0, invocation=invocation, lane=f"P_{game}")
        assert duo.run_scenario_with_rng_retry("link_new", args) == (True, 1)
    archived = _archives(tmp_path)
    assert Counter(path.read_text(encoding="utf-8") for path in archived) == Counter(observed)
    assert all(path.parent != tmp_path for path in archived), "immutable copies must be outside the live cleanup prefix"
    assert all("gen1_pure" in str(path.relative_to(tmp_path)) for path in archived)


def test_gen1_first_catch_ball_miss_keeps_every_attempt_without_changing_retry(tmp_path, monkeypatch):
    observed = _recorded_invocations(tmp_path, monkeypatch, retry=True)
    args = SimpleNamespace(game="gen1_pure_green", idle_jitter=0, invocation=1, lane="P_gen1_pure_green")
    assert duo.run_scenario_with_rng_retry("link_new", args) == (True, 2)
    assert Counter(path.read_text(encoding="utf-8") for path in _archives(tmp_path)) == Counter(observed)


@pytest.mark.parametrize("failure, attempts", [
    ("RESULT: FAIL (binding identity refused)", 1),
    (duo.RNG_OUT_OF_BALLS, 3),
])
def test_gen1_terminal_failure_keeps_every_sides_exact_receipt(tmp_path, monkeypatch, failure, attempts):
    observed = _recorded_invocations(tmp_path, monkeypatch, failure=failure)
    args = SimpleNamespace(game="gen1_pure_green", idle_jitter=0, invocation=1, lane="P_gen1_pure_green")
    assert duo.run_scenario_with_rng_retry("link_new", args) == (False, attempts)
    assert len(observed) == 3 * attempts
    assert Counter(path.read_text(encoding="utf-8") for path in _archives(tmp_path)) == Counter(observed)
