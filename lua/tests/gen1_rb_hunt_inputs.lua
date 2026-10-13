-- Pure R/B Route 1 hunt for the duo lane. From the parking tile (10,35) pace the grass column
-- until a wild battle starts, then CATCH (FIGHT once while the foe is at full HP, then
-- throw the bag's Poke Ball), RUN, or switch the linked slot in for a bounded faint window.
-- Same shape as gen1_rb_route1_inputs: point -> buttons,
-- phase; the caller owns frames. The battle itself is played through gen1_battle_driver.lua,
-- whose synchronous step(buttons) is bridged with a coroutine so it still costs one frame per
-- buttons returned from here.
--
-- Terminal phases: "caught" (party grew), "escaped" (mode "run": RUN succeeded),
-- "out-of-balls" (the throw failed, no ball left, RUN succeeded), "linked-fainted",
-- "linked-active-menu" and failures "linked-survived-3-battles", "hunt-exhausted",
-- "whiteout", "stuck".
-- The shared input shapes (idle/hold/tap/move): this file's own directory locates the module,
-- the way the sibling drivers are already loaded.
local function here() return (debug.getinfo(1, "S").source or ""):match("^@(.*[/\\])") or "" end
local C = dofile(here() .. "gen1_inputs_common.lua")
local idle, hold, tap, move = C.idle, C.hold, C.tap, C.move

local M = {}
-- The parking tile, the grass patch and the encounter bound are lane facts (P3b-e):
-- F.WAYPOINTS.HUNT.* and F.TUNING.max_encounters_hunt. (Route 1 grass at the parking tile: pret
-- maps/Route1.blk + gfx/blocksets/overworld.bst, grass tile $52 read at the half-block's
-- bottom-right tile (engine/battle/wild_encounters.asm:27-31) -> steps x 10-11, y 32-35; (10,36)
-- is the Pallet Town edge: never walk there.)
local F = dofile(here() .. "gen1_rb_facts.lua")
function M.with_facts(facts)
    assert(facts and facts.WAYPOINTS and facts.WAYPOINTS.HUNT and facts.TUNING, "hunt needs a facts table")
    F = facts
    M.PARK = facts.WAYPOINTS.HUNT.park
    M.GRASS = facts.WAYPOINTS.HUNT.grass
    M.MAX_ENCOUNTERS = facts.TUNING.max_encounters_hunt
    return facts
end
M.with_facts(F)
-- One ball id test for every scan below: F.ITEM.BALL_IDS is the ItemUseBall dispatch set
-- (MASTER..POKE on vanilla, plus HYPER_BALL on pureRGB), so a scan can never miss a ball the
-- player can throw.
function M.is_ball(id)
    for _, b in ipairs(F.ITEM.BALL_IDS) do if id == b then return true end end
    return false
end


-- Fields gen1_scripted_play's point lacks: the foe's HP for the fight/throw decision and the
-- bag index of the first ball (F.ITEM.BALL_IDS, the ItemUseBall dispatch set).
-- rd(addr) reads one System Bus byte; symbols is the title's pret .sym table.
function M.extend_point(point, rd, symbols)
    local function u16(name) local a = assert(symbols[name], name); return rd(a) * 256 + rd(a + 1) end
    point.enemy_hp, point.enemy_max_hp = u16("wEnemyMonHP"), u16("wEnemyMonMaxHP")
    point.ball_index = nil
    local n = rd(assert(symbols.wNumBagItems))
    for i = 0, math.min(n, 20) - 1 do
        local id = rd(symbols.wBagItems + 2 * i)
        if M.is_ball(id) and point.ball_index == nil then point.ball_index = i end
    end
    return point
end

-- HP/W gate for a Poke Ball, ItemUseBall (engine/items/item_effects.asm):
-- W = floor(floor(maxhp*255/12) / max(floor(hp/4), 1)); passes when W > 255
-- or Rand2 <= W. This remains the fight/throw threshold, not the total catch probability.
local function hp_catch_odds(hp, maxhp)
    local C = F.CATCH
    local w = math.floor(math.floor(maxhp * C.maxhp_factor / C.maxhp_divisor)
                          / math.max(math.floor(hp / C.hp_divisor), 1))
    if w >= C.sure_catch_w then return 1 end
    return (w + 1) / (C.first_rand_max + 1)
end

-- No-status Poke Ball probability: both Rand1 <= the encounter's actual catch rate
-- and the HP/W gate must pass. A low-HP foe is not a sure catch unless its rate is 255.
function M.catch_odds(hp, maxhp, catch_rate)
    assert(type(catch_rate) == "number", "observed catch rate required")
    return (catch_rate + 1) / (F.CATCH.first_rand_max + 1) * hp_catch_odds(hp, maxhp)
end

-- opts.driver   gen1_battle_driver built by the caller with a step that yields the buttons
-- opts.step     that same yielding step (for text-advancing taps between driver calls)
-- opts.rd       read_u8 on the System Bus;  opts.symbols  the title's pret .sym table
-- opts.mode     "catch" | "run" | "sacrifice" | "switch-hold"; opts.log optional line sink
-- opts.switch_slot 0-based linked party slot; opts.move_slot 1-based move to play
-- opts.start_active: switch-hold lead was selected in the overworld, so the first
-- battle menu is already the hold point; do NOT spend a switch turn.
-- opts.stop_on_first_uncaught_battle: a linked-faint prerequisite cannot use a later Route 1
-- catch after the first battle already reported no_catch and dead-zoned that area.
-- opts.fainted() read-only engine-faint receipt predicate for sacrifice mode.
function M.new(expected, opts)
    assert(expected and (expected.player == "a" or expected.player == "b"), "R/B route identity required")
    assert(opts and opts.driver and opts.step and opts.rd and opts.symbols, "hunt needs driver/step/rd/symbols")
    local D, step, rd, S = opts.driver, opts.step, opts.rd, opts.symbols
    local mode = opts.mode or "catch"
    assert(mode == "catch" or mode == "run" or mode == "sacrifice" or mode == "switch-hold",
           "mode catch|run|sacrifice|switch-hold")
    local F = M.with_facts(expected.facts or dofile(here() .. "gen1_rb_facts.lua"))
    if mode == "sacrifice" then
        assert(type(opts.move_slot) == "number" and opts.move_slot >= 1 and opts.move_slot <= 4
               and type(opts.fainted) == "function", "sacrifice needs a move slot and faint receipt")
    end
    local log = opts.log or function() end
    local self = {last_frame = -1, encounters = 0, target = 1, battle_co = nil, stage = "begin",
                  party_before = nil, terminal = nil, receipts = {}}

    local function u16(name) local a = S[name]; return rd(a) * 256 + rd(a + 1) end
    local function foe() return u16("wEnemyMonHP"), u16("wEnemyMonMaxHP") end
    local function balls()
        local n, total = rd(S.wNumBagItems), 0
        for i = 0, math.min(n, 20) - 1 do
            local id = rd(S.wBagItems + 2 * i)
            if M.is_ball(id) then total = total + rd(S.wBagItems + 2 * i + 1) end
        end
        return total
    end
    local function ball_index()
        local n = rd(S.wNumBagItems)
        for i = 0, math.min(n, 20) - 1 do
            local id = rd(S.wBagItems + 2 * i)
            if M.is_ball(id) then return i end
        end
        return nil
    end
    local function mash(btn, frames)
        for i = 1, frames do step({[btn] = i % F.TUNING.input_cadence < 2}) end
    end
    -- The next battle menu after the driver's last A press, tapping B through anything that
    -- waits for a button (level-up stats box, caught/dex screens, the nickname prompt = NO).
    local function wait_menu(budget)
        local used = 0
        while used < budget do
            local r = D.wait_menu(240)
            used = used + r.frames
            if r.ok then return "menu" end
            if r.why == "battle_over" then return "battle_over" end
            mash("B", 32); used = used + 32
        end
        return "timeout"
    end
    local function run_until_escaped()
        for _ = 1, 10 do
            local t = D.run(600)
            self.receipts[#self.receipts + 1] = "run:" .. tostring(t.why)
            if t.ok then return "ran" end
            if t.why == "battle_over" then return "battle_over" end
            if t.why ~= "battle_menu_again" then
                local m = wait_menu(1200)
                if m ~= "menu" then return m end
            end
        end
        return "stuck"
    end
    -- One battle, start to finish. Returns the outcome; the caller decides what it means.
    local function plan()
        self.stage = "intro"
        local m = wait_menu(1800)
        if m ~= "menu" then return m end
        if mode == "run" then self.stage = "run"; return run_until_escaped() end
        if mode == "switch-hold" and opts.start_active then
            self.stage = "hold"
            log("[hunt] linked lead active without a switch turn")
            return "linked-active-menu"
        end
        if mode == "sacrifice" or mode == "switch-hold" then
            -- pret engine/battle/core.asm:2329-2419: PKMN -> PartyMenuInit (row = physical
            -- slot) -> SWITCH/STATS/CANCEL. Driver.switch_to verifies Y/X and cursor.
            self.stage = "switch"
            local switched = D.switch_to(opts.switch_slot or 1, 900)
            self.receipts[#self.receipts + 1] = "switch:" .. tostring(switched.why)
            if mode == "sacrifice" and opts.fainted() then return "linked-fainted" end
            if not switched.ok then return "stuck" end
            log("[hunt] linked slot " .. tostring(opts.switch_slot or 1) .. " active")
            if mode == "switch-hold" then
                self.stage = "hold"
                m = wait_menu(1800)
                return m == "menu" and "linked-active-menu" or m
            end
            for turn = 1, 60 do
                if opts.fainted() then return "linked-fainted" end
                self.stage = "linked-turn"
                m = wait_menu(1800)
                if opts.fainted() then return "linked-fainted" end
                if m ~= "menu" then return m end
                local chosen = D.choose("FIGHT")
                if not chosen.ok then return "stuck" end
                local move = D.commit_move(opts.move_slot, 900)
                self.receipts[#self.receipts + 1] = "gentle:" .. tostring(move.why)
                log("[hunt] linked move turn " .. turn .. " -> " .. tostring(move.why))
                if opts.fainted() then return "linked-fainted" end
                if move.why == "battle_over" then return "battle_over" end
            end
            return "stuck"
        end
        local dmax = nil -- largest Tackle damage seen so far (a non-crit roll is 217-255/255 of base)
        -- Preserve the existing HP/W-only throw threshold (or throw on the last turn).
        -- Measured over 51 throws against rate-255 Route 1 foes (release runs 1-5 + every kept
        -- receipt, 2026-09-17/18): all 45 throws at HP/W odds 1.00 caught; the 6 thrown early
        -- because the next Tackle MIGHT KO the foe (hp <= dmax+1, odds 0.58-0.67) missed 4 times,
        -- and every miss ends the hunt out-of-balls -- a whole-run retry. A foe KO costs one
        -- encounter and keeps the ball (the caller's "hunt again" path).
        -- For a 13-19 HP foe the HP/W gate is certain iff hp <= 7 and is 0.53-0.79 at hp 8-11.
        -- A lower species rate only changes the logged total, never the input timing.
        for turn = 1, 4 do
            local hp, maxhp = foe()
            -- LoadEnemyMonData copies wMonHCatchRate into this byte after the base stats
            -- (engine/battle/core.asm vanilla :6107-6118, pureRGB :6510-6521).
            -- ItemUseBall reads it for Rand1 (engine/items/item_effects.asm :301 / :346).
            -- The caller already supplies the title's complete .sym table.
            local rate_address = assert(S.wEnemyMonActualCatchRate, "wEnemyMonActualCatchRate")
            local rate = rd(rate_address)
            local odds = M.catch_odds(hp, maxhp, rate)
            local throw = hp_catch_odds(hp, maxhp) >= 0.99 or turn == 4
            log(string.format("[hunt] turn %d foe hp %d/%d odds %.2f dmax %s -> %s", turn, hp, maxhp, odds,
                              tostring(dmax), throw and "throw" or "fight"))
            if throw then break end
            self.stage = "fight"
            local c = D.choose("FIGHT")
            if not c.ok then self.receipts[#self.receipts + 1] = "fight:" .. tostring(c.why); return c.why == "battle_over" and "battle_over" or "stuck" end
            local t = D.commit_move(1)
            self.receipts[#self.receipts + 1] = "move1:" .. tostring(t.why)
            if not t.ok and t.why == "battle_over" then return "battle_over" end
            m = wait_menu(1800)
            if m ~= "menu" then return m end
            local after = foe()
            local dealt = hp - after
            if dealt > 0 then dmax = math.max(dmax or 0, math.ceil(dealt * 255 / 217)) end
        end
        while true do
            local idx = ball_index()
            if not idx then self.stage = "no-balls"; return run_until_escaped() end
            self.stage = "throw"
            local t = D.use_item(idx, 600)
            self.receipts[#self.receipts + 1] = "throw:" .. tostring(t.why)
            log("[hunt] threw ball index " .. idx .. " -> " .. tostring(t.why)
                .. " balls_before=" .. tostring(t.balls_before) .. " balls_after=" .. tostring(t.balls_after))
            -- caught: dex/nickname prompts (B = NO) then the battle ends; failed: the menu returns
            m = wait_menu(3600)
            if m ~= "menu" then return m end
        end
    end

    function self.step(handshake, status, point, frame)
        assert(type(frame) == "number" and frame > self.last_frame, "hunt frame did not advance")
        self.last_frame = frame
        assert(point and type(point.map) == "number" and type(point.x) == "number" and type(point.y) == "number"
            and type(point.battle) == "number" and type(point.party_count) == "number", "complete read-only hunt point required")
        if self.terminal then return idle(), self.terminal end
        if point.battle ~= 0 and point.battle ~= 1 then
            self.terminal = point.battle == 255 and "whiteout" or "unexpected-battle"
            return idle(), self.terminal
        end
        if point.battle == 1 and not self.battle_co then
            assert(point.battle_type == 0 and type(point.party_hp) == "number" and point.party_hp > 0,
                   "unexpected special battle or whiteout")
            self.encounters = self.encounters + 1
            self.party_before = point.party_count
            self.balls_before = balls()
            self.battle_co = coroutine.create(plan)
            log(string.format("[hunt] encounter %d at (%d,%d) mode=%s balls=%d", self.encounters, point.x, point.y, mode, self.balls_before))
        end
        if self.battle_co then
            local ok, res = coroutine.resume(self.battle_co)
            assert(ok, "battle plan error: " .. tostring(res))
            if coroutine.status(self.battle_co) ~= "dead" then return res or idle(), "wild-" .. self.stage end
            self.battle_co = nil
            self.outcome = res
            log("[hunt] battle outcome " .. tostring(res) .. " receipts " .. table.concat(self.receipts, ","))
            self.receipts = {}
            if res == "stuck" or res == "timeout" then self.terminal = "stuck"; return idle(), self.terminal end
            if res == "linked-fainted" or res == "linked-active-menu" then
                self.terminal = res
                return idle(), self.terminal
            end
            return idle(), "wild-" .. tostring(res)
        end
        if point.battle ~= 0 then return idle(), "wild-ending" end
        if self.outcome then
            -- the battle is over: classify it once, from the game's own state
            local o = self.outcome
            self.outcome = nil
            if mode == "sacrifice" then
                if opts.fainted() then self.terminal = "linked-fainted"
                elseif self.encounters >= 3 then self.terminal = "linked-survived-3-battles" end
            elseif point.party_count > self.party_before then self.terminal = "caught"
            elseif mode == "run" then self.terminal = "escaped"
            elseif balls() == 0 then self.terminal = "out-of-balls"
            elseif mode == "catch" and opts.stop_on_first_uncaught_battle and self.encounters == 1
                   and o == "battle_over" then self.terminal = "first-catch-battle-lost"
            elseif self.encounters >= M.MAX_ENCOUNTERS then self.terminal = "hunt-exhausted" end
            log(string.format("[hunt] after battle: outcome=%s result=%s party=%d balls=%d -> %s",
                              o, tostring(point.battle_result), point.party_count, balls(), self.terminal or "hunt again"))
            if self.terminal then return idle(), self.terminal end
        end
        if point.font_loaded or point.joy_ignore ~= 0 then return tap("B", frame), "close-text" end
        if point.map ~= F.MAP.ROUTE_1 then return idle(), "unknown-map" end
        local target = M.GRASS[self.target]
        if point.x == target[1] and point.y == target[2] then
            self.target = 3 - self.target
            target = M.GRASS[self.target]
        end
        return move(point, target), "pace-grass"
    end
    return self
end
return M
