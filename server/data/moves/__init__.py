"""server/data/moves — Unified move-data lookup across generations.

Replaces the previous fragmented split (server/move_data.py for gen3/gen4,
server/move_data_gen5.py for gen5). Each generation's move tables live in
its own submodule; this package exposes a single ``move_name`` /
``move_data`` accessor that handles per-generation lookup chains.

Lookup chains (most-specific first, fall-through to the previous gen):
  gen 3 vanilla  → gen3_vanilla
  gen 3 RR       → gen3_rr (Radical Red has a completely different table)
  gen 4          → gen4 (including category overrides) → gen3_vanilla
  gen 5          → gen5   → gen3_vanilla

Gen 1 / Gen 2 load their move data from data/games/<gen>/moves.json inside
their respective adapters and do not use this package.
"""

from . import gen3_rr, gen3_vanilla, gen4, gen5


def _chain(generation: int, variant: str, want: str):
    """Return the ordered list of lookup tables for a (gen, variant) pair.

    `want` is 'NAMES' or 'DATA'.
    """
    if generation == 3 and variant == "rr":
        return [getattr(gen3_rr, f"MOVE_{want}")]
    if generation == 4:
        return [getattr(gen4, f"MOVE_{want}"), getattr(gen3_vanilla, f"MOVE_{want}")]
    if generation == 5:
        return [getattr(gen5, f"MOVE_{want}"), getattr(gen3_vanilla, f"MOVE_{want}")]
    return [getattr(gen3_vanilla, f"MOVE_{want}")]


def move_name(move_id: int, *, generation: int = 3, variant: str = "vanilla") -> str:
    """Return display name for a move ID, or '' if the move isn't known."""
    for tbl in _chain(generation, variant, "NAMES"):
        if move_id in tbl:
            return tbl[move_id]
    return ""


def move_data(move_id: int, *, generation: int = 3, variant: str = "vanilla") -> dict | None:
    """Return move stats dict {type, power, accuracy, pp, split} or None."""
    for tbl in _chain(generation, variant, "DATA"):
        if move_id in tbl:
            return tbl[move_id]
    return None
