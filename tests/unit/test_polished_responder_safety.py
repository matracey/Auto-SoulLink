"""Execute the shipped responder: consent must retain its lease before native writes.

Native UI/normalization/save remain model traps. Lease, nickname validation and
all responder control flow execute the committed overlay's actual SM83 bytes.
"""
from __future__ import annotations

import pytest

from tests.unit import test_polished_trade_service as trade_service
from tests.unit.test_polished_trade_responder import Rig, host_consent

shipped_env = trade_service.shipped_env


def visit(env, *, tamper=None, menu=1, nickname=None, yes=True):
    rig = Rig(env)
    native_writes = []
    menus = 0

    def on_native(host, what):
        nonlocal menus
        if what == "yesno":
            menus += 1
            if menus == menu and tamper is not None:
                tamper(host)

    rig.on_native = on_native

    def setup(host):
        m = host.m
        allowed = {env.sym[n] for n in ("FixPlayerEVsAndStats", "Link_SaveGame")}
        m.farcall_allowed = allowed
        for name in ("FixPlayerEVsAndStats", "Link_SaveGame"):
            bank, addr = env.sym[name]

            def native(mm, name=name):
                native_writes.append((name, menus))
                mm.cf = False

            m.trap(addr, native, bank=bank)
        if nickname is not None:
            m.poke(rig.a("wPartyMonNicknames") + rig.own * 11, nickname)
        return host_consent(apply=False)(host)

    run = rig.run(setup, yesno=yes)
    assert run.fault is None
    assert run.res.sp_delta == 0
    return run, native_writes


def cancel(host):
    host.m.poke(host.rig.lease + 12, bytes(4))


def change_count(host):
    host.m.poke(host.rig.a("wPartyCount"), 5)


def change_generation(host):
    host.wr(6, host.rd(6) + 1)


def change_slot(host):
    host.wr(9, 1)


def invalidate_record(host):
    host.m.poke(host.rig.a("wPartyMon1") + host.rig.own * 48 + 31, 0)


@pytest.mark.parametrize("menu", [1, 2], ids=["offer-consent", "save-consent"])
@pytest.mark.parametrize("tamper", [cancel, change_count, change_generation, change_slot, invalidate_record])
def test_stale_consent_never_normalizes_or_saves(shipped_env, menu, tamper):
    run, writes = visit(shipped_env, tamper=tamper, menu=menu)
    assert writes == [], f"native writes consumed stale consent: {writes}"
    assert run.events == "AC", "cancelled consent must close without publishing DONE"
    assert sum(c[0] == "close" for c in run.calls) == 1


def test_cancelled_no_answer_cannot_republish_the_private_token(shipped_env):
    run, writes = visit(shipped_env, tamper=cancel, yes=False)
    assert writes == []
    assert run.events == "AC", "NO on a cancelled visit must not resurrect its token in DONE"


def test_valid_consent_normalizes_and_saves_only_after_both_yes_answers(shipped_env):
    run, writes = visit(shipped_env)
    assert writes == [("FixPlayerEVsAndStats", 2), ("Link_SaveGame", 2)]
    assert run.events == "ADC"
    assert [d["o8"] for d in run.info if d["kind"] == "D"] == [0]


@pytest.mark.parametrize("nickname", [bytes([0x80] * 11), bytes([0x56, 0x53] + [0x80] * 9),
                                      bytes([0x5D, 0x53] + [0x80] * 9)],
                         ids=["unterminated", "line-control", "compressed-control"])
def test_invalid_outgoing_nickname_is_refused_before_native_rendering(shipped_env, nickname):
    run, writes = visit(shipped_env, nickname=nickname, yes=False)
    assert not run.calls, f"invalid nickname reached native UI: {run.calls}"
    assert run.events == "C"
    assert writes == []


def test_ten_literal_glyph_nickname_is_renderable_and_decline_is_nonmutating(shipped_env):
    run, writes = visit(shipped_env, nickname=bytes([0x80] * 10 + [0x53]), yes=False)
    assert [c[0] for c in run.calls] == ["nick", "open", "print", "yesno", "close"]
    assert run.events == "ADC"
    assert [d["o8"] for d in run.info if d["kind"] == "D"] == [1]
    assert writes == []
