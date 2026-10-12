"""Empty RR move slots retain sentinel semantics through generation and lookup."""

from server.data.moves import move_data
from server.data.moves.gen3_rr import MOVE_DATA
from tools.gen_move_data import parse_rr_battle_moves


def test_rr_generator_keeps_empty_move_separate_from_pound():
    text = """
[MOVE_NONE] =
{
    .type = TYPE_MYSTERY,
    .power = 40,
    .accuracy = 100,
    .pp = 35,
    .split = SPLIT_PHYSICAL,
},
[MOVE_POUND] =
{
    .type = TYPE_NORMAL,
    .power = 40,
    .accuracy = 100,
    .pp = 35,
    .split = SPLIT_PHYSICAL,
},
"""
    generated = parse_rr_battle_moves(text)
    assert generated[0] == {"type": 9, "power": 0, "accuracy": 0, "pp": 0, "split": 2}
    assert generated[1] == {"type": 0, "power": 40, "accuracy": 100, "pp": 35, "split": 0}
    assert generated[0] == MOVE_DATA[0] == move_data(0, variant="rr")
    assert generated[1] == MOVE_DATA[1] == move_data(1, variant="rr")
