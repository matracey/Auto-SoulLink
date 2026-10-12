"""Gen 4 native and inherited moves share the server's Mystery-gap enum."""

import pytest

from server.adapters.gen4_hgsspt import Gen4Adapter
from server.data.moves import gen3_vanilla, gen4, gen5, move_data


@pytest.mark.parametrize("move_id,type_id,type_name", [
    (355, 2, "Flying"), (356, 14, "Psychic"), (362, 11, "Water"),
    (394, 10, "Fire"), (399, 17, "Dark"), (402, 12, "Grass"),
    (406, 16, "Dragon"), (420, 15, "Ice"), (422, 13, "Electric"),
    (418, 8, "Steel"), (421, 7, "Ghost"), (395, 1, "Fighting"),
])
def test_native_move_type_matches_display(move_id, type_id, type_name):
    assert move_data(move_id, generation=4)["type"] == type_id
    shown = Gen4Adapter().move_data(move_id)
    assert (shown["type_id"], shown["type_name"]) == (type_id, type_name)


@pytest.mark.parametrize("move_id", range(355, 468))
def test_native_types_match_the_same_moves_in_gen5(move_id):
    assert gen4.MOVE_DATA[move_id]["type"] == gen5.MOVE_DATA[move_id]["type"]


def test_inherited_move_types_are_not_shifted_again():
    for move_id, raw in gen3_vanilla.MOVE_DATA.items():
        assert move_data(move_id, generation=4)["type"] == raw["type"]
