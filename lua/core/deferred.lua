-- lua/core/deferred.lua -- the deferred command FIFO, shared by every generation.
--
-- Lifted from lua/gen1/client.lua:717-822 (run_deferred). Policy only; every byte that moves
-- goes through an injected executor, and each command runs inside pcall between exec.arm() and
-- exec.disarm() (disarm again after the pcall, as Gen 1 does, so a throw never leaves it armed).
--
--   Q = Deferred.new{ exec = {...}, memorial_box = int, send, log, hud, identity, read_party }
--   exec.arm(), exec.disarm()                      the "overworld" write window (+ allow ranges)
--   exec.faint_slot(slot, cmd_name)                force_faint / force_explode at the checkpoint
--   exec.deposit(key, slot_hint)       -> true | nil, reason
--   exec.withdraw(key, stats, nickname) -> true | nil, reason   (base stats: the executor's job)
--   exec.memorialize(key, slot_hint)   -> true | nil, reason
--   exec.stats_of(mon) -> stats table  (optional; default { level = m.level, maxHP = m.max_hp })
--   exec.rescan()                      (optional; after a successful move)
--   exec.write_count() -> int          (optional; bytes the sink has written so far -- tells a
--                                      clean executor error from a partial mutation)
--
-- Queue hygiene (docs/protocol.md §9 item 31): a box_mon cancels a still-queued party_mon for
-- the same key and vice versa (the NEW command stays: the server already cancelled the old
-- one at queue time, state.py:2043-2046); a memorialize already queued for a key absorbs a
-- duplicate, so a tail-retrying memorial is never multiplied.
--
-- An executor that THROWS still ends in a terminal keyed reply (items 28-30):
--   nothing written (write_count unchanged)  -> a safe retry at the tail, EXEC_RETRIES times,
--                                               then <cmd>_failed "executor error: ..."
--   bytes written, or no write_count          -> <cmd>_failed "uncertain: partial write ...":
--                                               never retried (the mutation may be half done)
--   the executor already answered             -> nothing more (a throw after the reply)
-- force_faint/force_explode have no reply on the wire: their errors are logged.
-- send/log/hud/identity/read_party may be left out: Session.new binds its own (session.lua).
--
-- Gate: Q:run(gate, frame, game_over) asks gate() -> ok, why only when the queue is non-empty
-- (Session's gate = writes_enabled and connected and game.checkpoint_ok()). One command per call.
-- Keyed replies keep cmd.key (what the server tracks), even when a retired alias moved the
-- physical key: box_mon_failed, stats_cache, sync_retrieve_done/_failed, memorialize_done/_failed.
-- Tail retry ONLY on the exact reasons "party full" (budget = queue length at the first refusal
-- + 1) and "last party mon" (unbounded; dropped silently once game_over is latched).
--
-- DELIBERATE DELTA FROM GEN 1 (docs/gen3/PLAN.md:57, R9; §5.4): Gen 1 sends stats_cache BEFORE
-- boxes:deposit (gen1/client.lua:761), so a refused deposit still told the server the mon was
-- in flight. Here the stats are SNAPSHOTTED before the deposit (party-only fields are zeroed by
-- the move) and stats_cache is SENT only after the executor confirms it; a refusal sends
-- box_mon_failed and no stats_cache. Gen 1 adopts this when it re-binds.
--
-- Can Gen 1 bind it unchanged? Its run_deferred body is this code with self.boxes:* and
-- writes:faint_party_slot as the executors, box_count - 1 as memorial_box and
-- rom.base_stats_for resolved inside its withdraw closure. Q.items is the plain array Gen 1
-- tests read as client.deferred (alias it). Only the stats_cache ordering above changes; the
-- queue hygiene and the executor-error replies are further deltas Gen 1 gains on re-bind.
local Deferred = { EXEC_RETRIES = 3 }
Deferred.__index = Deferred

local OPPOSITE = { box_mon = "party_mon", party_mon = "box_mon" }
local FAILED = { box_mon = "box_mon_failed", party_mon = "sync_retrieve_failed",
                 memorialize = "memorialize_failed" }

local EXEC = { "arm", "disarm", "faint_slot", "deposit", "withdraw", "memorialize" }

local function nick_label(_key, nickname)
    if type(nickname) == "string" and nickname:find("%S") then return nickname end
    return "Your Pokemon"
end

local function default_stats(m) return { level = m.level, maxHP = m.max_hp } end

function Deferred.new(p)
    local exec = assert(p and p.exec, "deferred: exec required")
    for _, n in ipairs(EXEC) do assert(exec[n] ~= nil, "deferred: exec." .. n .. " required") end
    return setmetatable({ items = {}, exec = exec, memorial_box = p.memorial_box, send = p.send,
                          log = p.log, hud = p.hud, identity = p.identity, read_party = p.read_party,
                          hold = nil }, Deferred)
end

function Deferred:push(cmd)
    local log = self.log or function() end
    local opp = OPPOSITE[cmd.cmd]
    for i = #self.items, 1, -1 do
        local q = self.items[i]
        if q.key == cmd.key then
            if cmd.intent_id and q.intent_id==cmd.intent_id then return end
            if cmd.intent_revision and q.intent_revision and cmd.intent_revision<q.intent_revision then return end
            if opp and q.cmd == opp then
                table.remove(self.items, i)
                log(cmd.cmd .. " cancels the queued " .. opp .. " " .. tostring(cmd.key))
            elseif cmd.cmd == "memorialize" and q.cmd == "memorialize" then
                log("memorialize already queued " .. tostring(cmd.key))
                return
            end
        end
    end
    -- server order: a command handed over late (a force_* held through a battle) goes back to its
    -- ARRIVAL place, ahead of anything the server sent after it (live deadzone_gen3 r3: the same
    -- key's later memorialize ran first and the force_faint was dropped, "key not in party")
    local at = #self.items + 1
    if cmd.arrival then
        for i, q in ipairs(self.items) do
            if q.arrival and q.arrival > cmd.arrival then at = i; break end
        end
    end
    table.insert(self.items, at, cmd)
end
function Deferred:size() return #self.items end

-- n, why, age (frames), head command name -- for the tick HUD line (PLAN §5.4). why is nil when
-- nothing has been refused since the last command finished.
function Deferred:pending(frame)
    local n = #self.items
    if n == 0 then return 0, nil, 0, nil end
    local h = self.hold
    return n, h and h.why, h and (frame - h.since) or 0, self.items[1].cmd
end

local function held(self, frame, why)
    if self.hold then self.hold.why = why else self.hold = { why = why, since = frame } end
end

function Deferred:run(gate, frame, game_over)
    if #self.items == 0 then self.hold = nil; return false end
    local open, gate_why = gate()
    if not open then held(self, frame, gate_why or "gate closed"); return false end
    local exec, id = self.exec, self.identity
    local log = self.log or function() end
    local send = self.send or function() return false end
    local function show(...) if self.hud then self.hud.show(...) end end
    local function find(key)
        local party = self.read_party and self.read_party() or nil
        if not id then return nil end
        return id:find_party_slot(key, party)
    end
    local function rescan() if exec.rescan then exec.rescan() end end

    local cmd = table.remove(self.items, 1)
    local requeued, settled
    local durable=self.outbox and cmd.intent_id and OPPOSITE[cmd.cmd]
    local function answer(event, fields)
        if durable then
            self.outbox:complete(cmd,event,fields)
            settled=true
            return true
        end
        settled = true
        return send(event, fields)
    end
    -- A retired alias (a rejected key_change) names the OLD key; the executor gets the key the
    -- cartridge holds and the evidence-validated slot. Evidence failure is TERMINAL for it: the
    -- executor's own key lookup would find whichever record carries the duplicated key.
    local r = id and id:retired(cmd.key)
    local phys, hint, refused = r and r.new_key or cmd.key, nil, nil
    if r then
        local slot, _, _, why = find(cmd.key)
        hint, refused = slot, (not slot) and (why or "retired record not found") or nil
    end
    local before = exec.write_count and exec.write_count()
    local ok, err = pcall(function()
        if durable then
            local slot,mon=find(cmd.key)
            local stats=slot and (exec.stats_of or default_stats)(mon) or nil
            local phase=self.outbox:command(cmd,stats)
            if phase=="done" or phase=="stale" then settled=true; return end
            local applied=exec.command_state and exec.command_state(cmd)
            if applied==true then
                if cmd.cmd=="box_mon" then
                    local saved=self.outbox:command_stats(cmd)
                    assert(saved,"applied deposit stats unavailable")
                    answer("stats_cache",{key=cmd.key,stats=saved})
                else answer("sync_retrieve_done",{key=cmd.key}) end
                rescan()
                return
            end
            if phase=="prepared" or applied==nil then
                self.items[#self.items+1]=cmd
                requeued="uncertain PC command; qualified census required"
                settled=true
                show("SLINK PC RECOVERY HELD - SEE LOG",255,160,60,600)
                log(requeued.." "..tostring(cmd.intent_id))
                return
            end
        end
        exec.arm()
        if refused and (cmd.cmd == "box_mon" or cmd.cmd == "memorialize") then
            log(cmd.cmd .. " refused for the retired key: " .. refused .. " " .. tostring(cmd.key))
            answer(cmd.cmd .. "_failed", { key = cmd.key, reason = refused })
        elseif cmd.cmd == "force_faint" or cmd.cmd == "force_explode" then
            local slot, _, _, why = find(cmd.key)
            if slot then
                exec.faint_slot(slot, cmd.cmd)
                show("!! " .. nick_label(cmd.key, cmd.nickname) .. " KO'd", 255, 80, 80, 360)
            else
                -- left the party before the checkpoint, or no longer names one mon: no byte
                -- moves. The protocol has no force_faint NACK.
                log(cmd.cmd .. " dropped at the checkpoint: " .. (why or "key not in party")
                    .. " " .. tostring(cmd.key))
            end
        elseif cmd.cmd == "box_mon" then
            local slot, mon = find(cmd.key)
            local stats = slot and (exec.stats_of or default_stats)(mon) or nil  -- BEFORE the move
            local done, reason = exec.deposit(phys, hint)
            if not done then
                answer("box_mon_failed", { key = cmd.key, reason = reason or "deposit refused" })
                show("X Box fail: " .. nick_label(cmd.key, mon and mon.nickname), 255, 80, 80, 240)
            else
                if stats then answer("stats_cache", { key = cmd.key, stats = stats }) end
                rescan()
                show("↓ " .. nick_label(cmd.key, mon and mon.nickname) .. " boxed", 100, 180, 255, 200)
            end
        elseif cmd.cmd == "party_mon" then
            local done, reason = exec.withdraw(cmd.key, cmd.stats, cmd.nickname)
            if done then
                answer("sync_retrieve_done", { key = cmd.key })
                rescan()
                show("↑ " .. nick_label(cmd.key, cmd.nickname) .. " unboxed", 100, 255, 160, 200)
            elseif reason == "party full" and (cmd.full_retries or 0) < (cmd.full_budget or (#self.items + 1)) then
                -- Whiteout rebuild: the server queues party_mon BEFORE the memorializes that free
                -- a slot, and sync_retrieve_failed is final there -- wait at the TAIL, bounded by
                -- the queue length seen at the first refusal.
                cmd.full_budget = cmd.full_budget or (#self.items + 1)
                cmd.full_retries = (cmd.full_retries or 0) + 1
                self.items[#self.items + 1] = cmd
                requeued = "party full"
                log("party_mon " .. tostring(cmd.key) .. ": party full, retry " .. cmd.full_retries
                    .. "/" .. cmd.full_budget .. " after the queue")
            else
                answer("sync_retrieve_failed", { key = cmd.key, reason = reason or "withdraw refused" })
            end
        elseif cmd.cmd == "memorialize" then
            local _, mem_mon = find(cmd.key)
            local done, reason = exec.memorialize(phys, hint)
            settled = true
            if done then
                send("memorialize_done", { key = cmd.key, box = self.memorial_box })
                rescan()
                if id then id:forget(cmd.key) end
                show("† " .. nick_label(cmd.key, mem_mon and mem_mon.nickname) .. " buried", 255, 140, 40, 300)
            elseif reason == "last party mon" and game_over then
                log("memorialize dropped: last mon after game over " .. tostring(cmd.key))
            elseif reason == "last party mon" then
                -- block until a party_mon lands -- at the TAIL: the rebuild that makes the
                -- memorial legal is itself queued behind this command (A5)
                self.items[#self.items + 1] = cmd
                requeued = "last party mon"
            else
                send("memorialize_failed", { key = cmd.key, reason = reason or "memorial refused" })
                show("X Mem fail: " .. nick_label(cmd.key, mem_mon and mem_mon.nickname), 255, 80, 80, 300)
            end
        end
        exec.disarm()
    end)
    exec.disarm()
    if not ok and not settled then
        local wrote = before == nil or exec.write_count() ~= before
        local tries = cmd.error_retries or 0
        if not wrote and tries < Deferred.EXEC_RETRIES then
            cmd.error_retries = tries + 1
            self.items[#self.items + 1] = cmd
            requeued = "executor error, nothing written"
            log("deferred " .. tostring(cmd.cmd) .. " error (nothing written), retry " .. cmd.error_retries
                .. "/" .. Deferred.EXEC_RETRIES .. ": " .. tostring(err))
        else
            local reason = (wrote and "uncertain: partial write before the error: " or "executor error: ")
                           .. tostring(err)
            log("deferred " .. tostring(cmd.cmd) .. " failed: " .. reason .. " " .. tostring(cmd.key))
            if FAILED[cmd.cmd] then answer(FAILED[cmd.cmd], { key = cmd.key, reason = reason }) end
        end
    elseif not ok then
        log("deferred " .. tostring(cmd.cmd) .. " error after its reply: " .. tostring(err))
    end
    if requeued then held(self, frame, requeued) else self.hold = nil end
    return true
end

return Deferred
