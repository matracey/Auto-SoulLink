"""Legacy qualification instrument regressions; imports and byte staging never launch emulators."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load(name):
    spec = importlib.util.spec_from_file_location('legacy_'+name, ROOT/'tools/polished_live'/f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_overlay_stages_from_the_committed_patch_not_an_obsolete_cache(tmp_path, monkeypatch):
    from patch.tools.make_ups import ups_create
    h = load('harness')
    base, overlay = b'base bytes', b'current overlay bytes'
    repo, cache = tmp_path/'repo', tmp_path/'cache'
    for p in [repo/'data/polished',repo/'patch/dist',cache/'release']:
        p.mkdir(parents=True)
    (repo/'data/polished/overlay_provenance.json').write_text(json.dumps({'output':{'sha1':hashlib.sha1(overlay).hexdigest()}}))
    (repo/'patch/dist/SLink-Polished.ups').write_bytes(ups_create(base,overlay))
    (cache/'release/polishedcrystal-3.2.3.gbc').write_bytes(base)
    from dataclasses import replace
    config = replace(h.launch_config({}), repo=repo, cache=cache, kind="overlay",
                     rom_src=cache/'companion-overlay/polishedcrystal-3.2.3.gbc',
                     rom=tmp_path/'lane/current.gbc')
    assert h.stage_rom(config)==hashlib.sha1(overlay).hexdigest()
    assert config.rom.read_bytes()==overlay
    (repo/'data/polished/overlay_provenance.json').write_text(json.dumps({'output':{'sha1':'0'*40}}))
    with pytest.raises(SystemExit, match='provenance'):
        h.stage_rom(config)




@pytest.mark.parametrize("text,expected", [("SOUL LINK\nNO CLIENT", True), ("SLink is linked.", False), ("SOUL LINK", False)], ids=["fallback", "obsolete", "incomplete"])
def test_phone_fallback_predicate(text, expected):
    from lupa import LuaRuntime
    source=(ROOT/'tools/polished_live/phone.lua').read_text()
    line=next(line for line in source.splitlines() if ': the SLink entry text box shows' in line)
    lua=LuaRuntime()
    lua.execute('L={hits={SlinkPanel=1},check=function(_,ok) result=not not ok end}; tag="test"')
    lua.globals().box=text
    lua.execute(line)
    assert lua.globals().result is expected


def test_capture_count_is_sampled_before_quarantine():
    from lupa import LuaRuntime
    source=(ROOT/'tools/polished_live/live.lua').read_text()
    start=source.index('L.check("STAGE2 native capture')
    expression=source[start:source.index('\n            break',start)]
    lua=LuaRuntime()
    lua.execute('L={rw=function() return 5 end,check=function(_,ok) result=ok end}; party_before=5; hit={party_count=6}')
    lua.execute(expression)
    assert lua.globals().result is True
    lua.execute('hit.party_count=5')
    lua.execute(expression)
    assert lua.globals().result is False





def test_receptionist_stack_probe_ignores_other_banks():
    from lupa import LuaRuntime
    source=(ROOT/'tools/polished_live/explore.lua').read_text()
    start=source.index('L.hook("SlinkDelayFrameBridge"')
    end=source.index('    -- known-positive control',start)
    lua=LuaRuntime()
    lua.execute('fmt=string.format; phase="test"; counts={}; stacks={}; order={}; reads=0; emu={getregister=function() reads=reads+1; return 100 end}; L={hook=function(_,f) callback=f end,bus=function() return 0 end,hex=function() return "00" end,rombank=function() return 126 end}')
    lua.execute(source[start:end])
    lua.execute('callback(false)')
    assert lua.globals().reads==0
    lua.execute('callback(true)')
    assert lua.globals().reads==1

