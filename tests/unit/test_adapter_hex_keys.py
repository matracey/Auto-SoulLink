import pytest

from server.adapters.gen3_frlge import Gen3Adapter
from server.adapters.gen4_hgsspt import Gen4Adapter
from server.adapters.gen5_bw import Gen5Adapter


@pytest.fixture(params=[Gen3Adapter, Gen4Adapter, Gen5Adapter])
def adapter(request):
    return request.param()


@pytest.mark.parametrize("key", [
    None, 1, True, [], {}, b"A:B",
    "", "A", ":B", "A:", "A:B:C", "G:B", "A:G",
    "-1:0", "0:-1", "+1:0", "0:+1", "0x1:0", "0:0X1",
    "1_0:0", "0:1_0", " A:B", "A:B ", "A:B\n",
    "\u0661:B", "A:\uff11", "123456789:0", "0:123456789",
])
def test_invalid_hex_keys_do_not_produce_an_ot_id(adapter, key):
    assert adapter.is_valid_mon_key(key) is False
    assert adapter.parse_ot_id(key) == ""


@pytest.mark.parametrize("key,ot_id", [
    ("0:0", "0"), ("A:b", "b"), ("aBcDeF:0123", "0123"),
    ("00000000:00000000", "00000000"), ("FFFFFFFF:abcdef01", "abcdef01"),
])
def test_short_and_full_hex_keys_preserve_the_ot_id(adapter, key, ot_id):
    assert adapter.is_valid_mon_key(key) is True
    assert adapter.parse_ot_id(key) == ot_id
