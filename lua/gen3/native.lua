-- RR companion ABI v1, injected and single-owner. No emulator globals.
-- ABI offsets/statuses: patch/src/ADDRESSES.md:366ff; handlers.c Mailbox/ack.
-- Absolute addresses/opcodes come only from the full pack's profile.native.
-- N.new(pack, {io, writes, reads, send(event, fields), in_battle,
--             artifact_kind, panel_closed(), panel_stage_safe(),
--             array(table)?, timeout_frames?, initial_seq?}).
-- The rival swap needs no gBattleMons refresh: OP_RIVAL_SWAP lands before the engine's selection.
-- panel_closed returns (closed, result) for the start-menu-driven panel. Its
-- caller binds field/script/result facts; no legacy literal address is imported.
--
-- DISPATCH RECEIPT (C5-7): the job table is the handle. enqueue/transfer return it, and
-- service() sets `job.posted = true` on it exactly when that job's opcode is published --
-- the last byte written, after every stage has landed. A refused, guarded-off, failed or
-- merely queued job never carries the flag, and a stage-only job (config) has no opcode to
-- publish, so it carries no receipt either. Callers must read "did my op reach the mailbox"
-- from this flag, never from a sink byte count: a completion callback (e.g. the rival swap's
-- readback) or a panel/NPC callback can write through the same sink in the same
-- service() call while the caller's own job is refused at its guard.
local N = {}
local CALL_IDS = {fallen=1, dead_zone=2, first_link=3}
local CALL_BIND_ATTEMPTS = 3
local O = {abi=4, opcode=6, seq=8, status=10, ack=12, reason=14, args=16, result=48}
local BUSY, OK, FAIL = 1, 2, 3
-- ST_FAIL reason words (patch/src/handlers.c owns the numbering; ADDRESSES.md lists both sides).
-- Only the named ones matter to Lua: an unknown word stays unnamed rather than being guessed.
local FAIL_REASONS = {[1] = "script_context", [2] = "bad_args", [3] = "not_on_field",
                      [8] = "window_closed"}
local function integer(v, maximum)
    return type(v) == "number" and v % 1 == 0 and v >= 0 and v <= maximum
end
local function clone(t)
    local out = {}
    for k, v in pairs(t) do out[k] = type(v) == "table" and clone(v) or v end
    return out
end

-- The cartridge's OWN live companion mailbox (signature at BASE, ABI at BASE+4), read from RAM whatever
-- artifact kind the launcher named. The server's refusal gate (GameRulesAdapter.companion_refusal) needs
-- evidence from the cartridge, not the launcher's own claim (owner 2026-10-02, review F1/F2): a randomized
-- cartridge declares "rand" on the wire either way. `nat` is the pack's profile.native; `io` reads RAM.
-- Returns the ABI the pack pins when the mailbox is live, else nil. Reads nothing when the pack has no native.
function N.companion_live(nat, io)
    if type(nat) ~= "table" or type(nat.BASE) ~= "number" or type(nat.SIG) ~= "number" or type(nat.ABI) ~= "number" then
        return nil
    end
    local ok, live = pcall(function()
        return io.read_u32(nat.BASE) == nat.SIG and io.read_u16(nat.BASE + O.abi) == nat.ABI
    end)
    return ok and live == true and nat.ABI or nil
end

function N.new(profile, deps)
    local p = assert(profile.native, "profile.native required")
    assert(p.ABI == 1 or p.ABI == 2, "unsupported native ABI")
    local v2 = p.ABI == 2 and assert(p.abi_v2, "generated v2 ABI required") or nil
    local offsets, fail_reasons = clone(O), clone(FAIL_REASONS)
    local trade_fields, trade_phase_offset
    if v2 then
        local c = assert(v2.constants, "v2 constants required")
        assert(c.SLINK_ABI_VERSION == p.ABI and c.SLINK_SIGNATURE == p.SIG, "v2 identity mismatch")
        local fields = assert(v2.structs.SlinkMailboxV2.fields, "v2 mailbox layout required")
        for alias, spec in pairs({signature={"signature",4,1,0x00},
            abi={"abi_version",2,1,0x04}, opcode={"opcode",2,1,0x06},
            seq={"seq",2,1,0x08}, status={"status",2,1,0x0A},
            ack={"ack_seq",2,1,0x0C}, reason={"reason",2,1,0x0E},
            args={"args",1,32,0x10}, result={"result",1,16,0x30},
            capabilities={"capabilities",4,1,0x40}, session_epoch={"session_epoch",4,1,0x44}}) do
            local field = assert(fields[spec[1]], "missing v2 mailbox field")
            assert(field.offset == spec[4], "invalid v2 mailbox field offset: " .. spec[1])
            assert(integer(field.offset, v2.structs.SlinkMailboxV2.size - field.width * field.count)
                   and field.width == spec[2] and field.count == spec[3], "invalid v2 mailbox field")
            offsets[alias] = field.offset
        end
        for name, value in pairs(p) do
            if type(name) == "string" and name:match("^OP_") then
                local expected = c["SLINK_" .. name]
                assert(type(expected) == "number" and value == expected, "v2 opcode mismatch: " .. name)
            end
        end
        for name, value in pairs(c) do
            if name:match("^SLINK_OP_") then
                assert(p[name:sub(7)] == value, "v2 opcode missing: " .. name)
            end
        end
        local witness = assert(v2.structs.SlinkTradeWitnessV2, "trade witness layout required")
        assert(witness.size == 0x50, "invalid trade witness size")
        trade_fields = {}
        for name, spec in pairs({session_epoch={0x00,4,1}, visit_id={0x04,4,1}, token={0x08,1,16},
            revision={0x18,2,1}, visit_flags={0x1A,2,1}, milestones={0x1C,4,1}, milestone_seq={0x20,2,5},
            final_result={0x2A,1,1}, save_status={0x2B,1,1}, milestone_frame={0x2C,4,5},
            old_pid={0x40,4,1}, old_otid={0x44,4,1}, received_pid={0x48,4,1}, received_otid={0x4C,4,1}}) do
            local field = assert(witness.fields[name], "missing trade witness field: " .. name)
            assert(field.offset == spec[1] and field.width == spec[2] and field.count == spec[3],
                   "invalid trade witness field: " .. name)
            trade_fields[name] = field.offset
        end
        -- These words have different legacy RR meanings. Name them only for v2.
        for symbol, name in pairs({SLINK_REASON_UNCERTAIN="uncertain",
            SLINK_REASON_IDENTITY="identity", SLINK_REASON_CLIENT_TOO_OLD="client_too_old"}) do
            fail_reasons[assert(c[symbol], "missing v2 failure reason")] = name
        end
        -- Older v2 profiles remain usable for their other capabilities. Durable
        -- FR trade requires the T2-R1 phase field and constants in its profile.
        local phase = fields.producer_phase
        if phase then
            assert(phase.offset == 0x48 and phase.width == 4 and phase.count == 1,
                   "invalid trade producer phase field")
            for name, value in pairs({SLINK_PHASE_IDLE=0, SLINK_PHASE_PRE_SAVE=1,
                SLINK_PHASE_READY=2, SLINK_PHASE_SCENE=3, SLINK_PHASE_DONE=4,
                SLINK_PHASE_UNCERTAIN=5, SLINK_REASON_WITHDRAW_TOO_LATE=14,
                SLINK_CAP_DURABLE_TRADE=1, SLINK_WITNESS_OFFSET=0x50,
                SLINK_SUCCESS_MILESTONES=0x1F, SLINK_SAVE_OK=1}) do
                assert(c[name] == value, "invalid trade ABI constant: " .. name)
            end
            trade_phase_offset = phase.offset
            fail_reasons[c.SLINK_REASON_WITHDRAW_TOO_LATE] = "withdraw_too_late"
        end
    end
    -- RR-DURABLE (Codex Emerald ruling P3): Radical Red stays ABI1 (its legacy reasons 11/12
    -- keep their meaning). Its durable trade is an ISOLATED descriptor: the shared FR/LG
    -- producer's shadow SlinkMailboxV2 at profile native.TRADE_BASE (patch/src/rr_trade_relay.h)
    -- holds capabilities/session_epoch/producer_phase at +0x40/+0x44/+0x48 and the witness at
    -- +0x50; commands still travel through the ABI1 mailbox at BASE. The witness layout and
    -- constants below are abi.h's, the same literals the v2 path asserts its generated ABI against.
    -- Bound only for the admitted production RR companion (lua/gen3/entry.lua): any other ABI1
    -- world keeps the v1 transport, whose raw 16/18/21 trade paths the durable patch refuses.
    local rr_durable = not v2 and profile.pack == "gen3_rr" and deps.title == "radical_red"
        and deps.production == true and integer(p.TRADE_BASE, 0x0203FFAF)
        and p.TRADE_BASE >= p.BASE + 0x50 or false
    local rr_trade_constants
    if rr_durable then
        local d = p.TRADE_BASE - p.BASE
        offsets.capabilities, offsets.session_epoch, trade_phase_offset = d + 0x40, d + 0x44, d + 0x48
        trade_fields = {session_epoch=0x00, visit_id=0x04, token=0x08, revision=0x18, visit_flags=0x1A,
            milestones=0x1C, milestone_seq=0x20, final_result=0x2A, save_status=0x2B,
            milestone_frame=0x2C, old_pid=0x40, old_otid=0x44, received_pid=0x48, received_otid=0x4C}
        rr_trade_constants = {SLINK_PHASE_IDLE=0, SLINK_PHASE_PRE_SAVE=1, SLINK_PHASE_READY=2,
            SLINK_PHASE_SCENE=3, SLINK_PHASE_DONE=4, SLINK_PHASE_UNCERTAIN=5,
            SLINK_REASON_WITHDRAW_TOO_LATE=14, SLINK_CAP_DURABLE_TRADE=1, SLINK_WITNESS_OFFSET=0x50,
            SLINK_SUCCESS_MILESTONES=0x1F, SLINK_SAVE_OK=1}
        fail_reasons[14] = "withdraw_too_late" -- unused by any RR v1 opcode
    end
    local epoch_mailbox = v2 ~= nil or rr_durable
    local trade_base = rr_durable and p.TRADE_BASE or p.BASE
    local O = offsets
    local session_epoch = 0
    local io, writes = assert(deps.io), assert(deps.writes)
    local send = assert(deps.send, "send(event, fields) required")
    local array = deps.array or function(t) return t end
    local reads = assert(deps.reads, "Gen 3 read facade required")
    local queue, pending, poisoned, posting = {}, nil, nil, false
    local trade_context, trade_blocked, trade_wanted_epoch, trade_dead_notice
    local trade_binding_job, trade_serial = nil, 0
    local fr_v2 = v2 and (profile.pack == "gen3_frlg"
        or (profile.pack == "gen3_emerald" and deps.title == "emerald" and deps.production == true))
        or rr_durable
    local frlg_title = (profile.pack == "gen3_frlg" and (deps.title == "firered" or deps.title == "leafgreen"))
        or (profile.pack == "gen3_emerald" and deps.title == "emerald")
        or rr_durable
    local control
    if v2 and fr_v2 and frlg_title and deps.production == true then
        local c, layout = v2.constants, assert(v2.structs.SlinkControlV2, "CONTROL layout required")
        assert(layout.size == 16 and c.SLINK_PI_COUNT_WIDTH == 4, "invalid CONTROL layout")
        for name, expected in pairs({session_epoch={0,4},pi_count={4,4},tn_enable={8,1}}) do
            local field = assert(layout.fields[name], "missing CONTROL field")
            assert(field.offset == expected[1] and field.width == expected[2] and field.count == 1,
                   "invalid CONTROL field: " .. name)
        end
        control = {base=p.BASE+c.SLINK_CONTROL_OFFSET,epoch=layout.fields.session_epoch.offset,
                   counter=layout.fields.pi_count.offset,enable=layout.fields.tn_enable.offset}
    end
    local call_queued, call_active, call_wanted_epoch
    local call_handshake_job, call_binding_attempts, call_binding_failed = nil, 0, false
    local call_first_posted, call_delivered_at = false, nil
    local log = deps.log or function() end
    -- post-conditions awaited after an ACK (the rival swap's engine snapshot)
    local watches, SNAPSHOT_FRAMES = {}, 600
    local seq = deps.initial_seq or 0
    assert(integer(seq, 65535), "invalid initial sequence")
    local last_frame, was_present, npc_count, panel_drawn
    local npc_enabled, sounds_enabled = false, true
    local panel_rows, panel_page, panel_showing = {}, 0, false
    local panel_dirty, panel_reopen = false, false
    -- Per-op ACK deadlines, each LONGER than the patch's own timeout for that op so the patch's
    -- ST_FAIL normally wins the race (patch/src/handlers.c drive_ui :1030-1072):
    --   sync ops (acked the frame they run)              1800
    --   UI kind 1 SHOW_MENU/SHOW_CHOICES/SHOW_INFO        60 start + 1800 answer -> 2400
    --   UI kind 2 CHOOSE_PARTY_MON                        1800 -> 2400
    --   UI kind 3 TRADE_SCENE                             180 start + 5400 scene -> 6000
    -- deps.timeout_frames (a harness knob) overrides every op with one value.
    local SYNC_TIMEOUT = 1800
    local op_timeout = {}
    for name, frames in pairs({OP_SHOW_MENU = 2400, OP_SHOW_CHOICES = 2400, OP_SHOW_INFO = 2400,
                               OP_CHOOSE_PARTY_MON = 2400, OP_TRADE_PREPARE = 2400, OP_TRADE_SCENE = 6000}) do
        if p[name] then op_timeout[p[name]] = frames end
    end
    local function timeout_for(op) return deps.timeout_frames or op_timeout[op] or SYNC_TIMEOUT end
    local self = {}

    local function present()
        if (not v2 and profile.pack ~= "gen3_rr") or deps.artifact_kind ~= "companion" then return false end
        local ok, value = pcall(function()
            return io.read_u32(p.BASE) == p.SIG and io.read_u16(p.BASE + O.abi) == p.ABI
        end)
        return ok and value == true
    end
    function self:mailbox()
        if not present() then return nil, "native absent" end
        local ok, fields = pcall(function()
            local out = {abi=p.ABI, opcode=io.read_u16(p.BASE + O.opcode),
                seq=io.read_u16(p.BASE + O.seq), status=io.read_u16(p.BASE + O.status),
                ack_seq=io.read_u16(p.BASE + O.ack), reason=io.read_u16(p.BASE + O.reason)}
            out.reason_name = fail_reasons[out.reason]
            if epoch_mailbox then
                out.capabilities = io.read_u32(p.BASE + O.capabilities)
                out.session_epoch = io.read_u32(p.BASE + O.session_epoch)
                if trade_phase_offset then out.producer_phase = io.read_u32(p.BASE + trade_phase_offset) end
            end
            return out
        end)
        if not ok then return nil, "native mailbox unreadable" end
        return fields
    end
    function self:idle()
        if poisoned or pending or panel_showing then return false end
        if not present() then return true end -- absence is safe for the Lua fallback
        local ok, value = pcall(function()
            return io.read_u16(p.BASE + O.opcode) == 0
                   and (posting or io.read_u16(p.BASE + O.status) ~= BUSY)
                   and (v2 ~= nil or io.read_u8(p.INFO + 1) == io.read_u8(p.INFO + 2))
        end)
        return ok and value == true
    end
    function self:trade_active() return trade_context ~= nil and not trade_context.reconciled end
    function self:hello_fields() return {} end -- no invented wire capability fields
    local function finish(job, why, result, reason)
        if job == trade_binding_job then trade_binding_job = nil end
        if job.done then job.done(why, result, reason) end
    end
    function self:cancel(handle)
        for i, job in ipairs(queue) do
            if job == handle then
                table.remove(queue, i)
                job.cancelled = true
                finish(job, "guard:stale")
                return true
            end
        end
        return false -- published operations belong to the cartridge; never erase one
    end

    local function abort(why)
        local active, waiting = pending, queue
        pending, queue = nil, {}
        trade_binding_job = nil
        call_queued, call_active = nil, nil -- no replay across a native reset/freshness loss
        if active then finish(active, why) end
        for _, job in ipairs(waiting) do finish(job, why) end
    end
    local function check_epoch()
        if not epoch_mailbox then return true end
        local ok, value = pcall(io.read_u32, p.BASE + O.session_epoch)
        if not ok or not integer(value, 0xFFFFFFFF) then
            poisoned = "native epoch unreadable"
            abort(poisoned)
            return nil, poisoned
        end
        if value == 0 then
            if session_epoch ~= 0 or poisoned then
                if fr_v2 and (trade_context or trade_blocked) then
                    trade_blocked = "trade epoch cleared while owned"
                    poisoned = "trade epoch cleared while owned"
                    abort(poisoned)
                    return nil, poisoned
                end
                -- Native explicitly unarmed the mailbox. Retire old work, then
                -- allow only a new handshake, including recovery from poison.
                session_epoch, poisoned = 0, nil
                abort("native epoch cleared")
            end
        elseif session_epoch ~= 0 and value ~= session_epoch then
            if fr_v2 then trade_blocked = "native epoch changed" end
            poisoned = "native epoch changed"
            abort(poisoned)
            return nil, poisoned
        end
        return true
    end
    local function enqueue(job)
        if not present() then finish(job, "native absent"); return nil, "native absent" end
        local epoch_ok, epoch_why = check_epoch()
        if not epoch_ok then finish(job, epoch_why); return nil, epoch_why end
        if poisoned then finish(job, poisoned); return nil, poisoned end
        if v2 and not job.handshake and session_epoch == 0 then
            finish(job, "client_too_old"); return nil, "client_too_old"
        end
        if #queue >= 64 then finish(job, "native queue full"); return nil, "native queue full" end
        queue[#queue + 1] = job
        -- the job IS the handle: `job.posted` is this job's dispatch receipt (see the header)
        return job
    end
    function self:set_session_epoch(value)
        if not epoch_mailbox then return nil, "unsupported native ABI" end
        if not integer(value, 0xFFFFFFFF) or value == 0 then return nil, "invalid session epoch" end
        if not present() then return nil, "native absent" end
        local epoch_ok, epoch_why = check_epoch()
        if not epoch_ok then return nil, epoch_why end
        if pending or #queue > 0 or not self:idle() then return nil, "native busy" end
        if fr_v2 and not self:trade_epoch_writable() then return nil, "trade producer owned or unavailable" end
        local bytes = {}
        for i=0,3 do bytes[i+1] = (value >> (8*i)) & 0xFF end
        local stages = {{p.BASE + O.session_epoch, bytes}}
        if control then
            stages[#stages+1] = {control.base+control.epoch, clone(bytes)}
            stages[#stages+1] = {control.base+control.enable, {npc_enabled and 1 or 0}}
        end
        return enqueue({handshake=true, stages=stages,
            valid=function() return not fr_v2 or self:trade_epoch_writable(), "trade producer owned or unavailable" end,
            done=function(why) if not why then session_epoch=value end end})
    end
    local function encode(text, limit)
        local out = {}
        -- The shared read facade is the sole character map, including multi-byte glyphs.
        local codes = reads.charmap.codes
        for _, codepoint in utf8.codes(tostring(text or "")) do
            if #out >= limit - 1 then break end
            local glyph = utf8.char(codepoint)
            out[#out + 1] = glyph == "\n" and 0xFE or codes[glyph] or 0
        end
        out[#out + 1] = reads.charmap.terminator
        return out
    end
    local function allow_for(job)
        local ranges = {{p.BASE + O.opcode, p.BASE + O.result}}
        for _, stage in ipairs(job.stages or {}) do
            ranges[#ranges + 1] = {stage[1], stage[1] + #stage[2]}
        end
        return function(addr, n)
            for _, range in ipairs(ranges) do
                if addr >= range[1] and addr + n <= range[2] then return true end
            end
            return false
        end
    end
    local function stage_intact(job)
        for _, stage in ipairs(job.stages or {}) do
            local bytes = io.read_bytes(stage[1], #stage[2])
            for i, b in ipairs(stage[2]) do if bytes[i] ~= b then return false end end
        end
        for i, b in ipairs(job.args or {}) do
            if io.read_u8(p.BASE + O.args + i - 1) ~= b then return false end
        end
        return true
    end
    local function dispatch(job)
        -- No caller can touch staging before owning the idle mailbox. Snapshots
        -- are copied on enqueue and staged only here, never in a command method.
        if job.panel then
            job.stages[#job.stages][2][1] = (io.read_u8(p.INFO + 6) + 1) % 256
        end
        local armed, why = pcall(function() writes:arm("native", allow_for(job)) end)
        if not armed then return nil, "native arm refused: " .. tostring(why) end
        posting = true
        local ok, err = pcall(function()
            assert(present() and io.read_u16(p.BASE + O.opcode) == 0,
                   "native changed before dispatch")
            for _, stage in ipairs(job.stages or {}) do
                assert(stage[1]+#stage[2] <= p.BASE+O.opcode or stage[1] >= p.BASE+O.opcode+2,
                       "opcode must be published last, never staged")
            end
            for _, stage in ipairs(job.stages or {}) do writes:write_bytes(stage[1], stage[2]) end
            if job.op then
                seq = (seq + 1) % 65536
                job.seq = seq
                if #job.args > 0 then writes:write_bytes(p.BASE + O.args, job.args) end
                -- No Lua-side status=BUSY: the patch sets MB->status itself on every ack (OK/FAIL,
                -- and ST_BUSY for the async ops), and ack_seq = seq-1 below already keeps a stale
                -- OK of the previous op from reading as this op's completion (completion needs
                -- ack == seq). Writing BUSY here also made writes.lua's per-write-call recheck (the window is frame-bound) of the
                -- native_idle clause (status ~= busy) refuse the next write call.
                writes:write_u16(p.BASE + O.ack, (seq + 65535) % 65536)
                writes:write_u16(p.BASE + O.seq, seq)
                job.publish_attempted = true -- a sink failure can leave a valid low-byte opcode
                if job.on_publish then job.on_publish() end
                writes:write_u16(p.BASE + O.opcode, job.op) -- publish last
                writes:disarm() -- publication closes the window; no later write call is permitted
                -- ... and the receipt is the publish's own witness: nothing after it can fail
                job.posted = true
            end
        end)
        posting = false
        writes:disarm()
        if not ok then
            poisoned = "native dispatch interrupted"
            finish(job, poisoned)
            return false, tostring(err)
        end
        if job.op then job.started = io.framecount(); pending = job
        else finish(job) end
        return true
    end

    -- Soul Link Match Call mirrors gen2/phone.lua's optional tags and scheduling.
    -- The native Emerald producer owns the contact, text and safe UI entry.
    local cc = v2 and v2.constants or rr_trade_constants
    local call_title = profile.titles and profile.titles.emerald
    local species_table = call_title and call_title.rom_tables and call_title.rom_tables.gSpeciesInfo
    local species_count = species_table and species_table.count
    local call_species_limit = integer(species_count, 0x10000) and species_count > 1 and species_count - 1 or 0
    local call_known_caps = 0
    for name, value in pairs(cc or {}) do
        if name:match("^SLINK_CAP_") then call_known_caps = call_known_caps | value end
    end
    local call_record_fields, call_witness_fields
    if v2 then
        local function layout(name, size, expected)
            local shape = assert(v2.structs[name], "missing call ABI structure")
            assert(shape.size == size, "invalid call ABI size")
            local out = {}
            for name_, spec in pairs(expected) do
                local field = assert(shape.fields[name_], "missing call ABI field")
                assert(field.offset == spec[1] and field.width == spec[2] and field.count == spec[3],
                       "invalid call ABI field: " .. name_)
                out[name_] = field.offset
            end
            return out
        end
        call_record_fields = layout("SlinkCallRecordV2", 36, {
            event={0,1,1}, has_names={1,1,1}, caller_species={2,2,1}, receiver_species={4,2,1},
            trainer={6,1,8}, caller_nick={14,1,11}, receiver_nick={25,1,11}})
        call_witness_fields = layout("SlinkCallWitnessV2", 32, {
            session_epoch={0,4,1}, seq={4,2,1}, revision={6,2,1}, phase={8,1,1},
            event={9,1,1}, reason={10,2,1}, armed_frame={12,4,1}, delivered_frame={16,4,1}})
    end
    local function call_supported()
        if not v2 or profile.pack ~= "gen3_emerald" then return false end
        local mb = self:mailbox()
        local caps = mb and mb.capabilities
        return integer(caps, 0xFFFFFFFF) and (caps & ~call_known_caps) == 0
               and (caps & cc.SLINK_CAP_MATCH_CALL) ~= 0
    end
    function self:match_call_capable()
        if not call_supported() or poisoned or session_epoch == 0 then return false end
        if call_binding_failed or (call_wanted_epoch and call_wanted_epoch ~= session_epoch) then return false end
        local mb = self:mailbox()
        return mb ~= nil and mb.session_epoch == session_epoch
    end
    function self:bind_match_call_session(value)
        if not v2 or profile.pack ~= "gen3_emerald" or deps.artifact_kind ~= "companion"
           or not integer(value, 0xFFFFFFFF) or value == 0 then return false end
        if call_wanted_epoch ~= value then call_queued, call_active = nil, nil end
        if call_handshake_job then self:cancel(call_handshake_job); call_handshake_job = nil end
        call_binding_attempts, call_binding_failed = 0, false
        call_wanted_epoch = value
        return true
    end
    local function call_name(text, size)
        if type(text) ~= "string" then return nil end
        local out = {}
        for glyph in text:gmatch(utf8.charpattern) do
            local code = reads.charmap.codes[glyph]
            if integer(code, 0xF6) and #out < size - 1 then out[#out+1] = code end
        end
        if #out == 0 then return nil end
        while #out < size do out[#out+1] = 0xFF end
        return out
    end
    local function call_record(id, data)
        local row, rf = {}, call_record_fields
        for i=1,36 do row[i] = i > rf.trainer and 0xFF or 0 end
        row[rf.event+1] = id
        local trainer = type(data) == "table" and call_name(data.trainer_name, 8)
        if not trainer then return row end
        row[rf.has_names+1] = 1
        for i, b in ipairs(trainer) do row[rf.trainer+i] = b end
        for _, item in ipairs({{"caller_mon", "caller_species", "caller_nick"},
                               {"receiver_mon", "receiver_species", "receiver_nick"}}) do
            local mon = type(data[item[1]]) == "table" and data[item[1]] or {}
            local species = integer(mon.species_id, call_species_limit) and mon.species_id > 0 and mon.species_id or 0
            row[rf[item[2]]+1], row[rf[item[2]]+2] = species & 0xFF, species >> 8
            for i, b in ipairs(call_name(mon.nickname, 11) or {}) do row[rf[item[3]]+i] = b end
        end
        return row
    end
    local function word(bytes, offset, width)
        local value = 0
        for i=width,1,-1 do value = value * 256 + bytes[offset+i] end
        return value
    end
    -- FR/LG durable trade, ABI2 contract; each admitted title supplies its own bindings.
    -- A beacon is not consent. This binding owns one opaque token/visit and
    -- accepts only coherent, identity- and command-bound native witnesses.
    local function trade_supported()
        if not fr_v2 or not frlg_title or deps.production ~= true or not trade_phase_offset then return false end
        local mb = self:mailbox()
        return mb and integer(mb.capabilities, 0xFFFFFFFF)
            and (mb.capabilities & ~call_known_caps) == 0
            and (mb.capabilities & cc.SLINK_CAP_DURABLE_TRADE) ~= 0 or false
    end
    function self:trade_capable()
        if not trade_supported() or poisoned or trade_blocked or session_epoch == 0 then return false end
        local mb = self:mailbox()
        return mb ~= nil and mb.session_epoch == session_epoch
            and (not trade_wanted_epoch or trade_wanted_epoch == session_epoch)
            and integer(mb.producer_phase, cc.SLINK_PHASE_UNCERTAIN)
            and mb.producer_phase ~= cc.SLINK_PHASE_UNCERTAIN
    end
    function self:trade_epoch_writable()
        if not trade_supported() or trade_blocked or trade_context then return false end
        local ok, reconciled = pcall(function()
            return deps.trade_recovery_clear and deps.trade_recovery_clear() == true
        end)
        if not ok or not reconciled then return false end
        local mb = self:mailbox()
        -- DONE is not reusable by an epoch rewrite. A consumed/reconciled
        -- terminal result permits another PREPARE in the SAME session only.
        return mb ~= nil and mb.producer_phase == cc.SLINK_PHASE_IDLE
    end
    function self:bind_trade_session(value)
        if not fr_v2 or not frlg_title or deps.production ~= true
           or not integer(value, 0xFFFFFFFF) or value == 0 or trade_context or trade_blocked then return false end
        trade_wanted_epoch = value
        return true
    end
    local function trade_safe()
        local ok, safe, why = pcall(function()
            if not deps.trade_safe then return false,"trade field check unavailable" end
            return deps.trade_safe()
        end)
        return ok and safe == true, ok and why or tostring(safe)
    end
    local function trade_dispatch_guard(t, valid, deadline, step)
        -- Ownership, caller authorization and deadlines still expire while a
        -- transient CPU/field checkpoint is closed. Only that checkpoint waits.
        if valid then
            local ok, why, retry = valid()
            if not ok then return false,why,retry end
        end
        if io.framecount() > deadline then return false,"guard:field_expired" end
        local safe, why = trade_safe()
        if not safe then
            why = tostring(why or "overworld checkpoint unavailable")
            local notice = step .. ": " .. why
            if t.wait_reason ~= notice then log("[SLink-gen3] trade waiting for " .. notice) end
            t.wait_reason = notice
            return false,why,true
        end
        t.wait_reason = nil
        return true
    end
    local function trade_deadline(cmd, op)
        local deadline = io.framecount()+timeout_for(op)
        if integer(cmd.dispatch_deadline, 0x1FFFFFFFFFFFFF) then
            deadline = math.min(deadline,cmd.dispatch_deadline)
        end
        return deadline
    end
    local function trade_wait_ready(t, job, deadline)
        local checked
        return function()
            local frame = io.framecount()
            -- Cheap ownership/capability/deadline checks remain per frame.
            if trade_context ~= t or frame > deadline or not self:trade_capable() then return true end
            if trade_safe() then return true end -- dispatch revalidates every identity/witness
            if not checked or frame-checked >= 30 then
                checked = frame
                local ok, _, retry = job.valid()
                if not ok and not retry then return true end
            end
            return false -- skip this held job so other native work can drain
        end
    end
    function self:trade_eligible(mon)
        if not self:trade_capable() or type(mon) ~= "table" then return false end
        if rr_durable then
            -- RR facts: CFRU leaves the BoxPokemon checksum zero (no checksum_ok key, never false),
            -- species ids run past FR's 411 (a u16), and ItemIsMail (0x080980F8) is byte-identical
            -- to FR's in RR, so 121..132 are its mail items too.
            return integer(mon.species, 0xFFFF) and mon.species > 0 and mon.checksum_ok ~= false
                and mon.has_species == 1 and mon.is_bad_egg ~= 1 and mon.is_bad_egg ~= true
                and mon.is_egg ~= 1 and mon.is_egg ~= true and mon.is_egg_flag ~= 1
                and mon.is_egg_flag ~= true and mon.mail == 0xFF
                and integer(mon.held_item, 0xFFFF) and not (mon.held_item >= 121 and mon.held_item <= 132)
        end
        -- Pinned pokefirered include/constants/items.h:121..132 are the
        -- contiguous mail items; MAIL_NONE is 0xFF. These are FR-only facts.
        return integer(mon.species, 411) and mon.species > 0 and mon.checksum_ok == true
            and mon.has_species == 1 and mon.is_bad_egg ~= 1 and mon.is_bad_egg ~= true
            and mon.is_egg ~= 1 and mon.is_egg ~= true and mon.is_egg_flag ~= 1
            and mon.is_egg_flag ~= true and mon.mail == 0xFF
            and integer(mon.held_item, 0xFFFF) and not (mon.held_item >= 121 and mon.held_item <= 132)
    end
    local function outgoing(key, slot)
        local ok, found = pcall(function()
            local party, match = reads.read_party(), nil
            if type(party) ~= "table" or #party < 1 or #party > 6 then return nil end
            for _, mon in ipairs(party) do
                if reads.key(mon) == key then
                    if match or not self:trade_eligible(mon) then return nil end
                    match = mon
                end
            end
            if match and integer(match.slot, 5) and (slot == nil or match.slot == slot) then return match end
        end)
        return ok and found or nil
    end
    local function fresh_trade_slot()
        local mb = self:mailbox()
        return mb and ((not trade_context and mb.producer_phase == cc.SLINK_PHASE_IDLE)
            or (trade_context and trade_context.reconciled and mb.producer_phase == cc.SLINK_PHASE_DONE))
    end
    function self:trade_authorized(token, old_key)
        -- Server authorization is owned by trade.lua's validated command path;
        -- this predicate adds local identity/field/ownership checks, not consent.
        return self:trade_capable() and trade_safe() and type(token) == "string" and #token > 0 and #token <= 256
            and type(old_key) == "string" and old_key:match("^%x%x%x%x%x%x%x%x:%x%x%x%x%x%x%x%x$") ~= nil
            and fresh_trade_slot() and pending == nil and #queue == 0 and outgoing(old_key) ~= nil or false
    end
    local function little(bytes, at, value, size)
        for i=0,size-1 do bytes[at+i+1] = (value >> (8*i)) & 0xFF end
    end
    local function trade_args(t)
        local bytes = {}
        for i=1,32 do bytes[i] = 0 end
        bytes[1], bytes[2] = t.slot, deps.player == "b" and 1 or 0
        little(bytes,4,t.pid,4); little(bytes,8,t.otid,4); little(bytes,12,t.visit,4)
        for i,b in ipairs(t.opaque) do bytes[16+i] = b end
        return bytes
    end
    local function trade_witness(t, final_seq)
        local wf, base = trade_fields, trade_base + cc.SLINK_WITNESS_OFFSET
        local ok, w = pcall(function()
            local before = io.read_u16(base + wf.revision)
            local raw = io.read_bytes(base, 0x50)
            local after = io.read_u16(base + wf.revision)
            if not integer(before,0xFFFF) or before == 0 or before % 2 ~= 0 or before ~= after
               or word(raw,wf.revision,2) ~= before then return nil end
            if word(raw,wf.session_epoch,4) ~= t.epoch or word(raw,wf.visit_id,4) ~= t.visit
               or word(raw,wf.old_pid,4) ~= t.pid or word(raw,wf.old_otid,4) ~= t.otid then return nil end
            for i,b in ipairs(t.opaque) do if raw[wf.token+i] ~= b then return nil end end
            local bits, flags = word(raw,wf.milestones,4), word(raw,wf.visit_flags,2)
            local result, saved = raw[wf.final_result+1], raw[wf.save_status+1]
            if bits > 31 or flags > 3 or result > 3 or (bits & (t.seen or 0)) ~= (t.seen or 0) then return nil end
            if (bits & 2) ~= 0 and (bits & 1) == 0 then return nil end
            if (bits & 4) ~= 0 and (bits & 2) == 0 then return nil end
            if (bits & 8) ~= 0 and (bits & 4) == 0 then return nil end
            if ((bits & 16) == 0) ~= (result == 0) then return nil end
            if (bits & 1) ~= 0 and (flags ~= 3 or (saved ~= 1 and saved ~= 255)) then return nil end
            for i=0,4 do
                if (bits & (1 << i)) ~= 0 then
                    local expected = i == 0 and t.prepare_seq or (i == 4 and final_seq or t.scene_seq)
                    if expected == nil or word(raw,wf.milestone_seq+2*i,2) ~= expected then return nil end
                end
            end
            local received_pid, received_otid = word(raw,wf.received_pid,4), word(raw,wf.received_otid,4)
            local mb = self:mailbox()
            if not mb or mb.session_epoch ~= t.epoch or not integer(mb.producer_phase,cc.SLINK_PHASE_UNCERTAIN) then return nil end
            if (result == 1 or result == 2) and mb.producer_phase ~= cc.SLINK_PHASE_DONE then return nil end
            if result == 3 and mb.producer_phase ~= cc.SLINK_PHASE_UNCERTAIN then return nil end
            if (bits & 8) ~= 0 and (saved ~= 1 or not t.incoming
               or received_pid ~= t.incoming.pid or received_otid ~= t.incoming.otid) then return nil end
            -- Port of abi.h slink_trade_success_is_durable, including expected
            -- received identity. Milestone frames are diagnostic, never clocks.
            if result == 1 and (bits ~= cc.SLINK_SUCCESS_MILESTONES or flags ~= 3 or saved ~= cc.SLINK_SAVE_OK
               or not t.incoming or received_pid ~= t.incoming.pid or received_otid ~= t.incoming.otid) then return nil end
            if result == 2 and (bits & 14) ~= 0 then return nil end
            return {bits=bits, flags=flags, result=result, saved=saved}
        end)
        if ok and w then t.seen = w.bits; return w end
        return nil
    end
    function self:trade_visit()
        local t = trade_context
        if not t or not t.prepared or t.scene_posted or t.withdraw_posted or t.reconciled
           or not self:trade_capable() then return nil, "durable_trade_unavailable" end
        local mb, w = self:mailbox(), trade_witness(t,t.prepare_seq)
        if not mb or mb.producer_phase ~= cc.SLINK_PHASE_READY or not w or w.bits ~= 1
           or w.flags ~= 3 or w.saved ~= 1 or w.result ~= 0 then return nil, "trade visit not ready" end
        return {id=t.visit, old_key=t.old_key, accepted=true, pre_saved=true, apply_open=true}
    end
    function self:trade_reconciled(token)
        local t = trade_context
        if t and t.token == token and t.terminal and (t.terminal == 1 or t.terminal == 2) then
            t.reconciled = true
            return true
        end
        return false
    end
    function self:prepare_trade(cmd, done, valid)
        if not self:trade_authorized(cmd.token, cmd.old_key) or not outgoing(cmd.old_key,cmd.slot)
           or trade_serial == 0xFFFFFFFF then
            if done then done("durable_trade_unavailable") end
            return nil, "durable_trade_unavailable"
        end
        trade_serial = trade_serial + 1
        local t = {token=cmd.token, old_key=cmd.old_key, slot=cmd.slot, epoch=session_epoch,
            pid=tonumber(cmd.old_key:sub(1,8),16), otid=tonumber(cmd.old_key:sub(10,17),16),
            visit=trade_serial, opaque={0x54,0x33,0x46,0x52}}
        -- Stable opaque mapping for the full server token, unique per session
        -- and preparation; no text truncation and no party-derived identity.
        little(t.opaque,4,t.epoch,4); little(t.opaque,8,t.visit,4); little(t.opaque,12,(~t.visit)&0xFFFFFFFF,4)
        trade_context = t
        local deadline = trade_deadline(cmd,p.OP_TRADE_PREPARE)
        local job
        job = {op=p.OP_TRADE_PREPARE, args=trade_args(t), stages={},
            valid=function()
                if trade_context ~= t or not self:trade_capable() or not outgoing(t.old_key,t.slot)
                   then return false, "guard:stale" end
                local mb = self:mailbox()
                if not mb or (mb.producer_phase ~= cc.SLINK_PHASE_IDLE and mb.producer_phase ~= cc.SLINK_PHASE_DONE)
                   then return false, "trade producer owned" end
                return trade_dispatch_guard(t,valid,deadline,"prepare")
            end,
            on_publish=function() t.prepare_seq = job.seq end,
            done=function(why)
                local w = job.posted and trade_witness(t,t.prepare_seq)
                if w and w.result == 2 then t.terminal, t.reconciled = 2, true end
                if not why and w and w.result == 0 and w.bits == 1 and w.saved == 1 then
                    t.prepared = true
                    local visit = self:trade_visit()
                    if visit then if done then done(nil,visit) end; return end
                end
                if job.posted and not t.reconciled then trade_blocked = why or "native prepare lacks ready witness" end
                if not job.posted and trade_context == t then trade_context = nil end
                if done then done(why or "native prepare lacks ready witness") end
            end}
        job.ready = trade_wait_ready(t,job,deadline)
        return enqueue(job)
    end
    function self:withdraw_trade(token)
        local t = trade_context
        if not t or t.token ~= token or t.reconciled then return nil, "no owned trade" end
        if t.scene_posted or trade_blocked then return nil, "withdraw_too_late" end
        if t.withdraw_job then return t.withdraw_job end
        local job
        job = {op=p.OP_TRADE_WITHDRAW, args=trade_args(t), stages={},
            valid=function()
                local mb = self:mailbox()
                return trade_context == t and self:trade_capable() and not t.scene_posted
                    and mb and mb.producer_phase == cc.SLINK_PHASE_READY, "withdraw_too_late"
            end,
            on_publish=function() t.withdraw_posted = true end,
            done=function(why)
                local w = job.posted and trade_witness(t,job.seq)
                if w and w.result == 2 then t.terminal, t.reconciled = 2, true
                elseif job.posted then trade_blocked = why or "native withdrawal lacks unchanged witness" end
                if why and not t.reconciled then log("[SLink-gen3] trade withdrawal unproved: " .. why) end
            end}
        t.withdraw_job = enqueue(job)
        return t.withdraw_job
    end
    local function trade_transfer(step, cmd, done, valid, progress)
        local t = trade_context
        local deadline = trade_deadline(cmd,step == "scene" and p.OP_TRADE_SCENE or nil)
        if not self:trade_capable() or not t or not self:trade_visit() then return nil, "durable_trade_unavailable" end
        if step == "enemy" then
            local rows = cmd.blobs_hex
            local hex = type(rows) == "table" and #rows == 1 and rows[1]
            if type(hex) ~= "string" or #hex ~= 200 or hex:find("[^%x]") then return nil, "invalid blobs" end
            local bytes = {}
            for i=1,200,2 do bytes[#bytes+1] = tonumber(hex:sub(i,i+1),16) end
            local ok, incoming = pcall(reads.decode_party_mon,bytes)
            if not ok or not self:trade_eligible(incoming) then return nil, "ineligible incoming mon" end
            for _, mon in ipairs(reads.read_party()) do
                if reads.key(mon) == reads.key(incoming) then return nil, "duplicate incoming identity" end
            end
            local job = {stages={{p.BLOB_BUF,bytes}}, valid=function()
                if trade_context ~= t or not self:trade_visit() then return false, "guard:stale" end
                return trade_dispatch_guard(t,valid,deadline,"staging")
            end, done=function(why)
                if not why then t.incoming = {bytes=bytes,pid=word(bytes,0,4),otid=word(bytes,4,4)} end
                if done then done(why) end
            end}
            job.ready = trade_wait_ready(t,job,deadline)
            return enqueue(job)
        end
        if step ~= "scene" or not t.incoming or cmd.token ~= t.token or cmd.old_key ~= t.old_key
           or cmd.visit ~= t.visit or cmd.slot ~= t.slot then return nil, "invalid trade binding" end
        local job
        job = {op=p.OP_TRADE_SCENE, args=trade_args(t), stages={{p.BLOB_BUF,clone(t.incoming.bytes)}},
            valid=function()
                if trade_context ~= t or not self:trade_visit() or not outgoing(t.old_key,t.slot)
                   then return false, "guard:stale" end
                return trade_dispatch_guard(t,valid,deadline,"scene")
            end,
            on_publish=function() t.scene_seq, t.scene_posted = job.seq, true end,
            observe=function()
                local w = trade_witness(t,job.seq)
                job.observed = w
                if w and progress then
                    progress({commit_entered=(w.bits & 2) ~= 0, scene_done=(w.bits & 4) ~= 0,
                        save_success=(w.bits & 8) ~= 0,
                        final_result=({[1]="committed",[2]="unchanged",[3]="uncertain"})[w.result],
                        unchanged_proved=w.result == 2})
                end
                return w
            end,
            done=function(why,result,reason)
                if why and not job.posted then
                    log("[SLink-gen3] trade scene refused before publication: " .. tostring(why))
                end
                -- Completion and progress consume the same coherent snapshot;
                -- rereading here could turn a torn progress read into an ACK
                -- success without delivering its save milestone to trade.lua.
                local w = job.posted and job.observed
                if job.posted and (not w or w.result == 0 or w.result == 3) then
                    why = why or "native trade terminal witness missing"
                    trade_blocked = why .. (reason and (": " .. reason) or "")
                elseif w then t.terminal = w.result end
                if w and w.result == 2 then why = "native refused" end
                if done then done(why,result,reason) end
            end}
        job.ready = trade_wait_ready(t,job,deadline)
        return enqueue(job)
    end
    local function service_trade_binding()
        if not trade_wanted_epoch or session_epoch == trade_wanted_epoch or trade_binding_job
           or poisoned or trade_blocked or trade_context or not trade_supported() then return end
        trade_binding_job = self:set_session_epoch(trade_wanted_epoch)
    end
    local function call_witness()
        local wf = call_witness_fields
        local base = p.BASE + cc.SLINK_CALL_WITNESS_OFFSET
        local ok, witness = pcall(function()
            local before = io.read_u16(base + wf.revision)
            local raw = io.read_bytes(base, 32)
            local after = io.read_u16(base + wf.revision)
            if before ~= after or before % 2 ~= 0 or word(raw, wf.revision, 2) ~= before then return nil end
            if before == 0 then
                if raw[wf.phase+1] == cc.SLINK_CALL_EMPTY then
                    return {revision=0, phase=cc.SLINK_CALL_EMPTY}
                end
                return nil
            end
            return {revision=before, epoch=word(raw,wf.session_epoch,4), seq=word(raw,wf.seq,2),
                phase=raw[wf.phase+1], event=raw[wf.event+1], reason=word(raw,wf.reason,2),
                delivered_frame=word(raw,wf.delivered_frame,4)}
        end)
        return ok and witness or nil
    end
    local function call_slot_free(w)
        return w and (w.phase == cc.SLINK_CALL_EMPTY or w.phase == cc.SLINK_CALL_REFUSED
                      or w.phase == cc.SLINK_CALL_COMPLETE)
    end
    local function call_args(id)
        local args = {}
        for i=1,32 do args[i] = 0 end
        args[1] = id
        return args
    end
    function self:request_match_call(name, data)
        local id = CALL_IDS[name]
        if not id or not self:match_call_capable() or (id == CALL_IDS.first_link and call_first_posted) then return false end
        local row = call_record(id, data) -- own a snapshot; newest equal priority wins
        if call_active and not call_active.job.posted and not call_active.job.publish_attempted then
            if id <= call_active.id then
                call_active.id = id
                call_active.job.args = call_args(id)
                call_active.job.stages[1][2] = row
            end
        elseif not call_queued or id <= call_queued.id then
            call_queued = {id=id, row=row}
        end
        return true
    end
    local function service_calls()
        if not call_supported() or poisoned then call_queued, call_active = nil, nil; return end
        if call_wanted_epoch and session_epoch ~= call_wanted_epoch then
            if call_binding_failed then return end
            if call_binding_attempts >= CALL_BIND_ATTEMPTS then
                if call_handshake_job then self:cancel(call_handshake_job); call_handshake_job = nil end
                call_binding_failed = true
                log("[SLink-gen3] match call handshake exhausted; explicit rebind required")
                return
            end
            call_binding_attempts = call_binding_attempts + 1
            -- Epoch is only a host field write, not native acceptance. Never
            -- displace an old UI's owned record/witness while attempting it.
            if not call_slot_free(call_witness()) then return end
            if not call_handshake_job then call_handshake_job = self:set_session_epoch(call_wanted_epoch) end
            return
        end
        call_handshake_job, call_binding_attempts = nil, 0
        if not self:match_call_capable() then call_queued, call_active = nil, nil; return end
        local now = io.framecount()
        if call_active then
            local active = call_active
            if active.epoch ~= session_epoch then call_queued, call_active = nil, nil; return end
            if not active.acked then return end
            local w = call_witness()
            if not w or w.revision == 0 or w.revision == active.before_revision or w.epoch ~= active.epoch
               or w.seq ~= active.job.seq or w.event ~= active.id then return end
            if w.phase == cc.SLINK_CALL_DELIVERED or w.phase == cc.SLINK_CALL_COMPLETE then
                if not active.delivered then
                    active.delivered, call_delivered_at = true, now -- emulator frames, as Gen 2
                    log("[SLink-gen3] match call " .. active.id .. " delivered")
                end
                if w.phase == cc.SLINK_CALL_COMPLETE then call_active = nil end
            elseif w.phase == cc.SLINK_CALL_REFUSED then
                log("[SLink-gen3] match call refused: " .. tostring(w.reason))
                call_active = nil
            end
            return
        end
        if not call_queued or not self:idle() or #queue ~= 0 then return end
        if call_delivered_at and now >= call_delivered_at
           and now - call_delivered_at < cc.SLINK_CALL_COOLDOWN_FRAMES then return end
        if not call_slot_free(call_witness()) then return end
        local next_call = call_queued
        local active = {id=next_call.id, epoch=session_epoch}
        call_active, call_queued = active, nil
        active.job = enqueue({op=p.OP_MATCH_CALL, args=call_args(active.id),
            stages={{p.BASE + cc.SLINK_TEXT_OFFSET, next_call.row}},
            ready=function()
                return call_active ~= active or call_slot_free(call_witness())
            end,
            valid=function()
                if call_active ~= active or not self:match_call_capable() or active.epoch ~= session_epoch then
                    return false, "guard:stale"
                end
                local w = call_witness()
                if not call_slot_free(w) then return false, "call UI still owned", true end
                active.before_revision = w.revision
                return true
            end,
            on_publish=function()
                if active.id == CALL_IDS.first_link then call_first_posted = true end
            end,
            done=function(why)
                if call_active ~= active then return end
                if why then call_active = nil else active.acked = true end
            end})
        if not active.job and call_active == active then call_active = nil end
    end

    function self:play_sound(id)
        if not sounds_enabled then return true end
        if not integer(id, 65535) then return nil, "invalid sound" end
        return enqueue({op=assert(p.OP_PLAY_SE), args={id & 255, id >> 8}})
    end
    function self:show_menu(cmd)
        local token = cmd.token
        return enqueue({op=assert(p.OP_SHOW_MENU), args={},
            stages={{p.TEXT_BUF, encode(cmd.text, 256)}},
            done=function(why, result)
                send("menu_result", {token=token, choice=not why and result or 0})
            end})
    end
    function self:show_choices(cmd)
        local options, bytes = cmd.options or {}, {}
        if #options < 1 or #options > 8 then
            send("menu_result", {token=cmd.token, choice=127}); return nil, "invalid choices"
        end
        bytes[1] = #options
        for _, option in ipairs(options) do
            for _, b in ipairs(encode(option, 113)) do bytes[#bytes + 1] = b end
        end
        if #bytes > 112 then
            send("menu_result", {token=cmd.token, choice=127}); return nil, "choices too long"
        end
        local stages = {{p.MENU_BUF, bytes}}
        local with_text = cmd.text and cmd.text ~= "" and 1 or 0
        if with_text == 1 then stages[#stages + 1] = {p.TEXT_BUF, encode(cmd.text, 256)} end
        local token = cmd.token
        return enqueue({op=assert(p.OP_SHOW_CHOICES), args={with_text}, stages=stages,
            done=function(why, result)
                send("menu_result", {token=token, choice=not why and result or 127})
            end})
    end
    function self:choose_mon(cmd)
        local token = cmd.token
        return enqueue({op=assert(p.OP_CHOOSE_PARTY_MON), args={}, done=function(why, result)
            send("mon_chosen", {token=token, slot=not why and result or 7})
        end})
    end
    -- Transport primitives for the later trade FSM. These do not expose
    -- apply_trade to client.lua, choose a target, or implement fallback policy.
    -- The FSM owns identity/preflight and reads back the party after completion.
    -- transfer("enemy", {blobs_hex={...}}, done): OP_SET_ENEMY_PARTY + BLOB_BUF.
    -- transfer("party", {slot, blob_hex, bump?}, done): OP_SET_PARTY_MON + BLOB_BUF.
    -- transfer("scene", {slot}, done): OP_TRADE_SCENE, no staging.
    -- valid (optional): a dispatch-time guard, called by service() immediately before this job is
    -- dispatched (same frame-end callback, CPU stopped); false, why drops the job with done(why).
    -- The trade FSM uses it to re-locate the offered mon at the moment a slot op actually posts.
    function self:transfer(step, cmd, done, valid, progress)
        if fr_v2 then return trade_transfer(step,cmd,done,valid,progress) end
        -- Direct scene probes exercise the legacy transport without claiming
        -- durable completion. The FSM's milestone path still requires capability.
        if step == "scene" and progress ~= nil and not self:trade_capable() then
            if done then done("durable_trade_unavailable") end
            return nil, "durable_trade_unavailable"
        end
        local op, args, stages = nil, {}, {}
        if step == "scene" or step == "party" then
            if not integer(cmd.slot, 5) then return nil, "invalid party slot" end
            args = {cmd.slot}
        end
        if step == "scene" then op = assert(p.OP_TRADE_SCENE)
        elseif step == "party" or step == "enemy" then
            local rows = step == "party" and {cmd.blob_hex} or cmd.blobs_hex
            if type(rows) ~= "table" or #rows < 1 or #rows > 6 then
                return nil, "invalid blobs"
            end
            local bytes = {}
            local mon_size = reads.PARTY_MON_SIZE
            if not integer(mon_size, 600) or mon_size == 0 then return nil, "invalid mon size" end
            for _, hex in ipairs(rows) do
                if type(hex) ~= "string" or #hex ~= 2 * mon_size or hex:find("[^%x]") then
                    return nil, "invalid blobs"
                end
                for i = 1, 2 * mon_size, 2 do bytes[#bytes + 1] = tonumber(hex:sub(i, i + 1), 16) end
            end
            stages = {{p.BLOB_BUF, bytes}}
            if step == "party" then
                op, args = assert(p.OP_SET_PARTY_MON), {cmd.slot, cmd.bump and 1 or 0}
            else op, args = assert(p.OP_SET_ENEMY_PARTY), {#rows} end
        else return nil, "unsupported transfer step" end
        return enqueue({op=op, args=args, stages=stages, done=done, valid=valid, progress=progress})
    end
    -- G5-RR-RIVAL: the patch's rival-swap window W1 (docs/gen3/research/rival_swap_refresh_window.md
    -- §5), mirrored from patch/src/handlers.c's OP_RIVAL_SWAP check (OMP F4): gBattleCommunication[0]
    -- < 15, gBattleMainFunc == BeginBattleIntroDummy and not a LINK battle here; the trainer id in
    -- the caller's guard (gTrainerBattleOpponent_A == the epoch's). The fifth clause, callback2 ==
    -- CB2_HandleStartBattle (RV_CB2_START_BATTLE), has no pack word, so it stays the patch's alone
    -- (a refusal there is REASON_WINDOW_CLOSED, never a write). A post read open at this frame end
    -- is consumed by the next frame's slink_hook, before the callbacks run (§5.2).
    local rr = profile.titles and profile.titles[deps.title or "radical_red"]
    function self:rival_window_open()
        local ram, rom, der = rr and rr.ram, rr and rr.rom, rr and rr.derived
        if not (ram and rom and der and ram.BATTLE_MAIN_FUNC_ADDR and ram.BATTLE_COMM_ADDR
                and ram.BATTLE_TYPE_ADDR and der.BATTLE_TYPE_LINK_MASK
                and rom.BEGIN_BATTLE_INTRO_DUMMY_ADDR) then
            return false
        end
        return io.read_u8(ram.BATTLE_COMM_ADDR) < 15
               and io.read_u32(ram.BATTLE_MAIN_FUNC_ADDR) == rom.BEGIN_BATTLE_INTRO_DUMMY_ADDR
               and (io.read_u32(ram.BATTLE_TYPE_ADDR) & der.BATTLE_TYPE_LINK_MASK) == 0
    end
    -- guard(epoch): the caller's live epoch check, run at dispatch. hold(epoch) (optional): true
    -- while a PRE-announced swap should wait in the queue for the window -- the job is STAGED, not
    -- posted, and other jobs pass it; once the window opens (or the hold ends) it dispatches.
    function self:replace_rival_team(cmd, guard, hold)
        local trainer = cmd.trainer_id or 0
        local function reply(why, species, reason)
            local fields = {trainer_id=trainer, species_ids=array(species or {}), error=why}
            if reason then fields.reason = reason end
            send("rival_team_replaced", fields)
        end
        local epoch = {session = cmd.session, battle_id = cmd.battle_id, trainer_id = cmd.trainer_id}
        local function in_battle() return deps.in_battle and deps.in_battle() or false end
        local function holding() return hold ~= nil and hold(epoch) == true end
        if not in_battle() and not holding() and not self:rival_window_open() then
            reply("not_in_battle"); return true
        end
        local rows, bytes = cmd.blobs_hex or {}, {}
        if #rows < 1 or #rows > 6 then reply("invalid_blobs"); return true end
        for _, hex in ipairs(rows) do
            if type(hex) ~= "string" or #hex ~= 200 or hex:find("[^%x]") then
                reply("invalid_blobs"); return true
            end
            for i = 1, 200, 2 do bytes[#bytes + 1] = tonumber(hex:sub(i, i + 1), 16) end
        end
        local count = #rows
        return enqueue({op=assert(p.OP_RIVAL_SWAP), args={count, trainer & 255, trainer >> 8},
            ready=function() return self:rival_window_open() or not holding() end,
            valid=function()
                -- C5-11a: the guard runs at DISPATCH, immediately before posting, and every part
                -- is re-evaluated there -- never at enqueue. `guard` is the caller's live epoch
                -- check (client.lua), which owns the identity; this file adds its own in-battle
                -- and interval checks so a job can never post outside them. OMP F3: while the
                -- caller's hold still lasts, a failed check is a read that is NOT READY yet (the
                -- third return keeps the job queued); only an ended hold makes it a refusal.
                local ok, why = true, nil
                if not in_battle() and not self:rival_window_open() then ok, why = false, "not_in_battle"
                elseif guard then ok, why = guard(epoch) end
                if not ok then return false, why, holding() end
                return true
            end,
            stages={{p.BLOB_BUF, bytes}}, done=function(why, _result, reason)
                -- the patch's own ST_FAIL reason is the error (review F3): window_closed,
                -- bad_args, ...; an unnamed word stays "native_refused", never a guess
                if why then
                    reply(why == "native refused" and (reason or "native_refused") or why, nil, reason)
                    return
                end
                local ram = assert(rr.ram)
                local actual = io.read_bytes(ram.ENEMY_BASE, #bytes)
                for i, b in ipairs(bytes) do
                    if actual[i] ~= b then reply("enemy_readback_failed"); return end
                end
                if io.read_u8(ram.ENEMY_COUNT_ADDR) ~= count then
                    reply("enemy_readback_failed"); return
                end
                -- No gBattleMons refresh (G5-RR-RIVAL): OP_RIVAL_SWAP is consumed only inside W1,
                -- BEFORE the engine selects and snapshots the enemy lead, so the engine's own
                -- converter builds gBattleMons from the swapped party (window doc §5 "What ends up
                -- correct"). The readback above is the whole success condition.
                local species = {}
                for i = 0, count - 1 do
                    local raw = {}
                    for j = 1, 100 do raw[j] = actual[i * 100 + j] end
                    local mon = reads.decode_party_mon(raw)
                    if not mon then reply("enemy_readback_failed"); return end
                    species[#species + 1] = mon.species
                end
                -- OMP F2 post-condition: the engine builds gBattleMons[1] from the swapped party
                -- (BattleIntroDrawTrainersOrMonsSprites); success waits, bounded, for its
                -- personality to be one of the staged mons', else enemy_snapshot_stale.
                -- ponytail: a value that is ALREADY a staged PID at the ack (a rematch against the
                -- same team) cannot be told from the new write, so it is accepted at once.
                local pids = {}
                for i = 0, count - 1 do
                    local o = i * 100
                    pids[actual[o + 1] | actual[o + 2] << 8 | actual[o + 3] << 16 | actual[o + 4] << 24] = true
                end
                local lead_pid = ram.BATTLE_MONS_ADDR + 0x58 + 0x48   -- gBattleMons[1].personality
                watches[#watches + 1] = {deadline = io.framecount() + SNAPSHOT_FRAMES,
                    check = function() return pids[io.read_u32(lead_pid)] == true end,
                    ok = function() reply(nil, species) end,
                    fail = function() reply("enemy_snapshot_stale") end}
            end})
    end

    local function panel_job(open)
        local function ready()
            if not deps.panel_stage_safe then return true end
            local ok, safe = pcall(deps.panel_stage_safe)
            return ok and safe == true
        end
        if v2 then
            local c=v2.constants
            local pages=math.min(255,math.max(1,math.ceil(#panel_rows/c.SLINK_INFO_MAX_LINES)))
            panel_page=panel_page%pages
            local text,lines={},0
            for i=1,256 do text[i]=255 end
            local function row(slot,value)
                for i,b in ipairs(encode(value,c.SLINK_INFO_LINE_WIDTH)) do text[slot*32+i]=b end
            end
            for i=panel_page*6+1,math.min(#panel_rows,(panel_page+1)*6) do
                row(lines,tostring(panel_rows[i]):gsub("|","\n"));lines=lines+1
            end
            if lines==0 then row(0,"No run data yet");lines=1 end
            row(7,string.format("PAGE %d/%d",panel_page+1,pages))
            local epoch,bytes=session_epoch,{}
            for i=0,3 do bytes[i+1]=(epoch>>(8*i))&255 end
            local request=(io.read_u16(p.INFO+c.SLINK_INFO_REQUEST_FIELD)%65535)+1
            return {op=open and p.OP_SHOW_INFO or nil,args={},ready=ready,stages={
                {p.INFO+c.SLINK_INFO_TEXT_FIELD,text},
                {p.INFO+c.SLINK_INFO_LINES_FIELD,{lines,panel_page,pages,1}},
                {p.INFO+c.SLINK_INFO_EPOCH_FIELD,bytes},
                {p.INFO+c.SLINK_INFO_REQUEST_FIELD,{request&255,request>>8}}},
                valid=function() return session_epoch==epoch and epoch~=0 and io.read_u8(p.INFO+c.SLINK_INFO_STATE_FIELD)==0,"panel identity/ownership changed" end}
        end
        local pages = math.max(1, math.ceil(#panel_rows / p.INFO_MAXLINES))
        panel_page = panel_page % pages
        local rows = {}
        for i = panel_page * p.INFO_MAXLINES + 1,
                math.min(#panel_rows, (panel_page + 1) * p.INFO_MAXLINES) do
            rows[#rows + 1] = panel_rows[i]:gsub("|", "\n")
        end
        if #rows == 0 then rows[1] = "No run data yet" end
        local stages = {{p.INFO, {1}}}
        for i, row in ipairs(rows) do
            stages[#stages + 1] = {p.INFO + 8 + (i - 1) * p.INFO_LINEW, encode(row, p.INFO_LINEW)}
        end
        stages[#stages + 1] = {p.INFO + 8 + p.INFO_PAGESLOT * p.INFO_LINEW,
            encode(string.format("PAGE %d/%d", panel_page + 1, pages), p.INFO_LINEW)}
        stages[#stages + 1] = {p.INFO + 4, {panel_page, pages}}
        stages[#stages + 1] = {p.INFO + 3, {#rows}} -- publish lines after all strings
        stages[#stages + 1] = {p.INFO + 6, {(io.read_u8(p.INFO + 6) + 1) % 256}}
        return {op=open and assert(p.OP_SHOW_INFO) or nil, args={panel_page}, ready=ready,
            stages=stages, panel=true,
            done=function(why, result)
                if open and not why and result == 0 and pages > 1 then
                    panel_page = (panel_page + 1) % pages
                    enqueue(panel_job(true))
                elseif open and not why then
                    panel_page = 0
                    enqueue(panel_job(false))
                end
            end}
    end
    function self:link_panel(cmd)
        if v2 then
            local mb=self:mailbox()
            if not mb or (mb.capabilities&v2.constants.SLINK_CAP_INFO_PANEL)==0 then return nil,"native panel absent" end
            panel_rows,panel_page=clone(cmd.rows or {}),0
            panel_dirty,panel_reopen=true,false
            return true
        end
        panel_rows, panel_page = clone(cmd.rows or {}), 0
        for i, job in ipairs(queue) do
            if job.panel and not job.op then
                queue[i] = panel_job(false)
                return true
            end
        end
        return enqueue(panel_job(false))
    end
    function self:config(cmd)
        if cmd.native_sounds ~= nil then sounds_enabled = cmd.native_sounds == true end
        if control then
            if cmd.overworld_presence ~= nil or cmd.pc_trade_npc ~= nil then
                npc_enabled = cmd.overworld_presence ~= true and cmd.pc_trade_npc ~= false
            end
            -- A config commonly arrives before the automatic epoch binding.
            -- That binding publishes these same CONTROL fields when it is safe.
            if session_epoch == 0 then return true end
            local epoch, bytes = session_epoch, {}
            for i=0,3 do bytes[i+1] = (epoch >> (8*i)) & 255 end
            return enqueue({stages={{control.base+control.epoch,bytes},
                                   {control.base+control.enable,{npc_enabled and 1 or 0}}},
                valid=function() return session_epoch == epoch,"guard:epoch" end})
        end
        if v2 then return nil, "v2 control binding unavailable" end
        local stages = {}
        if cmd.overworld_presence ~= nil or cmd.pc_trade_npc ~= nil then
            npc_enabled = cmd.overworld_presence ~= true and cmd.pc_trade_npc ~= false
            stages[#stages + 1] = {p.TN_ENABLE, {npc_enabled and 1 or 0}}
        end
        if cmd.battle_calc ~= nil then
            stages[#stages + 1] = {p.CALC_OFF, {cmd.battle_calc and 0 or 1}}
        end
        if #stages == 0 then return true end
        return enqueue({stages=stages})
    end

    function self:service()
        local frame, here = io.framecount(), present()
        local owned_panel = pending and pending.panel
        if (last_frame and frame < last_frame) or (was_present and not here) then
            if fr_v2 and trade_context and not trade_context.reconciled then trade_blocked = "native reset during owned trade" end
            abort("native reset")
            poisoned, npc_count, panel_drawn, panel_showing = nil, nil, nil, false
            session_epoch = 0
        end
        last_frame, was_present = frame, here
        if not here then return nil, "native absent" end
        local epoch_ok, epoch_why = check_epoch()
        if not epoch_ok then return nil, epoch_why end
        if fr_v2 and trade_context and not trade_supported() then
            trade_blocked, poisoned = "trade capability lost", "trade capability lost"
            abort(poisoned)
        end
        if pending then
            local job = pending
            local opcode = io.read_u16(p.BASE + O.opcode)
            if io.read_u16(p.BASE + O.seq) ~= job.seq or (opcode ~= 0 and opcode ~= job.op) then
                poisoned = "native sequence overwritten"; abort(poisoned)
            elseif not stage_intact(job) then
                poisoned = "native staging overwritten"; abort(poisoned)
            else
                -- Observe before consuming the ACK: a native frame may publish
                -- all milestones together. ACK/result scratch alone is no proof.
                if job.observe then job.observe() end
                if io.read_u16(p.BASE + O.ack) == job.seq
                and (io.read_u16(p.BASE + O.status) == OK or io.read_u16(p.BASE + O.status) == FAIL) then
                local status, result = io.read_u16(p.BASE + O.status), io.read_u8(p.BASE + O.result)
                local reason = status == FAIL and io.read_u16(p.BASE + O.reason) or nil
                pending = nil -- consume receipt BEFORE any next post can rewrite it
                finish(job, status == FAIL and "native refused" or nil, result,
                       reason and fail_reasons[reason] or nil)
            elseif frame - job.started >= timeout_for(job.op) then
                -- Never reuse a timed-out slot: opcode==0 can mean an async handler
                -- still owns it. A reset/absent beacon is the recovery boundary.
                -- Poison blocks further posts. Durable trade treats a possibly committed
                -- operation as uncertain; neither raw replacement nor RAM readback repairs it.
                poisoned = "native timeout"; abort(poisoned)
                end
            end
        end
        if poisoned then return nil, poisoned end
        for i = #watches, 1, -1 do
            local wt = watches[i]
            if wt.check() then table.remove(watches, i); wt.ok()
            elseif io.framecount() >= wt.deadline then table.remove(watches, i); wt.fail() end
        end
        if control then
            local counter = io.read_u32(control.base+control.counter)
            if npc_count ~= nil and ((counter-npc_count)&0xFFFFFFFF) == 1
               and npc_enabled and io.read_u8(control.base+control.enable) == 1
               and io.read_u32(control.base+control.epoch) == session_epoch
               and self:trade_capable() and not self:trade_active() and not pending then
                send("trade_request", {})
            end
            npc_count = counter -- resets/discontinuities re-latch without inventing a talk
        end
        if not v2 then -- v1 NPC/panel offsets are never inherited by a v2 mailbox
            local counter = io.read_u8(p.PI_COUNT)
            if npc_count ~= nil and counter > npc_count and npc_enabled and not pending then
                send("trade_request", {})
            end
            npc_count = counter -- backwards counters re-latch; never synthesize an interaction
            local drawn = io.read_u8(p.INFO + 2)
            if panel_drawn ~= nil and drawn ~= panel_drawn and not owned_panel then
                panel_showing = true
            end
            panel_drawn = drawn
            if panel_showing and deps.panel_closed then
                local closed, result = deps.panel_closed()
                if closed then
                    panel_showing = false
                    if not pending then
                        panel_page = result == 0 and panel_page + 1 or 0
                        enqueue(panel_job(result == 0 and #panel_rows > p.INFO_MAXLINES))
                    end
                end
            end
        end
        if v2 then
            local state=io.read_u8(p.INFO+v2.constants.SLINK_INFO_STATE_FIELD)
            if panel_showing and state==0 then
                panel_showing=false
                if not panel_dirty then
                    local next_page=io.read_u8(p.INFO+v2.constants.SLINK_INFO_RESULT_FIELD)==0 and #panel_rows>6
                    panel_page=next_page and panel_page+1 or 0
                    panel_dirty,panel_reopen=true,next_page
                end
            else panel_showing=state~=0 end
            if panel_dirty and not panel_showing and session_epoch~=0 and not pending and #queue==0 then
                enqueue(panel_job(panel_reopen));panel_dirty,panel_reopen=false,false
            end
        end
        service_calls()
        service_trade_binding()
        if not self:idle() or #queue == 0 then return true end
        -- a job whose `ready` says "not yet" (a staged rival swap waiting for its window) stays
        -- queued and does not block the jobs behind it
        local at = 1
        while queue[at] and queue[at].ready and not queue[at].ready() do at = at + 1 end
        local job = queue[at]
        if not job then return true end
        if job.valid then
            local valid, why, retry = job.valid()
            if not valid and retry then return true end         -- not ready yet: stays queued
            if not valid then table.remove(queue, at); finish(job, why); return nil, why end
        end
        local ok, why = dispatch(job)
        if ok ~= nil then table.remove(queue, at) end
        if ok == false then abort(poisoned) end
        return ok, why
    end
    local service, advertised_trade = self.service, false
    function self:service()
        local result = table.pack(service(self))
        if fr_v2 and not trade_dead_notice and (trade_blocked or poisoned) then
            trade_dead_notice = true
            log("[SLink-gen3] durable trade unavailable for this session: " .. tostring(trade_blocked or poisoned))
        end
        local capable = self:trade_capable()
        if capable ~= advertised_trade then
            advertised_trade = capable
            if deps.trade_capability_changed then deps.trade_capability_changed(capable) end
        end
        return table.unpack(result,1,result.n)
    end
    return self
end
return N
