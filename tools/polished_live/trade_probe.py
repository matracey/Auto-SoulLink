#!/usr/bin/env python3
"""S1 — live probe for the Polished trade DISPATCH-ENTRY window (docs/polished/TRADE.md §10.6).

Run 2 Stage B already measured the ACCEPT population, but it sampled at
`SlinkDelayFrameBridge` (ROM0 $0070) ENTRY. The dispatcher will not run there: `SlinkTradeDispatch`
is called from INSIDE `SlinkService`, three frames further in, so the window `trade_dispatch.asm`
reads is not the one Stage B sampled. This driver runs the same Stage B probe and then reports the
SAME samples as the dispatch-entry window, so §10.3 can be confirmed or refuted against raw bytes.

The offset is arithmetic, not a guess, and it is derived here rather than pasted. At bridge entry
`sp = B` and `$0DAB` (the return into DelayFrame+3) sits at `B+0`. The bridge then pushes, in this
order (`patch/polished/src/slink.asm:41-45`):

    push af  (hROMBank)   -> B-2
    push hl               -> B-4
    push bc               -> B-6
    push af  (xor a)      -> B-8
    rst Bankswitch        -> no change
    call SlinkService     -> B-10

`SlinkService` runs straight-line today (no pushes, no `ret` before the dispatch call), and
`call SlinkTradeDispatch` costs 2 more:

    dispatch entry sp = B-12

so `dispatch sp + 12 == bridge sp + 0`, exactly as TRADE.md §10.2 states. Consequences this driver
checks against the measurement:

  * dispatch sp+12 == bridge sp+0..1   (DelayFrame + 3)
  * dispatch sp+14 == bridge sp+2..3   (NextOverworldFrame.gfx_done + 6)
  * dispatch sp+24 == bridge sp+12..13 (HandleMap + $15, or $62B5 ScriptEvents.loop+9 in a script)
  * dispatch sp+26 == bridge sp+14..15 (OverworldLoop.loop + 9)
  * dispatch sp+5  == hROMBank         (the low byte of the AF pushed first; the sampled value)

It writes nothing to the shared cache: the lane, cache and ROM all come from $POL_WORK_ROOT.

    POL_WORK_ROOT=F:/slink-work/lanes/pol-trade2 python tools/polished_live/trade_probe.py
"""
from __future__ import annotations

import json
import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent))


from tools.polished_live import harness  # noqa: E402

# The six positions TRADE.md §10.3 pins, expressed in DISPATCH-entry bytes, and where each one
# comes from. sp+5 is hROMBank; the rest are bridge-entry words shifted by the frame depth.
PINNED = {
    5: "hROMBank (the AF pushed by ldh a,[hROMBank]; push af) = LOW(BANK(NextOverworldFrame))",
    12: "LOW(DelayFrame + 3)",
    13: "HIGH(DelayFrame + 3)",
    14: "LOW(NextOverworldFrame.gfx_done + 6)",
    15: "HIGH(NextOverworldFrame.gfx_done + 6)",
    24: "LOW(HandleMap + $15)  -- $62B5 ScriptEvents.loop+9 inside a script",
    25: "HIGH(HandleMap + $15)",
    26: "LOW(OverworldLoop.loop + 9)",
    27: "HIGH(OverworldLoop.loop + 9)",
}
# Positions the card must NOT pin: Stage B and Stage 4 both record these varying.
NOISE = {4, 8, 9, 16, 17, 18, 19, 20, 21, 22, 23}
FRAME_DEPTH = 12          # dispatch sp == bridge sp - 12


def dispatch_bytes(sample: dict) -> dict[int, int]:
    """Bridge-entry sp+0..31 as dispatch-entry sp+12..43, plus hROMBank at sp+5."""
    out: dict[int, int] = {}
    raw = bytes.fromhex(sample["bytes"])
    for i, byte in enumerate(raw):
        out[i + FRAME_DEPTH] = byte
    out[5] = int(sample["rombank"], 16)
    return out


def report(run: pathlib.Path) -> int:
    path = run / "stacks.json"
    if not path.is_file():
        print(f"[trade-probe] no stacks.json in {run}")
        return 1
    rows = json.loads(path.read_text(encoding="utf-8"))
    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(row["phase"], []).append(row)

    print()
    print(f"[trade-probe] DISPATCH-ENTRY window (dispatch sp == bridge sp - {FRAME_DEPTH}), "
          f"{len(rows)} distinct stacks over {sum(r['count'] for r in rows)} samples")
    print("[trade-probe] pinned positions: " +
          ", ".join(f"sp+{k}={v.split('=')[0].strip()}" for k, v in sorted(PINNED.items())))
    print()
    failures = 0
    for phase in sorted(groups):
        samples = groups[phase]
        views = [dispatch_bytes(s) for s in samples]
        total = sum(s["count"] for s in samples)
        # The dominant stack in each phase, by sample count.
        best = max(samples, key=lambda s: s["count"])
        view = dispatch_bytes(best)
        sp_byte = best["sp"]
        print(f"[{phase}] x{total} samples / {len(samples)} distinct, SP={sp_byte}, "
              f"hROMBank={best['rombank']}, dominant x{best['count']}")
        for offset in sorted(PINNED):
            seen = {v.get(offset) for v in views if offset in v}
            line = f"    sp+{offset:<2} = {view.get(offset)}"
            if len(seen) > 1:
                line += f"   VARIES across {len(seen)} values in this phase: {sorted(seen)}"
                failures += 1
            print(line)
        noisy = sorted({v[o] for v in views for o in NOISE if o in v})
        print(f"    noise positions sp+{sorted(NOISE)} in this phase: {len(noisy)} distinct "
              f"byte(s) seen -- deliberately unpinned")
        print()
    print(f"[trade-probe] pinned positions that vary inside a phase: {failures}")
    return failures


def main() -> int:
    from dataclasses import replace
    work = pathlib.Path(os.environ.get("POL_WORK_ROOT", "F:/slink-work/lanes/pol-trade2"))
    env = dict(os.environ)
    env.setdefault("POL_LANE", str(work))
    env.setdefault("POL_EXPLORE", "B")
    env.setdefault("POL_POSMODE", "warp")
    config = harness.launch_config(env)
    cache = work / "cache/polished"
    config = replace(config, cache=cache, rom_src=cache / "companion-overlay/polishedcrystal-3.2.3.gbc")
    rc = harness.cmd_explore(config)
    run = config.lane / f"explore_{config.explore}"
    rc |= report(run)
    print(f"[trade-probe] explore rc={rc} run={run}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
