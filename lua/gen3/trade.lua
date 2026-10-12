-- Durable trade protocol over a cartridge transport. No writes or ABI addresses.
-- Native owns all staging/publication and proves visit/save/commit milestones.
local T = {}
local function token(v) return type(v) == "string" and v ~= "" end
local function copy(t)
    local out = {}
    for k, v in pairs(t) do out[k] = v end
    return out
end
-- No current abi.h reason guarantees that SCENE failed before mutation.
-- Admission here requires a T2 guarantee; tests may inject MODEL-only reasons.
local PRECOMMIT_REFUSALS = {}
function T.new(d)
    local native = assert(d.native)
    local journal = d.journal
    local precommit_refusals = copy(d.precommit_refusal_reasons or PRECOMMIT_REFUSALS)
    assert(type(d.prepare_frames) == "number" and d.prepare_frames > 0 and d.prepare_frames % 1 == 0,
           "pack prepare deadline required")
    assert(type(d.apply_frames) == "number" and d.apply_frames > 0 and d.apply_frames % 1 == 0,
           "pack apply deadline required")
    assert(d.decode_blob, "reads facade blob decoder required")
    assert(d.epoch, "trade session epoch required")
    local active, prepared = nil, nil
    local retired, owed, uncertain_records = {}, {}, {}
    local recovery_reports = {}
    local self = {}
    local function bucket(records, epoch)
        records[epoch] = records[epoch] or {}
        return records[epoch]
    end
    local function retired_for(epoch, value)
        return (retired[epoch] or {})[value]
    end
    local function report_pending(epoch, value)
        for _, row in ipairs(owed) do
            if row[1] == "trade_done" and row[2].token == value and row[3] == epoch then return true end
        end
        return d.report_pending and d.report_pending(epoch, value) == true or false
    end
    local function prior_report_pending(epoch, value)
        for prior, records in pairs(retired) do
            if prior ~= epoch and records[value] and report_pending(prior, value) then return true end
        end
        return false
    end
    local function may_commit(t)
        return t.commit_entered or (t.scene_job and (t.scene_job.posted or t.scene_job.publish_attempted))
    end
    local function emit(name, fields, epoch)
        owed[#owed + 1] = {name, fields, epoch or d.epoch()}
    end
    local function flush()
        if not d.ready_to_send() then return end
        while owed[1] do
            local row = owed[1]
            -- A reconnect cannot revive an old readiness lease. Terminal reports
            -- remain owed across connections and keep their original epoch.
            if row[1] ~= "apply_ready" or row[3] == d.epoch() then
                if d.send(row[1], copy(row[2]), row[3]) == false then return end
            end
            table.remove(owed, 1)
        end
    end
    local function locate(old_key)
        local party = d.party()
        if type(party) ~= "table" or #party < 1 or #party > d.capacity then return nil end
        local found
        for _, mon in ipairs(party) do
            if d.key(mon) == old_key then
                if found then return nil end
                found = mon
            end
        end
        if not found or type(found.slot) ~= "number" or found.slot % 1 ~= 0 or found.slot < 0 or found.slot >= d.capacity
           or type(found.species) ~= "number" or found.species <= 0
           or found.is_egg == 1 or found.is_egg == true or found.is_bad_egg == 1 or found.is_bad_egg == true or found.checksum_ok == false
           or not native:trade_eligible(found) then return nil end
        return found, party
    end
    local function visit()
        if not native.trade_capable or not native:trade_capable() or not d.eligible() then return nil end
        local v = native:trade_visit()
        if not v or v.id == nil or v.accepted ~= true or v.pre_saved ~= true or v.apply_open ~= true then return nil end
        return v
    end
    local uncertain -- the journal refusal path below must report uncertainty, not certain unchanged
    local function unchanged(t)
        if retired_for(t.epoch, t.token) then return end
        if t.phase ~= "precommit-proof" then
            t.phase = "withdrawing" -- invalidate queued callbacks before cancel can call them
            if t.prepare_job then native:cancel(t.prepare_job) end
            if t.scene_job then native:cancel(t.scene_job) end
            if t.stage_job and not t.stage_done then
                t.phase = "draining"
                local cancelled = native:cancel(t.stage_job)
                if retired_for(t.epoch, t.token) then return end -- synchronous cancellation callback
                if not cancelled and not t.stage_done
                   and (t.stage_job.posted or t.stage_job.publish_attempted) then
                    return -- native still owns staging; its ACK/abort callback retires this lease
                end
                t.stage_done = true
            end
        end
        local reconciled = not t.journaled
        if t.journaled and journal and (not may_commit(t) or t.precommit_proved) then
            local ok, why = journal:precommit_unchanged(t.token,t.journal_epoch)
            if why == "trade journal lock busy" then
                t.phase = "precommit-proof" -- no certain result until the guard is rechecked
                return
            end
            reconciled = ok == true
        end
        if t.journaled and not reconciled then
            return uncertain(t, "durable precommit journal unavailable")
        end
        if t.prepare_pending then emit("apply_ready", {token=t.token, ok=false}, t.epoch) end
        local result = {token=t.token, slot=t.slot, new_key=t.old_key, new_species=0}
        bucket(retired, t.epoch)[t.token] = result
        emit("trade_done", result, t.epoch)
        if active == t then active = nil end
        if prepared == t then prepared = nil end
        if reconciled and t.precommit_proved and native.trade_reconciled then native:trade_reconciled(t.token) end
        if native.withdraw_trade then native:withdraw_trade(t.token) end
        if d.completed then d.completed(t, false) end
    end
    local function declare_uncertain(t, why)
        t.phase, t.why = "uncertain", why
        bucket(uncertain_records, t.epoch)[t.token] = t
        -- An acknowledged report does not make possibly unsaved RAM durable.
        -- Keep this barrier until a real reset and a post-declaration hello.
        t.requires_reload = t.requires_reload or t.reload_seen or (may_commit(t) and not t.save_success)
        local declaration = t.reload_seen and "reset" or (t.requires_reload and "unsaved" or "live")
        local fields = {token=t.token, uncertain=true, after_reset=t.requires_reload or nil}
        if t.declared ~= declaration then
            t.declared = declaration
            emit("trade_done", fields, t.epoch)
        end
        bucket(retired, t.epoch)[t.token] = fields
        if active == t then active = nil end
        if not t.warned then
            t.warned = true
            if d.log then d.log("trade uncertain: " .. tostring(why)) end
            if d.hud then d.hud.show("TRADE UNCERTAIN - CHECK PARTY", 255, 64, 64, 600) end
        end
    end
    uncertain = function(t, why)
        if active == t then declare_uncertain(t, why) end
    end
    local function result(t)
        if not (t.commit_entered and t.scene_done and t.save_success and t.final_result == "committed") then
            return uncertain(t, "native completion lacks commit/scene/save witnesses")
        end
        local proof = t.saved_result
        if not proof then
            local party, got, old = d.party(), nil, false
            if type(party) ~= "table" or #party ~= t.party_count then
                return uncertain(t, "final party unreadable or count changed")
            end
            for _, mon in ipairs(party) do
                local k = d.key(mon)
                if k == t.partner_key then
                    if got then return uncertain(t, "received identity duplicated") end
                    got = mon
                end
                if k == t.old_key then old = true end
            end
            if not got or old or got.is_egg == 1 or got.is_bad_egg == 1 or got.checksum_ok == false then
                return uncertain(t, "saved result does not match received identity")
            end
            proof = {party=party, got=got,
                     fields={token=t.token, slot=got.slot, new_key=d.key(got), new_species=got.species}}
            t.saved_result = proof -- complete post-save identity proof, latched before a busy guard retry
        end
        if not journal then
            return uncertain(t, "durable journal result unavailable")
        end
        local saved, why = journal:native_saved(t.token,t.journal_epoch)
        if saved ~= true then
            if why == "trade journal lock busy" then
                t.phase = "save-proof" -- native save is already witnessed; retry without another RAM write
                return
            end
            return uncertain(t, "durable journal result unavailable")
        end
        if native.trade_reconciled then native:trade_reconciled(t.token) end
        local fields = proof.fields
        bucket(retired, t.epoch)[t.token], active = fields, nil
        emit("trade_done", fields, t.epoch)
        if d.completed then d.completed(t, true, proof.party, proof.got) end
    end
    local post_scene
    post_scene = function(t)
        if active ~= t then return end
        local mon = locate(t.old_key)
        if not mon then return unchanged(t) end
        if not t.journaled then
            local ok, why = journal:arm(t.token,t.journal_epoch)
            if ok ~= true then
                if why == "trade journal lock busy" then
                    t.phase = "arm-proof" -- the allocated epoch is kept; no scene has been posted
                    return
                end
                if d.log then d.log("trade write-ahead refused: " .. tostring(why)) end
                return unchanged(t)
            end
            t.journaled = true
            if d.intent_committed then d.intent_committed(t) end
        end
        t.slot, t.phase = mon.slot, "scene"
        t.scene_attempt = (t.scene_attempt or 0) + 1
        local attempt = t.scene_attempt
        local function progress(witness)
            if active ~= t or t.scene_attempt ~= attempt or t.phase ~= "scene" or not t.scene_job or not t.scene_job.posted then return end
            if type(witness) ~= "table" then return end
            if witness.commit_entered == true then t.commit_entered = true end
            if witness.scene_done == true then
                if not t.commit_entered then return uncertain(t, "scene milestone before commit") end
                t.scene_done = true
            end
            if witness.save_success == true then
                if not t.scene_done then return uncertain(t, "save milestone before scene") end
                if not t.save_success then
                    -- A cartridge save witness alone cannot authorize journal retirement.
                    -- The host must persist SaveRAM before native_saved can release the lease.
                    local ok, saved = pcall(d.saveram)
                    if not ok or saved == false then
                        return uncertain(t, "host SaveRAM flush failed: " .. tostring(saved))
                    end
                    t.save_success = true
                end
            end
            if witness.final_result ~= nil then t.final_result = witness.final_result end
            if witness.unchanged_proved == true and witness.final_result == "unchanged" and not t.commit_entered then
                t.precommit_proved = true
            end
        end
        t.scene_job = native:transfer("scene", {slot=t.slot, token=t.token, old_key=t.old_key,
            visit=t.visit, dispatch_deadline=t.apply_deadline},
            function(why, result_code, reason)
                if active ~= t or t.scene_attempt ~= attempt or t.phase ~= "scene" then return end
                if why == "guard:moved" then return post_scene(t) end
                if why then
                    if t.precommit_proved and t.final_result == "unchanged" and not t.commit_entered then return unchanged(t) end
                    local named_refusal = why == "native refused" and token(reason)
                    if named_refusal and precommit_refusals[reason] == true and not t.commit_entered then
                        t.precommit_proved = true
                        return unchanged(t)
                    end
                    if may_commit(t) then
                        if named_refusal then
                            why = why .. ": " .. reason .. " (result " .. tostring(result_code) .. ")"
                        end
                        return uncertain(t, why)
                    end
                    return unchanged(t)
                end
                result(t)
            end,
            function()
                if active ~= t or t.scene_attempt ~= attempt or t.phase ~= "scene" then return false, "guard:stale" end
                if t.epoch ~= d.epoch() then return false, "guard:epoch" end
                if d.frame() > t.apply_deadline then return false, "guard:apply_expired" end
                local open, why = journal:lease_open(t.token,t.journal_epoch)
                if not open then
                    if why == "trade journal lock busy" then return false, "guard:journal_busy", true end
                    return false, "guard:journal"
                end
                local v, current = visit(), locate(t.old_key)
                if not v or v.id ~= t.visit or not current then return false, "guard:lost" end
                if current.slot ~= t.slot then return false, "guard:moved" end
                return true
            end, progress)
        if not t.scene_job and active == t and t.phase == "scene" then unchanged(t) end
    end
    -- A valid prepare that arrives while the native side is briefly busy (still leaving the
    -- chooser, a queued job, the field checkpoint not yet reached) waits up to prepare_frames
    -- instead of refusing: T5 LG->FR 2026-09-27 answered ok=false for "native not authorized"
    -- 187 frames after its own mon_chosen and the server cancelled the whole trade.
    local held = nil
    local function refuse(cmd, why, epoch)   -- every apply_ready ok=false names its reason once
        if d.log then d.log("apply_prepare refused: " .. why) end
        emit("apply_ready", {token=cmd.token, ok=false}, epoch); flush()
    end
    local function hold_journal(cmd, epoch, deadline)
        if held then return refuse(cmd, "another prepare held") end
        held = {cmd=cmd, epoch=epoch, deadline=deadline, journal_busy=true}
    end
    function self:prepare(cmd, deadline)
        if not token(cmd.token) then return end
        if held and held.cmd.token == cmd.token then return end
        local epoch = d.epoch()
        deadline = deadline or d.frame() + d.prepare_frames
        if not self:capable() then
            if journal and journal.busy then return hold_journal(cmd,epoch,deadline) end
            return refuse(cmd, "not trade-capable")
        end
        if not d.eligible() then return refuse(cmd, "not eligible") end
        if self:hide_party() then
            if journal and journal.busy then return hold_journal(cmd,epoch,deadline) end
            return refuse(cmd, "party hidden")
        end
        if prior_report_pending(epoch, cmd.token) then return refuse(cmd, "prior report pending") end
        if prepared and prepared.token == cmd.token and prepared.epoch == epoch then
            local t = prepared
            if (t.phase == "preparing" or t.phase == "prepared_visible") and t.old_key == cmd.old_key then return end
            local v = visit()
            local ok = t.old_key == cmd.old_key and v ~= nil and v.id == t.visit
                       and locate(t.old_key) ~= nil and t.prepare_deadline ~= nil and d.frame() <= t.prepare_deadline
            if not ok then unchanged(t) end
            emit("apply_ready", {token=cmd.token, ok=ok}); flush(); return
        end
        if native.prepare_trade then
            local mon = locate(cmd.old_key)
            if not mon then return refuse(cmd, "old_key not in party") end
            if active or prepared then return refuse(cmd, "another trade in flight") end
            if retired_for(epoch, cmd.token) then return refuse(cmd, "token retired") end
            if not native.trade_authorized then return refuse(cmd, "native not authorized") end
            if not native:trade_authorized(cmd.token, cmd.old_key) then
                if held then return refuse(cmd, "another prepare held") end
                held = {cmd=cmd, epoch=epoch, deadline=deadline}
                return
            end
            local t = {token=cmd.token, epoch=epoch, old_key=cmd.old_key, slot=mon.slot, phase="preparing", prepare_pending=true}
            local allocated, why = journal:allocate()
            if not allocated then
                if why == "trade journal lock busy" then return hold_journal(cmd,epoch,deadline) end
                return refuse(cmd, "journal allocate failed")
            end
            t.journal_epoch = allocated
            prepared = t
            t.prepare_job = native:prepare_trade({token=t.token, slot=t.slot, old_key=t.old_key,
                dispatch_deadline=deadline}, function(why, w)
                if prepared ~= t or t.phase ~= "preparing" then return end
                if t.epoch ~= d.epoch() then unchanged(t); flush(); return end
                if why or type(w) ~= "table" or w.accepted ~= true or w.pre_saved ~= true or w.apply_open ~= true
                   or w.id == nil or w.old_key ~= t.old_key then
                    unchanged(t); flush(); return
                end
                t.visit = w.id
                t.prepare_deadline = d.frame() + d.prepare_frames
                if d.publish_visible_snapshot then
                    t.phase = "prepared_visible" -- first send a live visible party census
                else
                    t.phase, t.prepare_pending = "prepared", nil
                    emit("apply_ready", {token=t.token, ok=true}, t.epoch); flush()
                end
            end, function()
                local current = locate(t.old_key)
                if prepared ~= t or t.phase ~= "preparing" or t.epoch ~= d.epoch() or not d.eligible() or not current
                   or current.slot ~= t.slot then return false, "guard:stale" end
                return true
            end)
            if not t.prepare_job and prepared == t then unchanged(t); flush() end
            return
        end
        local v, mon = visit(), locate(cmd.old_key)
        local ok = v ~= nil and mon ~= nil and active == nil and prepared == nil and retired_for(epoch, cmd.token) == nil
        if ok then
            local durable_epoch, why = journal:allocate()
            if not durable_epoch then
                if why == "trade journal lock busy" then return hold_journal(cmd,epoch,deadline) end
                emit("apply_ready", {token=cmd.token, ok=false}); flush(); return
            end
            prepared = {token=cmd.token, epoch=epoch, old_key=cmd.old_key, slot=mon.slot, visit=v.id, phase="prepared",
                        prepare_deadline=deadline, journal_epoch=durable_epoch}
        end
        emit("apply_ready", {token=cmd.token, ok=ok})
        flush()
    end
    function self:withdraw(cmd)
        if prepared and prepared.token == cmd.token then
            local t = prepared
            unchanged(t)
        end
        local t = active
        if t and t.token == cmd.token then
            if may_commit(t) then
                uncertain(t, "withdrawal after possible commit")
            else
                unchanged(t)
            end
        end
        flush()
    end
    function self:reset()
        local waiting = held
        held = nil
        if journal then journal:discontinuity() end
        recovery_reports = {}
        if prepared then
            local t = prepared
            unchanged(t)
        end
        local t = active
        if t and may_commit(t) then
            t.requires_reload, t.reload_seen, t.evidence_allowed = true, false, false
            declare_uncertain(t, "reset after possible commit")
        elseif t then
            unchanged(t)
        end
        for epoch, records in pairs(uncertain_records) do
            for value, record in pairs(records) do
                if not record.reload_seen and ((record.requires_reload and not record.evidence_allowed)
                   or report_pending(epoch, value)) then
                    record.requires_reload, record.reload_seen, record.evidence_allowed = true, false, false
                    declare_uncertain(record, "reset after uncertainty")
                end
            end
        end
        if waiting then refuse(waiting.cmd, "cartridge reset", waiting.epoch) end
    end
    function self:apply(cmd)
        if not token(cmd.token) then return end
        local t = prepared
        local previous = retired_for(d.epoch(), cmd.token)
        if previous then emit("trade_done", previous); flush(); return end
        if not t or t.phase ~= "prepared" or active or t.token ~= cmd.token or t.old_key ~= cmd.old_key then return end
        if t.epoch ~= d.epoch() then unchanged(t); flush(); return end
        -- Every facade call is preflight: a decoder, identity or eligibility error
        -- must retire the preparation rather than strand it without a response.
        local ok, mon, party, partner_key = pcall(function()
            local v = visit()
            local outgoing, rows = locate(t.old_key)
            if not v or v.id ~= t.visit or not outgoing or d.frame() > t.prepare_deadline then return end
            if type(cmd.blob_hex) ~= "string" or #cmd.blob_hex ~= 2*d.mon_size or cmd.blob_hex:find("[^%x]") then return end
            local incoming = d.decode_blob(cmd.blob_hex)
            if type(incoming) ~= "table" or incoming.checksum_ok == false
               or not native:trade_eligible(incoming) then return end
            local incoming_key = d.key(incoming)
            if not token(incoming_key) then return end
            for _, m in ipairs(rows) do
                if d.key(m) == incoming_key then return end
            end
            return outgoing, rows, incoming_key
        end)
        if not ok or not mon then unchanged(t); flush(); return end
        t.partner_key = partner_key
        t.party_count = #party
        t.slot, t.blob_hex, t.phase = mon.slot, cmd.blob_hex, "wait"
        -- Acceptance starts a separate dispatch budget. A published scene is then owned
        -- by native's completion/timeout protocol, never by the old readiness deadline.
        t.apply_deadline = d.frame() + d.apply_frames
        prepared, active = nil, t
    end
    function self:tick()
        flush()
        if held then
            local h = held
            if h.epoch ~= d.epoch() or d.frame() > h.deadline then
                held = nil; refuse(h.cmd, h.journal_busy and "trade journal lock busy" or "native not authorized")
            elseif h.journal_busy or native:trade_authorized(h.cmd.token, h.cmd.old_key) then
                held = nil; self:prepare(h.cmd,h.deadline)
            end
        end
        if prepared and prepared.epoch ~= d.epoch() then unchanged(prepared); flush() end
        if prepared and prepared.prepare_deadline and d.frame() > prepared.prepare_deadline then unchanged(prepared); flush() end
        if prepared and prepared.phase == "prepared_visible" and d.ready_to_send() and d.clear()
           and d.publish_visible_snapshot() == true then
            local t = prepared
            t.phase, t.prepare_pending = "prepared", nil
            emit("apply_ready", {token=t.token, ok=true}, t.epoch)
            flush()
        end
        local t = active
        if t and (t.phase == "precommit-proof" or t.phase == "save-proof" or t.phase == "arm-proof") then
            if t.epoch ~= d.epoch() then
                if t.phase == "arm-proof" then unchanged(t) -- no intent or scene was published
                else uncertain(t, "session epoch changed before journal proof") end
            elseif t.phase == "arm-proof" and d.frame() > t.apply_deadline then unchanged(t)
            elseif t.phase == "arm-proof" then post_scene(t)
            elseif t.phase == "precommit-proof" then unchanged(t)
            else result(t) end
            flush()
            return
        end
        if not t or t.phase ~= "wait" then return end
        if t.epoch ~= d.epoch() or d.frame() > t.apply_deadline then unchanged(t); flush(); return end
        if not d.eligible() or not d.clear() then return end
        t.phase = "stage"
        t.stage_job = native:transfer("enemy", {blobs_hex={t.blob_hex}, dispatch_deadline=t.apply_deadline}, function(why)
            if active ~= t then return end
            t.stage_done = true
            if t.phase == "draining" then return unchanged(t) end
            if t.phase ~= "stage" then return end
            if why then unchanged(t) else post_scene(t) end
        end, function()
            if active == t and t.epoch ~= d.epoch() then return false, "guard:epoch" end
            if active == t and d.frame() > t.apply_deadline then return false, "guard:apply_expired" end
            local v = visit()
            if active ~= t or not v or v.id ~= t.visit or not locate(t.old_key) then return false, "guard:stale" end
            return true
        end)
        if not t.stage_job and active == t then unchanged(t) end
    end
    function self:hide_party()
        if journal and journal:hidden() then return true end
        if not journal and native.trade_capable and native:trade_capable() then return true end
        for _, records in pairs(uncertain_records) do
            for _, t in pairs(records) do
                if t.requires_reload and not t.evidence_allowed then return true end
            end
        end
        return false
    end
    function self:allow_reload_evidence(value, epoch)
        local record = (uncertain_records[epoch or d.epoch()] or {})[value]
        if record and record.reload_seen and journal and journal:declared(value,record.journal_epoch) == true then
            record.evidence_allowed = true
        end
    end
    function self:reloaded(value, epoch)
        local record = (uncertain_records[epoch or d.epoch()] or {})[value]
        return record and record.reload_seen == true or false
    end
    function self:outstanding() return journal and journal:outstanding() or nil end
    function self:qualify_reload(proof)
        if not journal or journal:qualify(proof) ~= true then return false end
        local records = journal:qualified_records() or {}
        for _, r in ipairs(records) do
            local id = tostring(r.epoch) .. ":" .. r.token
            if not recovery_reports[id] then
                recovery_reports[id] = true
                for _, by_token in pairs(uncertain_records) do
                    local old = by_token[r.token]
                    if old and old.journal_epoch == r.epoch then old.reload_seen = true end
                end
                emit("trade_done", {token=r.token, uncertain=true, after_reset=true}, r.epoch)
            end
        end
        return #records > 0
    end
    function self:declaration_sent(value)
        if not journal then return false end
        local candidates = {}
        for _, r in ipairs(journal:qualified_records() or {}) do
            if r.token == value then candidates[#candidates+1] = r end
        end
        if #candidates ~= 1 then return false end
        local r = candidates[1]
        if journal:declared(r.token,r.epoch) ~= true then return false end
        for _, by_token in pairs(uncertain_records) do
            local old = by_token[value]
            if old and old.journal_epoch == r.epoch then old.evidence_allowed = true end
        end
        return true
    end
    function self:server_final(cmd)
        if not journal then return false end
        return journal:final(cmd.token,cmd.epoch,cmd.verdict)
    end
    function self:capable()
        return journal ~= nil and journal:ready() and native.trade_capable and native:trade_capable() == true
    end
    function self:state()
        if active then
            active.posted = (active.stage_job and active.stage_job.posted == true)
                            or (active.scene_job and active.scene_job.posted == true) or false
            active.possibly_posted = active.scene_job and active.scene_job.publish_attempted == true or false
        end
        return active, prepared
    end
    return self
end
return T
