-- scenario_gen3_linked_faint_active.lua — the in-battle ACTIVE faint, mechanism P+H.
--
-- Owner rulings 15-18 (docs/gen3/G4_request_draft.md §6): when the linked partner dies while our
-- linked mon is the ACTIVE battler, the client commits Perish counter 0 + a no-op action and
-- ends the plan with the controller hand-off (gBattlerControllerFuncs[0] =
-- PlayerBufferExecCompleted), so the engine's own Perish KO faints the mon IN BATTLE with NO
-- button press. Spec: docs/gen3/research/active_faint_in_battle_scope_2026-09-23.md §2d/§5 (O1-O7)
-- and rr_active_faint_parity_scope_2026-09-23.md §3.2/§5.5 (the hand-off oracle, R1-R5).
--
-- The SUBJECT parks with its linked lead as battler 0 (READY_ACTIVE), gets force_faint, and
-- presses NOTHING from READY_ACTIVE until the KO. The carrier then reads the engine, never the
-- client's own word:
--   commit   5 `battle_commit` write lines on one frame, the last the 4-byte controller slot;
--            the entry reads perish/handoff/"active faint committed"; gStatuses3[0] & 0x20 set
--   hand-off the slot goes PlayerBufferExecCompleted -> PlayerBufferRunCommand (RR: or CFRU's
--            bit-24 successor) within 2 frames with exec bit 0 clear                      (R1)
--   window   no harness press and gMain.heldKeysRaw == 0 on every frame (R3 inverts: pulse L)
--   KO       gBattleMons[0].hp == 0 with battler 0 still the linked slot and in battle; Perish
--            flag cleared (O5); PP never dropped and lastUsedMovePlayer unchanged (O3); no
--            SLink write ever touched an HP word (O4)
--   site     the faint site fires with gActiveBattler 0, battler 0 = the slot, both HP words 0
--            and playerFaintCounter = commit + 1 (O1, O2, O4)
--   after    wild/trainer: the send-out puts another slot in (O6); whiteout: outcome LOST plus
--            TX whiteout (O6); the subject never sends `faint` for the key (O7)
--
-- D.active_faint_case (tools/e2e_duo.py SCENARIOS):
--   nil/"wild"  A1: B hunts on the Route 1 grass. A receives no SLink force command;
--               the read-only FORCED_HP0 watcher observes in-battle HP0, then the engine faint site.
--               The HP0 marker alone does not establish the cause.
--   "whiteout"  B deposits its slot-1 mon at the Viridian PC first (normal inputs), so the linked
--               lead is its only mon: the Perish KO whites out (ruling 18: P is not held). The run
--               is over (the only pair died), so B keeps its last mon: memorialize is dropped.
--   "trainer"   B takes the T2 route to Bug Catcher Rick 102 (lua/tests/gen3_routes.lua); the
--               forced party screen follows the KO.
--   "command"   A2 (active_end_gen3): no pair death. A is the subject; the runner queues
--               force_faint to A after READY_ACTIVE; B idles and never saves. A sends out slot 1,
--               RUNs, and saves the engine-written HP 0.
--   "lhammer"   RR R3: as wild, but B pulses L from the commit to the KO -- pressed while the
--               game last READ L released (gMain.heldKeysRaw, bit 0x200), released once it read it
--               pressed, so every game read sees a fresh edge even on an overrunning main loop --
--               and releases at the KO. The pre-commit input guard stays on. The POKe BALLS count
--               and CFRU's ball-id byte 0x0203AD30 must not change.
--   "mega"      RR R5: a signed G5 limit (owner ruling 20): no RR trainer route, no mega-capable
--               party; the runner SKIPs it and this case refuses by name.
--   "explode"   explode_gen3 (RR, --explode-mode; owner ruling 19): the server sends
--               force_explode, and the client's Explosion menu skip ends in the SAME hand-off, so
--               the same observer applies: commit (>= 2 lines, last the controller slot), hand-off,
--               no input, then the engine's own Explosion stamps lastUsedMovePlayer 153 and the
--               attacker faints (setatkhptozero -> tryfaintmon: the faint site, counter +1).
--               No Perish flag and no PP check (the plan rewrites the moves and PP).
-- A linked mon that is its party's LAST mon (a one-mon fixture: RR's rr_battle.sav, or the
-- whiteout case) keeps its slot after game_over: its memorialize is dropped, logged LAST_MON_KEPT.
local fmt = string.format
local SLOT, BENCH = 0, 1
local OUTCOME_LOST, OUTCOME_RAN = 2, 4 -- include/constants/battle.h
local PLAN_ENTRIES = 5                 -- status3, perish timer, chosen action, comm, hand-off
local RR_BALL_ID = 0x0203AD30          -- rr_active_faint_parity_scope §3.1 (the L-throw's store)
local MOVE_EXPLOSION = 153             -- pret include/constants/moves.h
local L_BIT = 0x200                    -- KEYINPUT L (pret include/gba/io_reg.h L_BUTTON)

local function hexbytes(t) local o = {} for i, v in ipairs(t) do o[i] = fmt("%d", v) end return table.concat(o, "/") end

--- The engine observer, installed at READY_ACTIVE. Returns the state table the scenario polls.
local function observe(ctx, key, slot, hammer, explode, case)
    local o = {}
    local H = ctx.handoff
    local PERISH = ctx.perish_mask or 0x20
    local perish_label = ctx.perish_label or "gStatuses3[0]"
    local w0, att0, in0, site0 = #ctx.write_lines(), ctx.attempted(), ctx.inputs(), #ctx.faint_sites()
    local hp_addr = ctx.hp_addrs(slot)
    local l_held = false
    local function set_l(held)
        ctx.press(held and { L = true } or {})
        l_held = held
    end
    local function fail(s)
        if hammer and l_held then set_l(false) end
        o.fail = o.fail or s
    end
    local function successor(v) for _, t in ipairs(H.to) do if v == t then return true end end end
    -- R1 L3: every count the receipt prints is MEASURED here, never a literal
    local function hp_writes()
        local n, lines = 0, ctx.write_lines()
        for i = w0 + 1, #lines do if hp_addr[lines[i].address] then n = n + 1 end end
        return n
    end
    ctx.watch(function()
        if o.fail or o.done then return true end
        local s = ctx.engine_sample(slot)
        local lines = ctx.write_lines()
        for i = w0 + 1, #lines do
            if hp_addr[lines[i].address] then return fail(fmt("SLink wrote an HP word 0x%08X", lines[i].address)) end
        end
        if o.commit and not o.ko then
            o.keys = o.keys | s.keys
            if not hammer and o.keys ~= 0 then
                return fail(fmt("input between the commit and the KO (keys=0x%X presses=%d)",
                                o.keys, ctx.inputs() - in0))
            end
        end
        if not o.commit then
            if s.keys ~= 0 or ctx.inputs() ~= in0 then return fail("input before the commit") end
            local c = {}
            for i = w0 + 1, #lines do if lines[i].reason == "battle_commit" then c[#c + 1] = lines[i] end end
            if #c == 0 then
                if not s.in_battle or s.battle_hp == 0 then return fail("the battle or the mon ended before any commit") end
                o.base = s                                   -- the last parked frame before the commit
                return
            end
            local last, e = c[#c], ctx.battle_hold(key)
            if (explode and #c < 2) or (not explode and #c ~= PLAN_ENTRIES) then
                return fail(fmt("commit wrote %d lines, not %s", #c, explode and ">= 2" or PLAN_ENTRIES))
            end
            for _, l in ipairs(c) do if l.frame ~= c[1].frame then return fail("the commit spans frames") end end
            if last.address ~= H.slot_addr or last.len ~= 4 then return fail("the commit does not end in the hand-off") end
            local cmd = explode and "force_explode" or "force_faint"
            if ctx.received(cmd, key) == 0 then return fail("commit without a " .. cmd .. " RX") end
            if s.ctrl0 ~= H.from and not successor(s.ctrl0) then
                return fail(fmt("controller slot 0x%08X after the commit", s.ctrl0))
            end
            if explode then
                if not (e and e.explode and e.why == "explosion committed") then
                    return fail("the entry is not a committed Explosion: why=" .. tostring(e and e.why))
                end
                for i = 1, 4 do
                    if s.pp[i] ~= 5 then return fail("Explosion commit PP was not initialized to 5") end
                end
            else
                if s.status3 & PERISH == 0 then return fail(perish_label .. " lacks the Perish flag after the commit") end
                if not (e and e.perish and e.handoff and e.why == "active faint committed") then
                    return fail("the entry is not a handed-off Perish commit: why=" .. tostring(e and e.why))
                end
            end
            o.base = o.base or s
            o.keys = s.keys                               -- 0: the pre-commit guard ran this frame
            o.commit = { frame = s.frame, attempted = ctx.attempted() - att0, ctrl = s.ctrl0,
                         pp = {s.pp[1], s.pp[2], s.pp[3], s.pp[4]} }
            ctx.log(fmt('ACTIVE_COMMIT %s frame=%d writes=%d attempted=%d handoff=1 status3=0x%X ctrl=0x%08X '
                        .. 'counter=%d last_move=%d pp=%s why="%s" case=%s', key, s.frame, #c, o.commit.attempted,
                        s.status3, s.ctrl0, o.base.counter, o.base.last_move, hexbytes(o.base.pp), e.why, case))
            if hammer then set_l(true) end
            return
        end
        if not o.handoff then
            if successor(s.ctrl0) then
                o.handoff = s.frame - o.commit.frame
                local bit0 = s.exec & 1
                ctx.log(fmt("HANDOFF %s from=0x%08X to=0x%08X frames=%d exec_bit0=%d", key, H.from, s.ctrl0,
                            o.handoff, bit0))
                if bit0 ~= 0 then return fail("exec bit 0 still set at the hand-off") end
            elseif s.ctrl0 ~= H.from or s.frame - o.commit.frame > 2 then
                return fail(fmt("no hand-off successor within 2 frames (ctrl=0x%08X)", s.ctrl0))
            end
        end
        if not o.ko then
            if not hammer and (s.keys ~= 0 or ctx.inputs() ~= in0) then
                return fail(fmt("input between the commit and the KO (keys=0x%X presses=%d)", s.keys, ctx.inputs() - in0))
            end
            if not explode then
                for i = 1, 4 do if s.pp[i] < o.base.pp[i] then return fail("PP dropped: the mon acted") end end
            end
            if s.battler0_slot ~= slot then return fail("battler 0 left the linked slot before the KO") end
            if not s.in_battle then return fail("the battle ended before the KO") end
            if s.battle_hp > 0 then
                if hammer then set_l((s.keys & L_BIT) == 0) end   -- press on a read release, release on a read press
                return
            end
            if hammer then set_l(false) end
            if explode then
                if s.last_move ~= MOVE_EXPLOSION then return fail("the KO came without the Explosion action") end
                local dropped = false
                for i = 1, 4 do
                    if s.pp[i] < 0 or s.pp[i] > o.commit.pp[i] then return fail("Explosion PP readback invalid") end
                    if s.pp[i] < o.commit.pp[i] then dropped = true end
                end
                if not dropped then return fail("PP did not drop for Explosion") end
                ctx.log(fmt("EXPLOSION_PP %s committed=%s ko=%s", key, hexbytes(o.commit.pp), hexbytes(s.pp)))
            else
                if s.status3 & PERISH ~= 0 then return fail("the Perish flag is still set at the KO") end
                if not ctx.rr and s.last_move ~= o.base.last_move then return fail("lastUsedMovePlayer moved: the mon acted") end
            end
            o.ko = s.frame
            ctx.log(fmt("ACTIVE_KO %s frame=%d in_battle=1 battle_hp=0 status3=0x%X pp=%s last_move=%d inputs=%d "
                        .. "keys=0x%X hp_writes=%d attempted=%d case=%s", key, s.frame, s.status3, hexbytes(s.pp),
                        s.last_move, ctx.inputs() - in0, o.keys, hp_writes(), ctx.attempted() - att0, case))
        end
        if not o.site then
            local sites = ctx.faint_sites()
            for i = site0 + 1, #sites do
                local f = sites[i]
                if f.active == 0 and f.battler0_slot == slot then
                    if f.battle_hp ~= 0 or f.party_hp ~= 0 then return fail("the faint site saw a non-zero HP word") end
                    if f.counter ~= (o.base.counter + 1) % 256 then
                        return fail(fmt("playerFaintCounter %d -> %d, not +1", o.base.counter, f.counter))
                    end
                    o.site = f.frame
                    ctx.log(fmt("ACTIVE_FAINT_SITE %s frame=%d active=0 battler0_slot=%d battle_hp=0 party_hp=0 counter=%d->%d",
                                key, f.frame, slot, o.base.counter, f.counter))
                end
            end
        end
        if s.in_battle then
            if s.outcome ~= 0 then o.outcome = s.outcome end
            if s.battler0_slot ~= slot then o.sent_out = o.sent_out or s.battler0_slot end
        else
            o.done = true
        end
    end)
    return o
end

--- The engine-side check of R3's L presses: the ball count and CFRU's ball-id byte.
local function balls(ctx) return ctx.balls(), ctx.peek_u8(RR_BALL_ID) end

local function one_mon_party(ctx, key)
    -- ruling 18: build the one-mon party by normal inputs (PC deposit of the slot-1 mon)
    local bench = (ctx.party() or {})[BENCH + 1]
    if not bench or bench.key == key then return false, "no slot-1 mon to deposit" end
    ctx.walk_to_pc("linked_faint_active whiteout", {heal_before_deposit = true})
    local gone, why = ctx.pc_deposit("linked_faint_active whiteout deposit")
    if gone ~= bench.key then return false, "the deposit moved " .. tostring(gone or why) end
    if not ctx.observe_boxed(bench.key) then return false, bench.key .. " was never read back boxed" end
    if #(ctx.party() or {}) ~= 1 then return false, "the party is not the linked lead alone" end
    ctx.log(fmt("ONE_MON_PARTY %s deposited=%s", key, bench.key))
    ctx.walk_pc_to_grass("linked_faint_active whiteout")
    return true
end

--- After the battle: the linked key leaves for the memorial box -- or, when it is its party's
--- only mon, game_over (the only pair is dead) makes lua/core/deferred.lua drop its memorialize.
local function settle_memorial(ctx, key)
    local party = ctx.party() or {}
    if #party == 1 and party[1].key == key then
        if not ctx.wait_until(function()
            return ctx.received("game_over") > 0 and ctx.received("memorialize", key) > 0
                   and not ctx.queued("memorialize", key)
        end, 600, "game_over and the dropped last-mon memorialize") then
            return false, "the last-mon memorialize was never settled after game_over"
        end
        ctx.log("LAST_MON_KEPT " .. key)
        return true
    end
    if not ctx.wait_sent("memorialize_done", key, 600) then return false, "no memorialize_done for " .. key end
    return true
end

local function enter(ctx, key, case)
    if case == "whiteout" and #(ctx.party() or {}) == 1 then
        -- a one-mon fixture (RR rr_battle.sav) is already the lone-lead party: no PC trip
        ctx.log(fmt("ONE_MON_PARTY %s deposited=-", key))
    elseif case == "whiteout" then
        -- the walks FLEE every incidental battle: the lone lead must reach the parked battle
        -- alive (W3: a fought Route 1 encounter whited it out before READY_ACTIVE)
        if type(ctx.flee_incidentals) ~= "function" then return false, "missing flee_incidentals seam" end
        local ok, res = ctx.flee_incidentals("linked_faint_active whiteout", function()
            return table.pack(one_mon_party(ctx, key))
        end)
        if not ok then
            local e = type(res) == "table" and (res.whiteout and "whited out" or "table error") or tostring(res)
            return false, "one-mon party walk: " .. e
        end
        if not res[1] then return false, res[2] end
    elseif case == "trainer" then
        if type(ctx.enter_trainer) ~= "function" then return false, "missing enter_trainer route seam" end
        local party = ctx.party() or {}
        local lead, bench = party[1], party[BENCH + 1]
        if not (lead and bench) then return false, "the trainer route needs a lead and a slot-1 mon" end
        local before = lead.level
        local ok, budget = pcall(ctx.preparation_budget, lead, 13)
        if not ok then return false, "PREPARATION budget: " .. tostring(budget) end
        -- the route's per-frame guard protects the BENCH (the send-out replacement), unchanged
        local entered, why = ctx.enter_trainer("linked_faint_active trainer", 102,
            { level_floor = 13, max_frames = budget, target_key = bench.key, target_slot = BENCH,
              target_hp = bench.hp })
        if not entered then return false, "trainer route: " .. tostring(why) end
        local after = (ctx.party() or {})[1]
        if not after or after.level < 13 then return false, "PREPARATION did not reach the level floor" end
        ctx.log(fmt("PREP_LEVEL before=%d after=%d floor=13", before, after.level))
        return true
    elseif case == "mega" then
        return false, "SIGNED LIMIT R5 (owner ruling 20): no RR trainer route and no mega-capable party"
    end
    if not ctx.hunt("linked_faint_active " .. case) then return false, "no wild encounter" end
    return true
end

local function subject(ctx, key, case)
    local ok, why = enter(ctx, key, case)
    if not ok then return false, why end
    if ctx.SP.verify_fight_cursor(ctx.cp, "incidental_battle") ~= "fight" then
        return false, "never reached the action menu"
    end
    if ctx.battler_slot() ~= SLOT then return false, "the linked lead is not battler 0" end
    ctx.frames(1)                                        -- the menu mash's last press is released
    local hammer, explode = case == "lhammer", case == "explode"
    local cmd = explode and "force_explode" or "force_faint"
    local b0, id0 = balls(ctx)
    local o = observe(ctx, key, SLOT, hammer, explode, case)
    ctx.log(fmt("READY_ACTIVE %s case=%s", key, case))
    if not ctx.wait_received(cmd, key, ctx.D.timeout_secs or 1500) then
        return false, "no " .. cmd .. " for " .. key
    end
    -- no input until the KO (the window the observer polices); the faint site after it may sit
    -- behind battle text that waits for a press -- RR explode_gen3 at 97672e6d: KO at frame 13308
    -- with lastUsedMovePlayer 153, then no site for 600 s with nothing pressed. So: 5 s hands-off,
    -- then A on the 16-frame cadence until the site (A on "Use next POKeMON?" is the YES the
    -- send-out below takes anyway).
    ctx.wait_until(function() return o.fail or o.ko end, 600, "the engine KO")
    if o.ko and not o.site then
        ctx.wait_until(function() return o.fail or o.site end, 5, "the faint site (hands-off)")
        if not (o.fail or o.site) then
            ctx.mash_until(function() return o.fail or o.site end, 120, "A")
        end
    end
    if o.fail then return false, o.fail end
    if not o.commit then return false, "the client never committed the hand-off plan" end
    if not o.handoff then return false, "the controller hand-off never ran" end
    if not o.ko then return false, "no in-battle engine KO witnessed" end
    if not o.site then return false, "the KO's faint site never fired" end
    if hammer then
        local b1, id1 = balls(ctx)
        if b1 ~= b0 or id1 ~= id0 then return false, fmt("L hammer lost a ball: %d/%d -> %d/%d", b0, id0, b1, id1) end
        ctx.log(fmt("LHAMMER_BALLS %s balls=%d ball_id=%d unchanged", key, b1, id1))
    end
    -- after the KO: the vanilla follow-up
    if explode then
        -- Explosion usually takes the wild foe too: whatever follows (a win, a forced send-out,
        -- a whiteout or a draw) is the scripted battle policy's
        if ctx.in_battle() then
            local fok, ferr = ctx.try(ctx.play.fight_through, ctx.cp, 4000)
            if not fok and not (type(ferr) == "table" and ferr.whiteout) then return false, "after the Explosion: " .. tostring(ferr) end
        end
    elseif case == "whiteout" then
        local fok, ferr = ctx.try(ctx.play.fight_through, ctx.cp, 4000)
        if not fok and not (type(ferr) == "table" and ferr.whiteout) then return false, "whiteout: " .. tostring(ferr) end
        if not ctx.mash_until(function() return ctx.sent("whiteout") > 0 end, 180, "A") then
            return false, "the client never sent whiteout"
        end
        if o.outcome ~= OUTCOME_LOST then return false, "outcome " .. tostring(o.outcome) .. ", not LOST" end
    else
        local turn = ctx.SP.verify_fight_cursor(ctx.cp, "incidental_battle")   -- A answers "Use next?" YES
        if turn ~= "party" then return false, "no send-out party screen after the KO (" .. tostring(turn) .. ")" end
        local sok, swhy = ctx.send_out(BENCH)
        if not sok then return false, "send-out: " .. tostring(swhy) end
        if case == "trainer" then
            local fok, ferr = ctx.try(ctx.play.fight_through, ctx.cp, 4000)
            if not fok and not (type(ferr) == "table" and ferr.whiteout) then return false, "after the send-out: " .. tostring(ferr) end
        else
            local rok, rwhy = ctx.run_away("linked_faint_active " .. case)
            if not rok then return false, rwhy end
        end
    end
    ctx.play.wait_scene_settled(ctx.cp, 3000)
    ctx.log(fmt("ACTIVE_OUTCOME %s outcome=%d sent_out=%s whiteout_tx=%d", key, o.outcome or 0,
                tostring(o.sent_out), ctx.sent("whiteout")))
    if case ~= "whiteout" and not explode and not o.sent_out then return false, "battler 0 never became another slot" end
    if case == "command" and o.outcome ~= OUTCOME_RAN then return false, "the battle did not end by RUN" end
    if ctx.sent("faint", key) ~= 0 then return false, "the client echoed faint for its own engine KO" end
    if case ~= "command" then
        local mok, mwhy = settle_memorial(ctx, key)
        if not mok then return false, mwhy end
    end
    local saved, svwhy = ctx.save("linked_faint_active")
    if not saved then return false, svwhy end
    return true, (explode and "Explode+H: engine Explosion KO" or "P+H: engine Perish KO")
                 .. " in battle with " .. (hammer and "alternating L input" or "no input")
                 .. " (" .. case .. ")"
end

local function natural(ctx, key)
    if not ctx.hunt("linked_faint_active a") then return false, "no wild encounter" end
    local fainted, why = ctx.lose_active(key, "linked_faint_active a")
    if not fainted then return false, "the linked lead did not faint: " .. tostring(why) end
    -- Completion stamp after lose_active returns; TX faint can precede this line.
    -- Only expansion requires the frame; older title receipt formats stay intact.
    if ctx.D.game == "gen3_exp" then
        ctx.log(fmt("LINKED_FAINTED %s frame=%d", key, emu.framecount()))
    else
        ctx.log("LINKED_FAINTED " .. key)
    end
    if ctx.in_battle() then
        local ok, err = ctx.try(ctx.play.fight_through, ctx.cp, 4000)
        if not ok and not (type(err) == "table" and err.whiteout) then
            return false, "after the faint: " .. tostring(err)
        end
        if ok and ctx.in_battle() then return false, "the battle never ended after the faint" end
    end
    ctx.play.wait_scene_settled(ctx.cp, 3000)
    if ctx.sent("faint", key) == 0 then return false, "the client never sent faint for " .. key end
    local mok, mwhy = settle_memorial(ctx, key)
    if not mok then return false, mwhy end
    local ok, why2 = ctx.save("linked_faint_active")
    if not ok then return false, why2 end
    return true, "natural faint of the active linked " .. key
end

local function idle(ctx)
    local text = ctx.wait_until(function() return ctx.partner_result() end, ctx.D.timeout_secs or 1800,
                                "the subject's RESULT")
    if not (text and text:find("\nRESULT: PASS", 1, true)) then return false, "the subject did not PASS" end
    return true, "idle (no save); the subject passed"
end

return function(ctx)
    local case = ctx.D.active_faint_case or "wild"
    if not ctx.wait_go(nil, ctx.D.timeout_secs or 1800) then return false, "no go-file" end
    local key = ctx.linked()
    local lead = key and ctx.find(key)
    if not lead or lead.slot ~= SLOT then return false, "the LINKED key must be the party lead" end
    if lead.hp == 0 then return false, "the fixture's linked lead is already fainted" end
    if case == "command" then
        if ctx.player == "b" then return idle(ctx) end
        return subject(ctx, key, case)
    end
    if ctx.player == "a" then return natural(ctx, key) end
    return subject(ctx, key, case)
end
