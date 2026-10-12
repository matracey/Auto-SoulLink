import pytest

from tests.unit.test_core_session import A, B, World


def control(w, paused=(), cancelled=None):
    w.s.handle_command(w.s, w.lua.table_from({
        "cmd": "recovery_control", "paused": w.lua.table_from(list(paused)),
        "cancelled": w.lua.table_from(cancelled or {}),
    }))


@pytest.mark.parametrize("command", ["box_mon", "party_mon", "memorialize", "force_faint"])
def test_missing_pair_holds_already_delivered_mutations(command):
    w = World()
    w.step_to(60)
    w.st.checkpoint = False
    w.command(cmd=command, key=A)
    w.step()
    control(w, paused=[A])
    w.st.checkpoint = True
    w.step(3)
    assert not w.called("deposit") and not w.called("withdraw")
    assert not w.called("memorialize") and not w.called("faint_slot")
    assert len(w.q["items"]) == 1
    control(w)
    w.step()
    assert len(w.q["items"]) == 0


def test_pause_does_not_block_an_unrelated_mon():
    w = World()
    w.step_to(60)
    control(w, paused=[A])
    w.command(cmd="box_mon", key=A)
    w.command(cmd="box_mon", key=B)
    w.step()
    assert w.called("deposit") == [("deposit", B)]
    assert len(w.q["items"]) == 1


def test_limbo_pauses_held_battle_write_and_reappearance_resumes_it():
    w = World()
    w.step_to(60)
    w.st.in_battle = True
    w.st.battle_result = "hold"
    w.command(cmd="force_faint", key=A)
    w.step()
    before = len(w.battle_writes())
    control(w, paused=[A])
    w.st.battle_result = "done"
    w.step(3)
    assert len(w.battle_writes()) == before
    assert w.s.battle_pending_count(w.s) == 1
    control(w)
    w.step()
    assert len(w.battle_writes()) == before + 1


def test_resolution_cancels_old_deferred_and_battle_work_once():
    w = World()
    w.step_to(60)
    w.st.checkpoint = False
    w.command(cmd="box_mon", key=A)
    w.step()
    w.st.in_battle = True
    w.st.battle_result = "hold"
    w.command(cmd="force_faint", key=A)
    w.step()
    control(w, cancelled={A: 1})
    assert not len(w.q["items"])
    assert not w.s.battle_pending_count(w.s)
    w.command(cmd="force_faint", key=A)
    w.step()
    control(w, cancelled={A: 1})
    assert w.s.battle_pending_count(w.s) == 1
