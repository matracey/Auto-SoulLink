from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def source(path):
    return (ROOT / path).read_text(encoding="utf-8")


def test_gen1_and_gen2_hellos_include_instrumentation_key():
    for path in ("lua/gen1/client.lua", "lua/gen2/client.lua"):
        text = source(path)
        assert "instrumentation_key = SLINK_INSTRUMENTATION_KEY" in text


def test_gen5_captures_key_before_clearing_launcher_globals():
    text = source("lua/clients/gen5_bw_client.lua")

    capture = text.index("local INSTRUMENTATION_KEY = SLINK_INSTRUMENTATION_KEY")
    clear = text.index("SLINK_INSTRUMENTATION_KEY = nil")
    hello = text.index("instrumentation_key=INSTRUMENTATION_KEY")
    assert capture < clear < hello
