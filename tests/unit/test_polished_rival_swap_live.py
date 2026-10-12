"""MODEL oracle falsifiers + real TCP server/client-rig seams; never launches EmuHawk."""
from __future__ import annotations

import asyncio
import copy
import json
import random
from pathlib import Path

import pytest

from tools.polished_live import rival_swap_live as r


def hello():
    from tests.unit.test_polished_write_path import live_mon
    mons = [live_mon(random.Random(0), 25), live_mon(random.Random(1), 19)]
    blobs = [r.pc.encode_party_blob(dict(m, ot_raw_hex=r.pc.encode_text("B", 11).hex(),
                                       nickname_raw_hex=r.pc.encode_text("PARTNER", 11).hex())).hex() for m in mons]
    return {"party": [{"blob_hex": b} for b in blobs]}


def model_evidence():
    h = hello()
    raw, spans = r.expected_plan([m["blob_hex"] for m in h["party"]])
    cmd = {"cmd": "replace_rival_team", "source": "auto", "trainer_id": 0x1B03,
           "n": len(raw), "blobs_hex": [m["blob_hex"] for m in h["party"]]}
    ack = {"event": "rival_team_replaced", "player": "a", "trainer_id": 0x1B03, "species_ids": [b[0] for b in raw]}
    trace = []
    def add(kind, **kw):
        trace.append(dict(kind=kind, ord=len(trace)+1, frame=100, **kw))
    binding = {"fixture_sha256": "a"*64, "route_sha256": "b"*64, "disclosure_sha256": "c"*64}
    add("start", setup="SYNTH", rom_sha1=r.SHA1, **binding)
    add("command", msg=cmd)
    before = bytes([0x55])*32768
    add("writer_begin", bank=15, pc=0x47DD, side=1, mode=2, trainer=0x1B03, wram=before.hex(),
        declared_spans=[[s["addr"], len(s["bytes"])] for s in spans])
    after = bytearray(before)
    for span in spans:
        for i, value in enumerate(span["bytes"]):
            addr = span["addr"]+i
            add("write", addr=addr, value=value, api="write_u8", domain="System Bus", pc=0x47DD, bank=15)
            after[addr-0xC000] = value
    add("writer_end", wram=after.hex(), ok=True)
    add("post", bank=15, pc=0x480D, side=1, trainer=0x1B03, image=[s["bytes"] for s in spans])
    mon = r.pc.decode_party_blob(raw[0])
    menu_bank, menu_pc = r.symbols()["LoadBattleMenu"]
    add("continued", bank=menu_bank, pc=menu_pc, trainer=0x1B03, mode=2,
        enemy={"species": mon["species_id"], "level": mon["level"], "moves": mon["moves"], "hp": mon["hp"]})
    add("reply", msg=ack)
    add("final", completed=True, writes=sum(len(s["bytes"]) for s in spans), overflow=0)
    wire = [{"dir": "s2c", "msg": {"commands": [cmd]}}, {"dir": "c2s", "msg": ack}]
    return trace, wire, h, binding


def test_complete_model_recording_passes():
    assert r.evaluate(*model_evidence()) == ("PASS", [])


def test_zero_hit_is_open_and_input_binding_cannot_be_forged():
    trace, _, h, binding = model_evidence()
    trace = [trace[0], dict(trace[-1], ord=2, writes=0)]
    assert r.evaluate(trace, [], h, binding) == ("OPEN", ["NO_SWAP"])
    trace[0]["fixture_sha256"] = "0"*64
    assert r.evaluate(trace, [], h, binding)[0] == "FAIL"


@pytest.mark.parametrize("fault,reason", [
    ("reply", "REPLY"), ("species", "NATIVE_CONTINUATION"), ("outside", "WRITE_PLAN"),
    ("second", "SECOND_WRITE"), ("post", "POST_IMAGE"), ("delta", "OUTSIDE_SPAN_OR_WRONG_IMAGE"),
    ("truncate", "INCOMPLETE"), ("overflow", "CENSORED"), ("late", "COMMAND_TOO_LATE"),
    ("wrong-bank", "OPERATION_PREDICATE"), ("server", "SERVER_COMMAND"),
], ids=["no-reply", "wrong-species", "outside-write", "second-write", "wrong-post", "outside-delta",
        "truncated", "overflow", "late-command", "wrong-bank", "forged-command"])
def test_oracle_red_controls(fault, reason):
    trace, wire, h, binding = model_evidence()
    def row(kind):
        return next(e for e in trace if e["kind"] == kind)
    if fault == "reply":
        wire.pop()
    elif fault == "species":
        row("continued")["enemy"]["species"] += 1
    elif fault == "outside":
        row("write")["addr"] = 0xC100
    elif fault == "second":
        trace.insert(-1, copy.deepcopy(row("writer_begin")))
    elif fault == "post":
        row("post")["image"][0][0] ^= 1
    elif fault == "delta":
        b = bytearray.fromhex(row("writer_end")["wram"])
        b[0] ^= 1
        row("writer_end")["wram"] = b.hex()
    elif fault == "truncate":
        trace.pop()
    elif fault == "overflow":
        row("final")["overflow"] = 1
    elif fault == "late":
        row("command")["ord"] = row("writer_end")["ord"]
    elif fault == "wrong-bank":
        row("writer_begin")["bank"] = 14
    elif fault == "server":
        wire[0] = {"dir": "s2c", "msg": {"commands": []}}
    verdict, reasons = r.evaluate(trace, wire, h, binding)
    assert verdict == "FAIL" and reason in reasons


@pytest.mark.parametrize("bad", [None, {}, [], [7]], ids=["null", "object", "empty", "nonobject"])
def test_malformed_never_passes(bad):
    assert r.evaluate(bad, [], hello(), {})[0] == "FAIL"


def test_source_copy_oracle_mutant_is_caught():
    source = Path(r.__file__).read_text()
    old = 'post.get("image") != [s["bytes"] for s in spans]'
    assert source.count(old) == 1
    scope = {"__file__": r.__file__, "__name__": "mutant"}
    exec(compile(source.replace(old, "False"), r.__file__, "exec"), scope)
    trace, wire, h, binding = model_evidence()
    next(e for e in trace if e["kind"] == "post")["image"][0][0] ^= 1
    with pytest.raises(AssertionError):
        assert scope["evaluate"](trace, wire, h, binding)[0] == "FAIL"


def test_real_fixture_partner_is_distinct_and_raw_records_preserved():
    if not r.FIXTURE.exists():
        pytest.skip("SYNTH rival fixture absent")
    h, d = r.partner_from_save(r.FIXTURE.read_bytes())
    assert h["trainer_name"] == "RivalB" and h["ot_id"] == 53699
    assert d["input_sha256"] != d["output_sha256"] and "SYNTH" in d["statement"]
    ds = r.duo._derive_save()
    data = r.FIXTURE.read_bytes()
    for slot, m in enumerate(h["party"]):
        old, new = ds._mon(data, ds.MAIN, slot), r.pc.decode_party_blob(bytes.fromhex(m["blob_hex"]))
        for field in ("species_id", "level", "hp", "moves", "stats", "form"):
            assert old[field] == new[field]


def test_real_server_tcp_auto_command_from_scripted_partner(tmp_path):
    if not r.FIXTURE.exists():
        pytest.skip("SYNTH rival fixture absent")
    from server.server import SLinkServer
    h, _ = r.partner_from_save(r.FIXTURE.read_bytes())
    (tmp_path/"rom_contract.json").write_text(json.dumps(r.duo.contract_for(r.SHA1)))
    async def exercise():
        server = SLinkServer(data_dir=str(tmp_path), rival_team_swap=True)
        listener = await asyncio.start_server(server.handle_client, "127.0.0.1", 0)
        port = listener.sockets[0].getsockname()[1]
        partner = await asyncio.to_thread(r.Partner, port, h)
        try:
            await asyncio.to_thread(partner.send, "hello")
            assert len(server.state.partner_blobs["b"]) == len(h["party"])
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            a = dict(h, player="a", ot_id=123, trainer_name="MODEL-A", party=[])
            for message in (a, {"event": "trainer_battle_start", "player": "a", "seq": 2, "trainer_id": 0x1B03}):
                writer.write((json.dumps(message)+"\n").encode())
                await writer.drain()
                response = json.loads(await reader.readline())
            commands = [c for c in response["commands"] if c["cmd"] == "replace_rival_team"]
            assert len(commands) == 1 and commands[0]["source"] == "auto"
            assert commands[0]["blobs_hex"] == [m["blob_hex"] for m in h["party"]]
            writer.close()
            await writer.wait_closed()
        finally:
            partner.close()
            listener.close()
            await listener.wait_closed()
    asyncio.run(exercise())


def test_actual_composed_client_consumes_server_generated_command(tmp_path):
    from server.server import SLinkServer
    from tests.unit import test_polished_rival_path as rp
    rig = rp.ready(fresh=True)
    rp.enter_battle(rig, rp.party(), mode=2)
    for label, value in (("hBattleTurn", 1), ("wOtherTrainerClass", 0x1B), ("wOtherTrainerID", 3), ("wCurOTMon", 255)):
        rig.put(label, value)
    rig.frame(3)
    announcement = rig.sent("trainer_battle_start")[-1]
    server = SLinkServer(data_dir=str(tmp_path), rival_team_swap=True)
    from server.adapters.gen2_polished import Gen2PolishedAdapter
    server.state.adapter = Gen2PolishedAdapter(artifact_kind="overlay")
    h = hello()
    server.state._ingest_party_blobs("b", h["party"])
    commands = server.state.handle_event("a", announcement)
    assert len(commands) == 1 and commands[0]["source"] == "auto"
    command = commands[0]
    rig.client.handle_command(rig.client, rig.lua.table_from(dict(command, blobs_hex=rig.lua.table_from(command["blobs_hex"]))))
    rig.frame(1)
    rig.put("wCurOTMon", 0)
    rig.put("wCurPartyMon", 0)
    for i, b in enumerate(rp.overlay()[1][15*0x4000+0x7DD:15*0x4000+0x7DD+3]):
        rig.mem[0x47DD+i] = b
    rp.fire_native_rival(rig)
    rp.fire_native_rival(rig)
    rig.frame(1)
    _, spans = r.expected_plan(command["blobs_hex"])
    assert [(w["addr"], w["value"]) for w in rig.writes()] == [(s["addr"]+i, b) for s in spans for i, b in enumerate(s["bytes"])]
    assert len(rig.sent("rival_team_replaced")) == 1 and not rig.sent("rival_team_replaced")[0].get("error")


def test_dry_run_has_no_launch_or_staging(tmp_path, monkeypatch, capsys):
    if not r.FIXTURE.exists():
        pytest.skip("SYNTH inputs absent")
    original = r.subprocess.Popen
    def read_only_git(cmd, **kwargs):
        assert cmd == ["git", "rev-parse", "HEAD"], "server/emulator launch on dry-run"
        return original(cmd, **kwargs)
    monkeypatch.setattr(r.subprocess, "Popen", read_only_git)
    out = tmp_path/"must-not-exist"
    route = tmp_path/"native-input-model.json"
    route.write_text(json.dumps({"steps": [{"frames": 1, "buttons": []}]}))
    assert r.main(["--dry-run", "--out", str(out), "--route", str(route)]) == 0
    assert not out.exists() and '"setup": "SYNTH"' in capsys.readouterr().out


@pytest.mark.parametrize("change", ["field", "button", "duration", "cap"], ids=["hidden-field", "nonbutton", "negative", "over-cap"])
def test_route_refuses_before_launch(tmp_path, monkeypatch, change):
    if not r.FIXTURE.exists():
        pytest.skip("SYNTH inputs absent")
    monkeypatch.setattr(r.subprocess, "Popen", lambda *a, **k: pytest.fail("launch with invalid route"))
    step = {"buttons": [], "frames": 1}
    if change == "field":
        step["write"] = "forbidden"
    elif change == "button":
        step["buttons"] = ["Warp"]
    elif change == "duration":
        step["frames"] = -1
    else:
        step["frames"] = 60001
    path = tmp_path/"route.json"
    path.write_text(json.dumps({"steps": [step]}))
    with pytest.raises(ValueError):
        r.main(["--dry-run", "--route", str(path)])


def test_offline_rejudge_does_not_change_original_files(tmp_path, capsys):
    trace, wire, h, binding = model_evidence()
    (tmp_path/"wire").mkdir()
    for file, value in (("trace.json", trace), ("partner.json", h), ("input.json", binding)):
        (tmp_path/file).write_text(json.dumps(value))
    (tmp_path/"wire/wire_a.jsonl").write_text("\n".join(json.dumps(row) for row in wire))
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert r.main(["--rejudge", str(tmp_path)]) == 0
    assert '"analysis": "OFFLINE"' in capsys.readouterr().out
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


def test_server_launch_option_and_lua55_compile():
    cmd = r.server_command(1, 2, "data", "wire")
    assert "--rival-team-swap" in cmd and cmd[cmd.index("-m")+1] == "server.server"
    from lupa.lua55 import LuaRuntime
    lua = LuaRuntime()
    source = Path(r.__file__).with_suffix(".lua").read_text()
    assert lua.eval("function(s) return assert(load(s)) ~= nil end")(source)


def test_lua_recorder_producer_shape_reaches_oracle(tmp_path):
    """MODEL APIs, real recorder Lua/JSON: catches recorder/oracle disagreements."""
    from lupa.lua55 import LuaRuntime
    lua = LuaRuntime(unpack_returned_tuples=True)
    trace, wire, h, binding = model_evidence()
    raw, spans = r.expected_plan([m["blob_hex"] for m in h["party"]])
    sym = r.symbols() | {"hROMBank": [0, 0xFF87], "wBattleMode": [1, 0xD000]}
    config = dict(binding, spans=spans, steps=[{"frames": 1, "buttons": []}], frames=1)
    lua.globals().config_json = json.dumps(config)
    lua.globals().symbols_json = json.dumps(sym)
    lua.globals().command_json = json.dumps(wire[0]["msg"])
    lua.globals().reply_json = json.dumps(wire[1]["msg"])
    lua.globals().enemy_json = json.dumps(next(t for t in trace if t["kind"] == "continued")["enemy"])
    lua.globals().run_path = str(tmp_path).replace("\\", "/")
    lua.globals().repo_path = str(r.ROOT).replace("\\", "/")
    lua.globals().rom_sha = r.SHA1
    lua.execute(r'''
        local original_dofile=dofile
        local J=original_dofile(repo_path..'/lua/json_codec.lua')
        local cfg=J.decode(config_json)
        local syms=J.decode(symbols_json)
        local enemy=J.decode(enemy_json)
        local ram,frame,pc={},0,0x47DD
        memory={getmemorydomainsize=function() return 32768 end,
          read_u8=function(a,d) return ram[a] or 0 end,
          write_u8=function(a,v,d) ram[a-0xC000]=v end}
        emu={framecount=function() return frame end,getregister=function() return pc end}
        gameinfo={getromhash=function() return rom_sha:upper() end}
        client={speedmode=function() end}
        local hooks={}
        local L={ROOT=repo_path,RUN=run_path,json=J,SYM=syms}
        L.slurp=function() return config_json end
        L.rombank=function() return 15 end
        L.bus=function() return 1 end
        L.rw=function(name,offset)
          if name=='wBattleMode' then return 2 end
          if name=='wOtherTrainerClass' then return 0x1B end
          if name=='wOtherTrainerID' then return 3 end
          if name=='wEnemyMonSpecies' then return enemy.species%256 end
          if name=='wEnemyMonForm' then return enemy.species>255 and 0x20 or 0 end
          if name=='wEnemyMonLevel' then return enemy.level end
          if name=='wEnemyMonHP' then return offset==1 and enemy.hp%256 or math.floor(enemy.hp/256) end
          error(name)
        end
        L.wbytes=function() return enemy.moves end
        L.hex=function(t) local s={} for _,b in ipairs(t) do s[#s+1]=string.format('%02x',b) end return table.concat(s) end
        L.hook_at=function(n,b,a,f) hooks[n]=f end
        L.hook=function(n,f) hooks[n]=f end
        L.check=function(_,ok,why) assert(ok,why) end
        L.finish=function() end
        local writer={write_enemy_party=function()
          for _,span in ipairs(cfg.spans) do for i,b in ipairs(span.bytes) do memory.write_u8(span.addr+i-1,b,'System Bus') end end
        end}
        local ranges=function()
          local list={} for _,s in ipairs(cfg.spans) do list[#list+1]={s.addr,#s.bytes} end return list
        end
        SLINK_GEN2_PARTS={pack='polished_crystal',qualification='DEV_OVERLAY_SHA1',battle={rival={writes=writer,ranges=ranges}}}
        package.loaded.connector={send=function() end,receive=function() return command_json end}
        L.frame=function()
          frame=frame+1
          if frame==1 then
            package.loaded.connector.receive()
            writer:write_enemy_party({}, {})
            pc=0x480D; hooks.swap_post(true)
            pc=syms.LoadBattleMenu[2]; L.rombank=function() return syms.LoadBattleMenu[1] end
            hooks.LoadBattleMenu(true)
            package.loaded.connector.send(reply_json)
          end
        end
        os.getenv=function(n) return ({SLINK_ROOT=repo_path,POL_SWAP_CONFIG='model',SLINK_HOST='localhost',SLINK_PORT='1'})[n] end
        dofile=function(path)
            if path:match('pol_lib.lua$') then return L end
            if path:match('recording_guard.lua$') then return original_dofile(path) end
            assert(path:match('/lua/slink.lua$'))
        end
    ''')
    lua.execute(Path(r.__file__).with_suffix(".lua").read_text())
    recorded = json.loads((tmp_path/"trace.json").read_text())
    assert len(raw) == 2
    assert r.evaluate(recorded, wire, h, binding) == ("PASS", [])
