-- lua/core/session.lua -- the generation-neutral client shell (P4 C4-1).
--
-- Lifted from lua/gen1/client.lua: send/seq :171-182, validate/pause :317-358, ack_cancel
-- :361-365, generic commands :480-596, hello gate :1804-1826, signal drain + failure banner
-- :1828-1845, alias observation :1852-1858, tick/safe :1859-1863, receive loop :1864-1876.
-- No BizHawk global and no memory write anywhere under lua/core: everything is injected, and
-- only the driver's executors (deferred exec.*, game.battle_write) move bytes.
--
--   S = Session.new{ net, json, hud, log, tag, player, game, identity, deferred }
--   S.send(event, fields) -> bool       S:validate() -> ok, why       S:handle_command(cmd)
--   S:start()  S:frame_end()  S:stop()  S:battle_pending_count()  S:drop_battle_writes(why)
--   S:eligible() -> bool   may anything be POSTED/armed now (writes enabled, hello'd, connected)
--   state the driver may read/set: writes_enabled, hello_sent, game_over, config, resolved_areas,
--   seeded, pending_safe (set it on battle end; `safe` goes out once out of battle)
--
-- The game driver (the ONLY generation seam; docs/gen3/research/p4_gen1_contract_map.md §3.2):
--   required: frame() -> int; read_party() -> party|nil, why (mons carry .slot);
--     game_is_live() -> bool, why; hello_ready() -> bool, why (live AND (in battle OR checkpoint),
--     plus any version hold); hello_fields() -> table; tick_fields() -> table|nil (nil = no tick);
--     in_battle() -> bool; checkpoint_ok() -> bool, why
--   optional: save_cleared() -> bool; on_reset(); start() -> signals (drain/close/failure/
--     handler_error); on_signal(sig); frame_hooks = { fn, ... } (after the signal drain);
--     pre_pump() (before net.pump: Gen 1's panel service); on_disconnect(); after_receive()
--     (pcall'd, before the deferred run: Gen 1's trade_tick); play_sound(gen3_se_id);
--     commands = { [name] = fn(cmd) -> consumed? }; battle_write(entry, slot, mon, ending)
--       -> "done" | "hold", why | nil; party_borrowed() -> bool (a borrowed party is in RAM:
--     a force_* whose key is not in it is HELD, not dropped); box_generation() -> gen, complete
--       (KEY-SCOPE-5: the generation the driver's own box scan bumps ONLY on a complete scan, and
--       whether that last scan was complete; the pair gates a retryable key_change refusal's
--       resend. TWO values on purpose -- Gen 1/2's own accessor (lua/gen1/client.lua:342)
--       returns the one, nil-if-incomplete WIRE field, because nothing there needs the flag
--       apart; a driver answering with one value reads as "complete unknown" here and is never
--       rescan-driven); rescan_boxes() (called once per tick while the last scan was incomplete
--       -- lua/gen1/client.lua:2309 -- and at once on a "box census unavailable" refusal).
-- battle_write contract: a player popup for an entry comes with its "done" -- never on a hold/deferred entry, or the retry shows it twice.
--
-- Nothing semantic precedes hello (docs/protocol.md §9 item 4): an event emitted while the
-- connection has not sent its hello yet (a frame hook, a hello_fields side effect) is held in
-- order and flushed right after the hello; a dropped connection drops the held events with it
-- (item 3: nothing is buffered across a disconnect). Validation runs FIRST in the frame, before
-- any driver service, so a pause decided this frame gates this frame's posts (S:eligible).
--
-- Command dispatch: noop; then game.commands[name] (returns true to CONSUME, false/nil to fall
-- through -- how Gen 1's receptionist show_menu, RR's trade/native/rival/explode, Gen 1's
-- known_keys bookkeeping on key_change_ack and its APEX pending_keys plug in); then
-- force_faint/force_explode routing; then box_mon/party_mon/memorialize (always deferred); then
-- the generic set. With no driver
-- handler, the prompts get the cancel sentinels and apply_trade is logged and ignored: no
-- write, no reply (the disabled-foundation write guard, docs/gen3/PLAN.md §10).
--
-- In-battle force_* (owner ruling 2026-09-23: vanilla FRLG behaves as Radical Red does today):
-- the key must resolve to one party slot; then, in battle and with a game.battle_write, the
-- command joins the core-owned pending-battle-write set, flushed every frame through
-- battle_write(entry, slot, mon, false): "done" removes it (bench mon), "hold" keeps it (the
-- active battler) with its reason and age on the tick HUD line, nil hands it to the deferred
-- checkpoint queue. Held entries are alias-aware (resolved through identity, including the
-- pending key_change alias; rewritten to the new key on key_change_ack). When the battle ends
-- each held entry gets one last battle_write(..., true); anything not "done" is deferred.
-- Which frames a battle write may land on is the driver's safety predicate, never the core's.
local Session = { TICK_INTERVAL = 30, VALIDATE_EVERY = 60, MAX_INVALID = 5, PENDING_HUD_FRAMES = 600,
                  MAX_PRE_HELLO = 64 }

local CANCEL = { show_choices = { "menu_result", "choice", 127 }, show_menu = { "menu_result", "choice", 0 },
                 choose_mon = { "mon_chosen", "slot", 7 } }
local DEFERRED_CMDS = { box_mon = true, party_mon = true, memorialize = true }
-- KEY-SCOPE-5: key_change refusals that retire nothing (lua/gen1/client.lua Client.RETRYABLE_REJECTIONS,
-- reason strings verbatim). The alias stays, and the change is re-sent after the next complete box
-- census; every other reason is terminal (U5).
local RETRYABLE_REJECTIONS = { ["box census unavailable"] = true, ["ambiguous key (trade clash)"] = true,
                              ["party hidden"] = true }

local function hud_color(cmd)
    if type(cmd.color) == "table" then return cmd.color[1], cmd.color[2], cmd.color[3], cmd.duration end
    return cmd.r, cmd.g, cmd.b, cmd.frames
end

function Session.new(p)
    local net, json, hud = assert(p.net, "net"), assert(p.json, "json"), assert(p.hud, "hud")
    local game, identity, deferred = assert(p.game, "game"), assert(p.identity, "identity"), assert(p.deferred, "deferred")
    local tag, sink = p.tag or "[SLink]", p.log or function() end
    local function log(msg) sink(tag .. " " .. msg) end
    local outbox = p.outbox
    local report_wait, report_sent_at

    local self = {
        player = p.player, seq = 0, frame = 0, hello_sent = false,
        writes_enabled = false, invalid_streak = 0, gate_revoked = false, game_over = false,
        resolved_areas = {}, seeded = false, config = {}, pending_safe = false,
        battle_pending = {}, pre_hello = {}, ack_pending = {}, signals = nil, signal_failure_shown = false,
        game = game, identity = identity, deferred = deferred,
        recovery_paused = {}, recovery_cancelled = {}, recovery_ready = not game.inventory_protocol,
    }

    local function transmit(name, fields)
        if not net.connected() then
            log("drop " .. name .. ": not connected")
            return false
        end
        self.seq = self.seq + 1
        local msg = fields or {}
        msg.event, msg.player, msg.seq = name, self.player, self.seq
        net.send(json.encode(msg))
        return true
    end
    local function send(name, fields)
        if outbox and outbox.events[name] then
            local ok,value=pcall(function()
                outbox:load(game.report_ot and game.report_ot())
                return outbox:append(name,fields)
            end)
            if not ok then
                log(tostring(value))
                hud.show("SLINK REPORT RECOVERY STOPPED - SEE LOG",255,60,60,1800)
                error(value)
            end
            report_sent_at=nil
            return value
        end
        if name ~= "hello" and not self.hello_sent and net.connected() then
            local held = self.pre_hello
            if #held >= Session.MAX_PRE_HELLO then
                log("pre-hello hold full: dropped " .. tostring(held[1][1]))
                table.remove(held, 1)
            end
            held[#held + 1] = { name, fields }
            return true
        end
        return transmit(name, fields)
    end
    -- S.send(event, fields) is the API; the method form S:send(event, fields) is accepted too, so
    -- a binding that calls it with a colon (lua/gen3/entry.lua's native send) cannot pass the
    -- session itself as the event name
    self.send = function(a, b, c)
        if a == self then return send(b, c) end
        return send(a, b)
    end

    local function signals_ok()
        self.signal_failure = self.signal_failure or (self.signals and self.signals.failure)
        if not self.signal_failure then return true end
        self.writes_enabled, self.gate_revoked = false, true
        if not self.signal_failure_shown then
            self.signal_failure_shown = true
            log("ENGINE SIGNALS STOPPED: " .. tostring(self.signal_failure))
            hud.show("SLINK STOPPED - SEE LOG", 255, 60, 60, 1800)
        end
        return false, "engine signals stopped"
    end

    function self:eligible()
        return signals_ok() and self.writes_enabled == true and self.hello_sent == true and net.connected() == true
            and self.recovery_ready
            and (not outbox or (outbox.accepted and outbox:ready()))
    end
    -- the queue's shared seams are the session's own unless the caller bound others
    deferred.send = deferred.send or send
    deferred.log = deferred.log or log
    deferred.hud = deferred.hud or hud
    deferred.identity = deferred.identity or identity
    deferred.read_party = deferred.read_party or game.read_party
    deferred.paused = function(key) return self.recovery_paused[key] == true end

    -- ── the writes gate: pause, never drop ─────────────────────────────────────────
    function self:validate()
        local healthy, failure = signals_ok()
        if not healthy then return false, failure end
        local ok, why = game.game_is_live()
        if ok then
            self.invalid_streak = 0
            if not self.writes_enabled then
                log(self.gate_revoked and "writes re-enabled after a live validation" or "writes ENABLED")
                self.writes_enabled, self.gate_revoked = true, false
            end
        else
            self.invalid_streak = self.invalid_streak + 1
            -- the save was cleared (reset / new game): the next hello is a new session, and every
            -- alias pointed at a record that is gone with it
            if game.save_cleared and game.save_cleared() then
                -- the save epoch ends here: events held for a hello that never went out belong
                -- to the save that is gone, so they die with it (never flushed into the next)
                self.hello_sent = false
                self.pre_hello = {}
                self.ack_pending = {}
                identity:clear()
                if game.on_reset then game.on_reset() end
            end
            if self.invalid_streak >= Session.MAX_INVALID and self.writes_enabled then
                -- the queues survive: an unreadable party is a transient the engine creates
                -- itself, and every command re-finds its key at the checkpoint anyway
                self.writes_enabled, self.gate_revoked = false, true
                log("writes PAUSED: " .. tostring(why))
            end
        end
        return ok, why
    end

    -- ── in-battle writes ───────────────────────────────────────────────────────────
    function self:battle_pending_count() return #self.battle_pending end

    function self:drop_battle_writes(why)
        for _, e in ipairs(self.battle_pending) do
            log(e.cmd .. " dropped (" .. tostring(why) .. ") " .. tostring(e.key))
        end
        self.battle_pending = {}
    end

    local function flush_battle_writes()
        if #self.battle_pending == 0 then return end
        local ending = not game.in_battle()
        local party = game.read_party()
        local keep = {}
        for _, e in ipairs(self.battle_pending) do
            local slot, mon, _party, why
            if party then slot, mon, _party, why = identity:find_party_slot(e.key, party) end
            local res, rwhy
            if not self.recovery_ready or self.recovery_paused[e.key] then
                res, rwhy = "hold", "missing linked Pokemon; waiting for recovery"
            elseif why then
                log(e.cmd .. " refused in battle: " .. why .. " " .. tostring(e.key))
                res = "done"
            elseif not self.writes_enabled or (outbox and not self:eligible()) then
                -- a paused gate holds battle writes too (Gen 1 on_battle_loop_head gates on
                -- writes_enabled, gen1/client.lua:1376): nothing lands while identity is unsure
                res, rwhy = "hold", self.gate_revoked and "writes paused" or "writes not enabled yet"
            elseif not party then
                res, rwhy = "hold", "party unreadable"
            elseif not slot then
                res, rwhy = "hold", "key not in party"      -- e.g. a borrowed party
            else
                res, rwhy = game.battle_write(e, slot, mon, ending)
            end
            if res == "hold" and not ending then
                e.why, e.since = rwhy or "held", e.since or self.frame
                keep[#keep + 1] = e
            elseif res ~= "done" then
                deferred:push({ cmd = e.cmd, key = e.key, nickname = e.nickname, arrival = e.arrival })
            end
        end
        self.battle_pending = keep
    end

    -- key_change_ack: local commands follow old -> new ONLY through the alias this client raised
    -- for exactly that pair when it sent the key_change, and only while that alias still names one
    -- record. BOTH keys are checked before anything is consumed (a wrong-pair ACK leaves the
    -- alias pending for the right one), and the mapping is decided only on an OBSERVED party: a
    -- fresh observation re-applies identity.lua's sticky latches (lost / ambiguous, never
    -- recomputed away). An unreadable party keeps the ACK pending until it can be observed. The
    -- migrated flag does not decide it -- migrated=false is a replay OR an old key the server
    -- never tracked (server/state.py:2557-2571) -- and key membership is never evidence. Without
    -- a valid alias the commands stay on the old key and resolve (or fail) by name.
    -- Returns true when the ACK is settled, false while it waits for a readable party.
    local function apply_ack(cmd)
        if not cmd.new_key or cmd.new_key == cmd.old_key then return true end
        local a = identity:pending_for(cmd.old_key)
        if not (a and a.old_key == cmd.old_key and a.new_key == cmd.new_key) then
            log("key_change_ack " .. tostring(cmd.old_key) .. " -> " .. tostring(cmd.new_key)
                .. ": no valid alias for the pair (" .. (a and "different pair" or "none")
                .. "); local commands stay on the old key")
            return true
        end
        local party = game.read_party()
        if not party then return false end
        identity:observe_one(a, party)
        identity:ack(cmd.old_key)                          -- consumed only now, for exactly this pair
        if a.lost or a.ambiguous then
            log("key_change_ack " .. cmd.old_key .. " -> " .. cmd.new_key .. ": no valid alias for the pair ("
                .. (a.lost and "lost" or "ambiguous") .. "); local commands stay on the old key")
            return true
        end
        for _, e in ipairs(self.battle_pending) do
            if e.key == cmd.old_key then e.key, e.migrated_from = cmd.new_key, cmd.old_key end
        end
        for _, q in ipairs(deferred.items) do
            if q.key == cmd.old_key then q.key, q.migrated_from = cmd.new_key, cmd.old_key end
        end
        return true
    end

    local function route_force(cmd)
        local c = cmd.cmd
        local entry = { cmd = c, key = cmd.key, nickname = cmd.nickname, arrival = cmd.arrival }
        local party = game.read_party()
        if not party then
            -- unreadable now (e.g. a naming prompt before the record lands): the checkpoint
            -- re-finds the key; a server command is never resent
            deferred:push(entry)
            return
        end
        local slot, _, _, why = identity:find_party_slot(cmd.key, party)
        if why then log(c .. ": " .. why .. " " .. tostring(cmd.key)) return end
        if not slot and not (game.party_borrowed and game.party_borrowed()) then
            log(c .. ": key not in party " .. tostring(cmd.key))
            return
        end
        -- a borrowed party hides the player's own: HOLD the command (the flush keeps it while
        -- the key is absent and resolves it after the restore; battle end hands it to the
        -- checkpoint queue), never drop it -- a server force_* is never resent
        if game.battle_write and game.in_battle() then
            self.battle_pending[#self.battle_pending + 1] = entry  -- flushed this frame
        else
            deferred:push(entry)
        end
    end

    -- ── inbound commands ───────────────────────────────────────────────────────────
    function self:handle_command(cmd)
        local c = cmd.cmd
        self.arrivals = (self.arrivals or 0) + 1
        cmd.arrival = self.arrivals                         -- server order, kept across a battle hold
        if c == "noop" then return end
        if c == "recovery_control" then
            local paused = {}
            for _, key in ipairs(cmd.paused or {}) do paused[key] = true end
            self.recovery_paused, self.recovery_ready = paused, true
            for key, revision in pairs(cmd.cancelled or {}) do
                if revision > (self.recovery_cancelled[key] or 0) then
                    self.recovery_cancelled[key] = revision
                    for _, queue in ipairs({deferred.items, self.battle_pending}) do
                        for i = #queue, 1, -1 do
                            if queue[i].key == key then table.remove(queue, i) end
                        end
                    end
                end
            end
            return
        end
        if c == "report_binding" and outbox then
            local ok,why=pcall(outbox.bind,outbox,cmd.binding,game.report_ot and game.report_ot())
            if not ok then
                outbox:disconnect()
                log("report binding refused: "..tostring(why))
                hud.show("SLINK REPORT BINDING REFUSED - SEE LOG",255,60,60,1800)
            end
            report_sent_at=nil
            return
        end
        if c == "report_ack" and outbox then
            outbox:ack(cmd)
            report_wait,report_sent_at=nil,nil
            return
        end
        if c == "sync_cancel" then
            if outbox then outbox:cancel(cmd) end
            for i=#deferred.items,1,-1 do
                local q=deferred.items[i]
                if q.key==cmd.key and q.intent_revision and q.intent_revision<=cmd.intent_revision then
                    table.remove(deferred.items,i)
                end
            end
            return
        end
        local own = game.commands and game.commands[c]
        if own and own(cmd) then return end
        if c == "force_faint" or c == "force_explode" then return route_force(cmd) end
        if DEFERRED_CMDS[c] then deferred:push(cmd) return end
        local cancel = CANCEL[c]
        if cancel then
            send(cancel[1], { token = cmd.token, [cancel[2]] = cancel[3] })
        elseif c == "msgbox" or c == "gui_prompt" then
            local r, g, b, f = hud_color(cmd)
            hud.prompt(cmd.text, r, g, b, f)
        elseif c == "hud_show" then
            local r, g, b, f = hud_color(cmd)
            hud.show(cmd.text, r, g, b, f)
        elseif c == "play_sound" then
            if game.play_sound then game.play_sound(cmd.sound) end
        elseif c == "resolved_areas" then
            self.resolved_areas = {}
            for _, a in ipairs(cmd.areas or {}) do self.resolved_areas[a] = true end
            self.seeded = true
        elseif c == "unresolve_area" then
            self.resolved_areas[cmd.area_id] = nil
        elseif c == "key_change_ack" then
            if not apply_ack(cmd) then self.ack_pending[#self.ack_pending + 1] = cmd end
        elseif c == "key_change_rejected" and RETRYABLE_REJECTIONS[cmd.reason] then
            -- KEY-SCOPE-5: nothing was retired; the alias stays and the change is re-sent after the
            -- next complete box census (a census refusal asks for that census now). Console only --
            -- the player sees no refusal for a change that is still in flight (HUD is player-facing
            -- only, owner ruling 2026-09-25).
            local a = identity:pending_for(cmd.old_key)
            if a and a.old_key == cmd.old_key and a.new_key == cmd.new_key then
                a.retry_gen = game.box_generation and (game.box_generation()) or nil
                if (cmd.reason == "box census unavailable" or cmd.reason == "party hidden")
                   and game.rescan_boxes then game.rescan_boxes() end
            end
            log("key_change refused (will retry): " .. tostring(cmd.reason) .. " " .. tostring(cmd.old_key))
        elseif c == "key_change_rejected" then
            identity:reject(cmd.old_key, game.read_party())
            log("key_change rejected: " .. tostring(cmd.reason) .. " " .. tostring(cmd.old_key))
            -- The HUD is player-facing only (owner rule): the reason is an internal code
            -- ("collision", "ambiguous match", ...) and belongs in the console line above.
            hud.show("IDENTITY CHANGE REFUSED", 255, 64, 64, 600)
        elseif c == "config" then
            self.config = cmd
        elseif c == "game_over" then
            -- the server's game_over carries no sound of its own: the client supplies the cue
            if game.play_sound then game.play_sound(26) end -- SE_FAILURE
            hud.set_game_over()
            self.game_over = true
        elseif c == "rebuild_start" then
            hud.set_rebuilding(cmd.text)
        elseif c == "rebuild_done" then
            hud.clear_rebuilding()
        else
            log("unhandled command " .. tostring(c))
        end
    end

    -- KEY-SCOPE-5: a change refused for a retryable reason goes out again once a complete box
    -- census NEWER than the refusal has gone out (mirrors lua/gen1/client.lua resend_refused_change).
    local function resend_refused_change()
        -- `a.msg` is the exact message the driver sent; Identity:begin_alias never sets it, and a
        -- resend with no fields is a key_change nobody can act on. Stay armed, send nothing.
        for _, a in pairs(identity.pending_aliases) do
            if a.msg and a.retry_gen and game.box_generation then
                local gen, complete = game.box_generation()
                if complete and gen and gen > a.retry_gen then
                    if send("key_change", a.msg) then a.retry_gen = nil end
                end
            end
        end
    end

    -- ── hello / tick ───────────────────────────────────────────────────────────────
    function self:send_hello()
        local f = game.hello_fields()
        if not f then return end
        if f.writes_enabled == nil then f.writes_enabled = self.writes_enabled end
        local instrumentation_key = SLINK_INSTRUMENTATION_KEY or os.getenv("SLINK_INSTRUMENTATION_KEY")
        if f.instrumentation_key == nil and instrumentation_key and instrumentation_key ~= "" then
            f.instrumentation_key = instrumentation_key
        end
        self.hello_sent = transmit("hello", f)
        if not self.hello_sent then return end
        local held = self.pre_hello
        self.pre_hello = {}
        for _, e in ipairs(held) do transmit(e[1], e[2]) end
    end

    function self:send_tick()
        -- KEY-SCOPE-5: an incomplete census is retried here, at most once per tick, and BEFORE
        -- the tick so the census this tick publishes is the fresh one -- but only while a refused
        -- key_change is still armed for one. Nothing else consumes the rescan (the driver keeps
        -- the census fresh on its own PC triggers), and a scan that CANNOT land -- a null storage
        -- pointer across a menu or a battle -- would otherwise walk every box every 30 frames
        -- for the rest of the session.
        for _, a in pairs(identity.pending_aliases) do
            if a.retry_gen and game.box_generation and game.rescan_boxes then
                local _, complete = game.box_generation()
                if complete == false then game.rescan_boxes() end
                break
            end
        end
        local f = game.tick_fields()
        -- the resend rides the tick that carries the newer census: a tick that went nowhere
        -- published no census, so re-sending on it only earns the same refusal again
        if f then
            local published = send("tick", f)
            if published and self.hello_sent and f.party_hidden ~= true then resend_refused_change() end
        end
    end

    -- PLAN §5.4: a stuck hold is diagnosable -- what, why, since when -- from the CONSOLE, never
    -- the HUD (owner 2026-09-25: the player needs no pending counter; Gen 1 shows holds nowhere).
    -- One line when a hold first passes PENDING_HUD_FRAMES or its reason changes, not per tick.
    local last_hold
    local function report_holds()
        local parts = {}
        local n, why, age, head = deferred:pending(self.frame)
        if n > 0 and why and age >= Session.PENDING_HUD_FRAMES then
            parts[#parts + 1] = string.format("%s x%d (%s)", tostring(head), n, tostring(why))
        end
        for _, e in ipairs(self.battle_pending) do
            local held = self.frame - (e.since or self.frame)
            if held >= Session.PENDING_HUD_FRAMES then
                parts[#parts + 1] = string.format("%s (%s)", e.cmd, tostring(e.why))
                break
            end
        end
        local line = #parts > 0 and table.concat(parts, "; ") or nil
        if line and line ~= last_hold then log("held: " .. line) end
        last_hold = line
    end

    -- ── per-frame driver ───────────────────────────────────────────────────────────
    function self:start()
        if game.start then self.signals = game.start() end
    end

    function self:frame_end()
        self.frame = game.frame()
        signals_ok()
        if outbox and outbox.failure then
            self.writes_enabled=false
            if not self.report_failure_shown then
                self.report_failure_shown=true
                log("gameplay journal stopped: "..outbox.failure)
                hud.show("SLINK REPORT RECOVERY STOPPED - SEE LOG",255,60,60,1800)
            end
            return
        end
        -- validation first: a pause decided this frame already gates this frame's posts
        if self.frame % Session.VALIDATE_EVERY == 0 then self:validate() end
        if game.pre_pump then game.pre_pump() end
        net.pump()
        local connected = net.connected()
        if not connected then
            self.hello_sent = false
            self.recovery_ready = not game.inventory_protocol
            self.pre_hello = {}
            if outbox then outbox:disconnect(); report_wait=nil end
            if game.on_disconnect then game.on_disconnect() end
        end
        -- pump, THEN hello: the reconnect hello is the first line of a new connection
        -- (tests/unit/test_connector_reconnect.py:5-8)
        if connected and not self.hello_sent and game.hello_ready() then self:send_hello() end
        connected = connected and self.hello_sent
        local sigs = self.signals
        for _, sig in ipairs(sigs and sigs:drain() or {}) do
            local ok, err = pcall(game.on_signal, sig)
            if not ok then log("signal " .. tostring(sig.kind) .. ": " .. tostring(err)) end
        end
        if sigs and sigs.handler_error then
            log("hook handler error: " .. tostring(sigs.handler_error))
            sigs.handler_error = nil
        end
        signals_ok()
        for _, fn in ipairs(game.frame_hooks or {}) do fn() end
        if identity:active() then
            local party = game.read_party()
            if party then identity:observe(party) end
        end
        if #self.ack_pending > 0 then                     -- ACKs held for a readable party
            local waiting = self.ack_pending
            self.ack_pending = {}
            for _, cmd in ipairs(waiting) do
                if not apply_ack(cmd) then self.ack_pending[#self.ack_pending + 1] = cmd end
            end
        end
        if self.frame % Session.TICK_INTERVAL == 0 then
            if connected then self:send_tick() end
            report_holds()
        end
        if self.pending_safe and connected and not game.in_battle() then
            self.pending_safe = false
            send("safe", {})
        end
        while true do
            local line = net.receive()
            if not line then break end
            local ok, reply = pcall(json.decode, line)
            if ok and type(reply) == "table" and type(reply.commands) == "table" then
                for _, cmd in ipairs(reply.commands) do
                    local hok, herr = pcall(self.handle_command, self, cmd)
                    if not hok then log("command " .. tostring(cmd and cmd.cmd) .. ": " .. tostring(herr)) end
                end
            else
                log("unreadable reply line")
            end
        end
        if game.after_receive then
            local ok, err = pcall(game.after_receive)
            if not ok then log("after_receive: " .. tostring(err)) end
        end
        if outbox and connected and outbox.accepted then
            local ok,why=pcall(function()
                if not report_sent_at or self.frame-report_sent_at>=Session.TICK_INTERVAL then
                    local row=outbox:head()
                    report_sent_at=self.frame
                    if row then
                        transmit(row.event,row.fields)
                        report_wait,report_sent_at=row.fields.report.number,self.frame
                    end
                end
            end)
            if not ok then
                log(tostring(why)); hud.show("SLINK REPORT RECOVERY STOPPED - SEE LOG",255,60,60,1800)
            end
        end
        if outbox and outbox.failure then
            self.writes_enabled=false
            if not self.report_failure_shown then
                self.report_failure_shown=true
                log("gameplay journal stopped: "..outbox.failure)
                hud.show("SLINK REPORT RECOVERY STOPPED - SEE LOG",255,60,60,1800)
            end
            return
        end
        flush_battle_writes()
        self:run_deferred()
    end

    function self:run_deferred()
        return deferred:run(function()
            local healthy, failure = signals_ok()
            if not healthy then return false, failure end
            if not self.writes_enabled then
                return false, self.gate_revoked and "writes paused" or "writes not enabled yet"
            end
            if not net.connected() then return false, "not connected" end
            if not self.recovery_ready then return false, "waiting for recovery control" end
            if outbox and not self:eligible() then return false,"report binding not accepted" end
            return game.checkpoint_ok()
        end, self.frame, self.game_over)
    end

    function self:stop()
        if self.signals then self.signals:close() end
    end

    return self
end

return Session
