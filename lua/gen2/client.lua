-- lua/gen2/client.lua — the Gen 2 Soul Link client, as a state machine over injected parts.
--
-- P3b rules qualification only. lua/gen1/client.lua is the standard: this file mirrors its
-- structure (outbound, wire shapes, writes gate, inbound commands, signals -> events,
-- hello/tick, per-frame driver) and every place it differs carries a `Gen 2:` reason.
-- Contract: docs/protocol.md. Evidence comes from the parts: the candidate graph is MODEL; the
-- production graph (Entry.build, admitted Crystal) binds the U1/U2 PHYSICAL receipts.
--
-- Parts (all injected; lua/gen2/entry.lua build / build_candidate compose them):
--   reads    lua/gen2/reads.lua     WRAM/SRAM decoders
--   wire     lua/gen2/wire.lua      decoded record -> docs/protocol.md §4 shapes (refuses eggs)
--   signals  factory(authority) -> the lua/gen2/signals.lua binder (typed events + latches)
--   writes   lua/gen2/writes.lua    bench faint behind the shared write permit
--   boxes    lua/gen2/boxes.lua     B.executor: deposit/withdraw/memorialize (nil: box commands NACK)
--   safety   checkpoint, :check(kind) -> ok, why (lua/gen2_write_safety.lua: only a U2-receipted
--            write kind in the held checkpoint frame)
--   checkpoint_pc  production only: the checkpoint PC hooked for writes + hello readiness
--   battle_hold    O-30: write_checkpoint.json battle_hold facts, the in-battle death site (before
--                  `call DetermineMoveOrder`); production composes it only behind a battle_faint receipt
--   battle_bench   O-32: true lands a BENCH death on receipt, at the battle frame end (Gen 1 battle_bench);
--                  production composes it only behind a receipt covering battle_bench (and battle_faint)
--   contest_mask   write_checkpoint.json contest_mask: the Bug-Catching Contest's party mask (ruling a)
--   net      lua/connector.lua      newline-JSON TCP (send/receive/pump/connected)
--   json/hud/io                     as Gen 1
--
-- Events come from the binder's latches only (capture, whiteout, PC ops, NPC trade,
-- evolution) or from a binder observation settled against one read (faint, battle start/end,
-- save, reset); nothing is inferred by polling. A binder refusal is logged once per reason. Writes happen only inside the armed permit at the
-- checkpoint. P4 native sound and SLINK trade are not here: those commands get the protocol's
-- "nothing happened" replies (handle_command) and request_sfx_local is the sound seam. The
-- native panel (P4.1f) is the optional p.panel (lua/gen2/panel.lua); nil means no panel. The
-- native SLINK TRADE (P4.3b) is the optional p.trade (lua/gen2/trade_overlay.lua) with its own
-- trade permit; without it (or off an advertising overlay) trade commands get the same replies.
local Client = { TICK_INTERVAL = 30, VALIDATE_EVERY = 60, MAX_INVALID = 5, MAX_PENDING_FRAMES = 600,
                 MAX_HELD = 64,
                 -- P4.3b: an armed PROMPT the player never picks up is withdrawn after this many frames
                 -- (fits inside the proposer's SLINK_TRADE_APPLY_FRAMES = 3600 hold with ~29 s left for the
                 -- YES/NO, patch/gen2/src/trade_service.asm; a waiting cartridge picks up an armed APPLY
                 -- within a frame, so this never gives up before the cartridge does)
                 TRADE_PICKUP_FRAMES = 1800,
                 -- W-4: a parked replace_rival_team gives up this many frames after trainer_battle_start
                 -- (Gen 1 RIVAL_INIT_FRAMES; rival_tick logs the measured RIVAL_WINDOW)
                 RIVAL_FRAMES = 240 }

-- Gen 2: the wild battle types whose failure dead-zones the map are exactly the ones the
-- binder links to the map's area (signals.lua final_event: NORMAL 0, FISH 4, TREE 8);
-- roamer/contest/scripted battles resolve their own namespace or nothing (O-17, O-18).
-- A profile with derived.area_battle_types (Polished: NORMAL 0, FISH 3, TREE 4) replaces this vanilla set.
local AREA_BATTLE_TYPES = { [0] = true, [4] = true, [8] = true }
-- The protocol NACK of each box command (docs/protocol.md §5). The executor (p.boxes, lua/gen2/boxes.lua
-- B.executor) runs them at the checkpoint; a kind the U2 receipt never proved is refused there, with its name.
local BOX_NACK = { box_mon = "box_mon_failed", party_mon = "sync_retrieve_failed",
                   memorialize = "memorialize_failed" }
local BOX_OPEN = "Gen 2 box executor not composed"
-- The U2 write kind whose held checkpoint the hello snapshot and the bench faint wait for.
local PARTY_HP = "party_hp"
-- O-30: the write kind held at the battle hold (gen2_write_safety BATTLE_KINDS).
local BATTLE_FAINT = "battle_faint"
-- O-32: the receipt-time bench write kind (gen2_write_safety BENCH_KINDS), checked at a battle frame end.
local BATTLE_BENCH = "battle_bench"

-- BURIAL-VISIBLE: a boxed burial waiting on an in-game SAVE (BOX-MEMORIAL-2) is shown, every this many
-- frames while it waits (hud.show sanitizes; the tick's awaiting_save puts it on the pair board)
Client.BURIAL_NAG_FRAMES = 1200
local BURIAL_TEXT = "SAVE TO FINISH BURIAL"

-- A mon key's evolution-stable identity: (DV:OT, species). Vanilla DDDD:OOOO:SS -> ("DDDD:OOOO", SS);
-- Polished DDDDDD:OOOO:SSS:TT -> ("DDDDDD:OOOO", SSS). Evolution changes only the species field; the
-- Polished traits byte (shiny/female/form) is left out of the stable part, so a form or trait change on
-- evolution cannot make the evolved mon look like a new one. nil for a non-key.
function Client.key_identity(key)
    if type(key) ~= "string" then return nil end
    local stable, species = key:match("^(%x+:%x+):(%x+)")
    if not stable then return nil end
    return stable, tonumber(species, 16)
end

local function nick_label(key, nickname)
    if nickname and nickname ~= "" then return nickname end
    return key and key:sub(1, 8) or "?"
end

-- KEY-SCOPE-5: key_change refusals that retire nothing. The alias stays, and the change goes out
-- again after the next complete box census (send_tick). Every other reason is terminal (U5).
Client.RETRYABLE_REJECTIONS = { ["box census unavailable"] = true, ["ambiguous key (trade clash)"] = true }

function Client.new(p)
    local HelloSession = assert(p.hello_session, "shared hello_session factory required")
    local ReplyDispatch = assert(p.reply_dispatch, "shared reply_dispatch factory required")
    local OwedReports = assert(p.owed_reports, "shared owed_reports factory required")
    local reads, wire, writes, safety, boxes = p.reads, p.wire, p.writes, p.safety, p.boxes
    local signals_factory = assert(p.signals, "Gen 2 signals factory required")
    local net, json, hud, io = p.net, p.json, p.hud, p.io
    local profile, sites, area_map = p.profile, p.sites, p.area_map
    local log = p.log or function(...) end
    local panel = p.panel -- P4.1f panel: lua/gen2/panel.lua, or nil (no panel)
    local phone = p.phone -- P4.5c phone calls: lua/gen2/phone.lua, or nil (no panel)
    local trade = p.trade -- P4.3b native SLINK TRADE: lua/gen2/trade_overlay.lua, or nil (no trade build)
    local dev_trade -- C4b: Polished pump (historical name); never aliases the vanilla p.trade.
    -- F2 (docs/polished/PLAIN_FAINT_F2.md): the optional Polished-only active-faint settlement interface, supplied ONLY
    -- by compose_polished (lua/gen2/entry.lua) from lua/gen2/polished_explode.lua. nil (every vanilla composition):
    -- every `settle` branch below is dead and the legacy death paths run unchanged.
    local settle = p.active_faint_settlement
    assert(settle == nil or (type(settle) == "table" and type(settle.attempt_mark) == "function"
           and type(settle.attempted_since) == "function"), "active_faint_settlement needs attempt_mark/attempted_since")
    local c = profile.constants
    local arr = json.array -- tag lists so an empty one encodes as [] not {}
    local area_battle_types = AREA_BATTLE_TYPES
    if profile.derived and profile.derived.area_battle_types then
        area_battle_types = {}
        for _, t in ipairs(profile.derived.area_battle_types) do area_battle_types[t] = true end
    end

    -- D5: the kind is the admission decision's (lua/gen2/entry.lua); anything else would run a trade or a hello as a
    -- kind the server never admitted.
    -- R3: rand_overlay is the randomized companion overlay (entry.lua anchors admission); it carries the overlay.
    assert(p.artifact_kind == nil or p.artifact_kind == "clean" or p.artifact_kind == "overlay"
           or p.artifact_kind == "rand_overlay", "artifact_kind must be clean, overlay or rand_overlay")
    assert(p.rescan_every == nil or (type(p.rescan_every) == "number" and p.rescan_every % 1 == 0
           and p.rescan_every >= Client.TICK_INTERVAL), "rescan_every must be nil or whole frames >= one tick")
    local self = {
        player = p.player, rom_type = p.rom_type, rom_sha1 = p.rom_sha1,
        -- Gen 2: one pairing foundation for all three packs (O-16; gen2_gsc.py game_id); Polished injects its own
        foundation = p.foundation or "gen2_gsc", artifact_kind = p.artifact_kind or "clean",
        -- P4.3b: the lease the cartridge is waiting on ({kind, gen, token, frame, ...}) and this
        -- visit's role/token, kept from the first command (trade_mask/show_menu) through APPLY
        trade = trade, trade_state = nil, trade_visit = nil,
        -- post-DONE trade reports, trade_uncertain, withdraw_offer: sent in order once the hello is ready and
        -- kept until the server answers them (lua/owed_reports.lua; review 2026-09-24 MAJOR-1)
        owed = OwedReports.new(),
        seq = 0, frame = 0, hello_sent = false,
        writes_enabled = false, invalid_streak = 0, gate_revoked = false,
        box_cache = {}, resolved_areas = {}, config = {}, deferred = {},
        box_generation = 0, box_complete = false, -- KEY-SCOPE-5: bumped per complete rescan_boxes
        battle = nil, has_pokeballs = false, nuzlocke_announced = false, signals = nil,
        -- Gen 2: the binder needs an operation authority (signals.lua header); the client
        -- owns it as a reset epoch, bumped at every reset/reload boundary.
        epoch = 0,
        -- Gen 2: the binder's faint names the record (same-frame key); the latch holds it
        -- until that record reads HP 0 (UpdateFaintedPlayerMon runs before the copy-back)
        faint_latches = {}, refusals_logged = {},
        pending_rescan = false, key_alias = nil, retired_alias = {},
        rescan_every = p.rescan_every, -- Polished: periodic census refresh in frames (see frame_end); nil = never
        -- Gen 2: messages observed before this connection's hello (see send)
        held = {}, held_full = false, last_frame = nil,
        -- Gen 2 (gen2-box-durability): backing withdraws whose box copy waits for a native SAVE
        -- ({key, armed}); a pending SaveBox after an active-box edit (OMP BOX F2, recorded, not blocking)
        settle = {}, box_save_pending = false,
        -- O-30, Gen 1 parity (lua/gen1/client.lua pending_battle_writes / arrivals / defer_held): deaths
        -- owed in battle wait for the next battle hold; `commanded` holds keys whose native faint is the
        -- echo of a death the server commanded (dropped once, never reported as a new faint).
        pending_battle_writes = {}, arrivals = 0, commanded = {},
        -- review 2026-09-24 MINOR-7: every key whose death landed here, until its burial. A heal before the
        -- burial (a Pokemon Center, the Battle Tower's HealParty before battle 1) revives it; the checkpoint
        -- and the battle holds zero it again, quietly. Kept across a reset (the client outlives it).
        dead_keys = {},
        -- INV-CLIENT-2: the server's dead/memorial keys for this player, sent at every accepted hello
        -- (`dead_keys`), REPLACE the set; after a reset/reload/identity change the sweep waits for that sync
        -- (another save with the same OT must never get a stale write)
        dead_synced = true,
    }
    self.trade_owed = self.owed.list
    -- F2 state (nil without the interface): obligations, identity generation, observation/attempt sequence, hold token
    local FS = settle and { owed = {}, next_id = 0, gen = 0, seq = 0, hold_serial = 0, visit = 0, in_battle = false,
                            suspended = false, logged = {}, spent = {} } or nil
    self.faint_settle = FS

    -- ── outbound ─────────────────────────────────────────────────────────────────────
    local function send(event, fields)
        if not net.connected() then
            log("[SLink-gen2] drop " .. event .. ": not connected")
            return false
        end
        -- Gen 2: nothing but the hello reaches the server before a hello is queued for this
        -- connection/identity (Gen 1 gates tick/safe on the same readiness). The candidate
        -- checkpoint refuses in the overworld, so that can be minutes: earlier messages wait
        -- in order and follow the hello (frame_end); a reset/reload boundary, an identity change
        -- and a savestate load drop them (drop_held; the load through abandon_timeline).
        if event ~= "hello" and not (self.hello_session and self.hello_session:status().ready) then
            if #self.held >= Client.MAX_HELD then
                log("[SLink-gen2] drop " .. event .. ": pre-hello queue full")
                -- a lost capture/faint/deposit is a transition the hello snapshot cannot rebuild:
                -- the player sees it once per filled queue, not only the log
                if not self.held_full then
                    self.held_full = true
                    hud.show("SLINK EVENTS LOST - SEE LOG", 255, 64, 64, 600)
                end
            else
                self.held[#self.held + 1] = { event = event, fields = fields }
            end
            return false
        end
        self.seq = self.seq + 1
        local msg = fields or {}
        msg.event, msg.player, msg.seq = event, self.player, self.seq
        if net.send(json.encode(msg)) == false then return false end
        self.owed:line_sent()
        return true
    end
    self.send = send

    -- A report the server must receive (MAJOR-1): never dropped on a down socket, re-sent after a reconnect
    local function owe(event, fields)
        self.trade_owed[#self.trade_owed + 1] = { event = event, fields = fields }
        self.owed:step(net.connected(), self.hello_session:status().ready == true, send)
    end

    -- Gen 2: held messages belong to the save and the timeline that produced them.
    local function drop_held(why)
        if #self.held > 0 then
            log("[SLink-gen2] " .. #self.held .. " pre-hello message(s) dropped: " .. why)
        end
        self.held, self.held_full = {}, false
    end

    -- Gen 1 parity (lua/gen1/client.lua defer_held): a battle-held death handed to the checkpoint keeps
    -- its ARRIVAL position, so it never falls behind a later memorialize for the same key.
    local function defer_held(entry)
        entry.arrival = entry.arrival or self.arrivals
        for i, queued in ipairs(self.deferred) do
            if queued.arrival and queued.arrival > entry.arrival then
                table.insert(self.deferred, i, entry)
                return
            end
        end
        self.deferred[#self.deferred + 1] = entry
    end

    -- Gen 2: a few point scalars (wBattleScriptFlags) exist only as the engine-site pack's
    -- point symbols, not profile.ram; both carry bank + address.
    local points = {}
    for _, site in pairs(sites) do
        for name, pt in pairs(site.point_symbols or {}) do points[name] = pt end
    end
    local function wram_bytes(name, n)
        local pt = points[name] or { addr = profile.ram[name], bank = profile.ram_bank[name] }
        if not pt.addr or not pt.bank then return nil, "no WRAM coordinates for " .. name end
        if io.bank_valid(pt.bank, pt.addr, n) ~= true then return nil, name .. ": WRAM bank unavailable" end
        local bytes = io.read_range(pt.addr, n, "System Bus")
        if type(bytes) ~= "table" or #bytes ~= n then return nil, name .. ": unreadable" end
        return bytes
    end
    local function wram_byte(name)
        local b, why = wram_bytes(name, 1)
        return b and b[1], why
    end

    -- ── reads → wire shapes ──────────────────────────────────────────────────────────
    -- Gen 2: the key is wire.mon_key (DDDD:OOOO:SS over the decoded record), the same
    -- function every snapshot entry uses; test_gen2_client pins it to gen2_codec.key.
    local mon_key = wire.mon_key

    -- The Gen 2 trainer id on the wire: class * 256 + instance (wOtherTrainerClass/wOtherTrainerID,
    -- C ram/wram.asm:2728,2736 / G:2194,2204), the pair ReadTrainerParty indexes by. One int keeps
    -- the shared trainer_battle_start/replace_rival_team contract; gen2_gsc.py decodes it.
    local function trainer_id_of(trainer) return trainer.class_raw * 256 + trainer.id_raw end

    local function current_party() return reads.read_party() end

    -- Gen 2: eggs stay off the wire until they hatch (O-15). Each entry keeps its own
    -- `slot`, so omitting an egg shifts no index. Any other wire refusal fails the whole
    -- snapshot closed: a partial party would read as a release to the server.
    local function party_entries(party, active, stages)
        local out = arr({})
        for _, m in ipairs(party.mons) do
            if not m.is_egg then
                local e, why = wire.party_entry(m, active, stages)
                if not e then return nil, "slot " .. tostring(m.slot) .. ": " .. tostring(why) end
                out[#out + 1] = e
            end
        end
        return out
    end

    local function snapshot_party()
        local party, why = current_party()
        if not party then return nil, why end
        local battle = reads.read_battle()
        if not battle then return nil, "battle state unreadable" end
        local active = battle.mode ~= 0 and battle.active_slot or nil
        local stages = active and reads.read_stat_stages("player") or nil
        local out
        out, why = party_entries(party, active, stages and stages.wire or nil)
        if not out then
            if self.snapshot_refusal ~= why then
                self.snapshot_refusal = why
                log("[SLink-gen2] party snapshot refused: " .. tostring(why))
            end
            return nil, why
        end
        self.snapshot_refusal = nil
        return out, battle
    end

    local function enemy_party(battle)
        if battle.mode == 0 then return arr({}) end
        local foe = reads.read_battle_mon("enemy")
        local stages = reads.read_stat_stages("enemy")
        local e = foe and wire.foe_entry(foe, stages and stages.wire or nil)
        return arr({ e })
    end

    -- Gen 2: the Ball pocket holds only balls (reads.read_pocket "balls"); Gen 1 needed an
    -- item-id set because its balls share the one bag list.
    local function ball_count()
        local pocket = reads.read_pocket("balls")
        local n = 0
        if pocket then for _, it in ipairs(pocket.entries) do n = n + it.quantity end end
        return n
    end

    -- Gen 2: maps are (group, number); area_map is keyed group*256+number.
    local function area_of()
        local map = reads.read_map()
        if not map then return "", "" end
        local a = area_map[tostring(map.group * 256 + map.number)]
        if a then return a.area_id, a.name end
        return "", "map_" .. map.group .. "_" .. map.number
    end

    local function announce_nuzlocke_start()
        if self.nuzlocke_announced then return end
        self.nuzlocke_announced = true
        hud.nuzlocke_start("Nuzlocke Start!")
        self:request_sfx_local(95)
    end

    local function pc_boxes_wire()
        local out = arr({})
        for _, e in ipairs(self.box_cache) do out[#out + 1] = e end
        return out
    end

    -- KEY-SCOPE-5: pc_boxes is a complete census only alongside a generation. Omitted before any
    -- complete scan and whenever the latest scan was incomplete (box_cache still goes out for
    -- display), so the server never takes a partial cache for a fresh census.
    local function box_generation()
        return self.box_complete and self.box_generation or nil
    end

    -- Gen 2: fourteen boxes, the current one authoritative in the active sBox shadow and the
    -- rest in their backing SRAM boxes (reads.lua read_box); eggs omitted as in the party.
    function self:rescan_boxes()
        local cur = reads.read_current_box_num()
        if cur == nil then self.box_complete = false; return self.box_cache end
        local cache, complete = {}, true
        for box = 0, c.NUM_BOXES - 1 do
            local mons
            if box == cur then mons = reads.read_active_box() else mons = reads.read_storage_box(box) end
            complete = complete and mons ~= nil -- any failed box read: not a census
            for _, m in ipairs(mons and mons.mons or {}) do
                local e = (not m.is_egg) and wire.box_entry(m, box) or nil
                if e then cache[#cache + 1] = e end
            end
        end
        self.box_cache, self.box_complete = cache, complete
        if complete then self.box_generation = self.box_generation + 1 end
        return cache
    end

    -- KEY-SCOPE-5: a change refused for a non-terminal reason goes out again once a complete box
    -- census NEWER than the refusal has gone out (this tick carried it)
    local function resend_refused_change()
        local a = self.key_alias
        if a and a.retry_gen and self.box_complete and self.box_generation > a.retry_gen then
            a.retry_gen = nil
            send("key_change", a.msg)
        end
    end

    -- ── the writes gate (R-4, W-6) ───────────────────────────────────────────────────
    local function game_is_live()
        local party, why = current_party()
        if not party then return false, "party unreadable: " .. tostring(why) end
        local player = reads.read_player()
        if not player then return false, "player unreadable" end
        if player.ot_id == 0 and party.count == 0 then return false, "pre-game (title/new game)" end
        return true
    end

    local function hello_identity()
        local player = reads.read_player()
        local id = player and player.ot_id
        if type(id) ~= "number" or id % 1 ~= 0 or id < 0 or id > 65535 then
            return nil, "player identity unavailable"
        end
        -- Gen 2: no save-version stamp in the key (Gen 1's is pureRGB's wGameInternalVersion)
        return table.concat({ tostring(self.player), self.foundation, self.artifact_kind,
                              tostring(self.rom_sha1), tostring(id) }, "|")
    end

    -- One reset path: the hello session, the binder's latches and every client latch go
    -- together, so nothing observed before a reset/reload can settle after it.
    function self:boundary(kind, why)
        self.epoch = self.epoch + 1
        self.dead_synced = false -- INV-CLIENT-2: the reloaded save waits for the hello's dead_keys
        if self.signals then self.signals:boundary(kind) end
        self.faint_latches, self.battle, self.pending_rescan = {}, nil, false
        self.key_alias, self.retired_alias = nil, {}
        self:rival_close("the " .. kind .. " boundary")
        -- O-30: a reset/reload leaves the battle; the owed deaths go to the checkpoint (Gen 1 battle_end)
        for _, w in ipairs(self.pending_battle_writes) do defer_held(w) end
        self.pending_battle_writes, self.commanded = {}, {}
        if settle then FS.boundary(kind) end
        -- P4.3b: Init clears the lease on reset; a reload leaves the Trade Center. The hello resyncs.
        self:trade_forget("the " .. kind .. " boundary")
        if dev_trade then dev_trade.forget("the " .. kind .. " boundary", true) end
        -- review m2: a trade report held for the hello is lost with the timeline; the side cannot vouch
        for _, m in ipairs(self.held) do
            if m.event == "trade_done" and m.fields and m.fields.token then
                self.trade_owed[#self.trade_owed + 1] = { event = "trade_done",
                    fields = { token = m.fields.token, uncertain = true } }
            end
        end
        drop_held("the " .. kind .. " boundary")
        self.hello_session:invalidate(why or kind)
    end

    -- A savestate load or rewind abandons the timeline (R4 S2). Unlike boundary(), which keeps
    -- what the engine finalized before a natural in-engine reset/reload, nothing observed on the
    -- timeline the player left may be published on the one they resumed: queued binder batches,
    -- pending acquisition/faint latches, the battle context and held messages all go, and the
    -- epoch moves so a settled observation stamped before the load is stale.
    function self:abandon_timeline(why)
        self.epoch = self.epoch + 1
        self.dead_synced = false
        if self.signals then self.signals:abandon(why) end
        self.faint_latches, self.deferred = {}, {}
        self.battle, self.pending_safe, self.pending_rescan = nil, false, true
        self.pending_battle_writes, self.commanded = {}, {}
        if settle then FS.abandon(why) end
        self:rival_close(why)
        self:trade_forget(why)
        if dev_trade then dev_trade.forget(why, true) end
        -- A delayed retirement may be lost after rewinding a key change; retaining its alias could faint another record.
        self.key_alias, self.retired_alias = nil, {}
        drop_held(why)
        -- Hello readiness names the still-open TCP connection and player OT, not a frame epoch;
        -- a changed OT invalidates it in hello_session:step. The next tick reports the resumed
        -- party and the pending rescan attempts to refresh its boxes before that tick.
    end

    function self:validate()
        local ok, why = game_is_live()
        if ok then
            self.invalid_streak = 0
            if not self.writes_enabled and not self.gate_revoked then
                self.writes_enabled = true
                log("[SLink-gen2] writes ENABLED")
            end
            if self.gate_revoked then
                self.gate_revoked, self.writes_enabled = false, true
                log("[SLink-gen2] writes re-enabled after a live validation")
            end
        else
            self.invalid_streak = self.invalid_streak + 1
            local player = reads.read_player()
            if player and player.ot_id == 0 then self:boundary("reset", "save_reset") end
            if self.invalid_streak >= Client.MAX_INVALID and self.writes_enabled then
                self.writes_enabled, self.gate_revoked = false, true
                log("[SLink-gen2] writes PAUSED: " .. tostring(why))
            end
        end
        return ok, why
    end

    -- ── inbound commands ─────────────────────────────────────────────────────────────
    local function ack_cancel(cmd)
        if cmd.cmd == "show_choices" then send("menu_result", { token = cmd.token, choice = 127 })
        elseif cmd.cmd == "show_menu" then send("menu_result", { token = cmd.token, choice = 0 })
        elseif cmd.cmd == "choose_mon" then send("mon_chosen", { token = cmd.token, slot = 7 }) end
    end

    -- Returns slot, mon, party, why; `why` = "ambiguous key" when two slots answer.
    -- ponytail: Gen 1's frozen record-evidence latch is not ported: both Gen 2 key_change
    -- sources (NPC trade, evolution) are refused by the binder when the old or new full key
    -- names a second record, and two answering slots refuse here. Ceiling: a record leaving
    -- and an identical one arriving before the ack (Gen 1 cx-fc0d91b7); port it if that bites.
    --
    -- `death` (the command name: force_faint/explode and memorialize, never the other box ops):
    -- a missing exact key falls back to the evolution-stable identity. Gen 2 sends no key_change
    -- for evolution (U1 OPEN), and EvolveAfterBattle both rewrites the species and adds the
    -- max-HP gain to a fainted mon's HP, so a death owed past the battle would otherwise escape
    -- (A2). The DV word + OT id
    -- (key fields 1-2) must name exactly one party mon, and its species must descend from the
    -- key's (evolutions.json), so a DV/OT collision never kills another mon. Client.key_identity
    -- parses both key shapes; `old` is the key's species field, the pre-evolution species.
    local evolutions = p.evolutions or {}
    local function descends(from, to)
        for _, nxt in ipairs(evolutions[string.format("%d", from)] or {}) do
            if nxt == to or descends(nxt, to) then return true end
        end
        return false
    end
    local function find_party_slot(key, death)
        local party = current_party()
        if not party then return nil end
        local target = self.retired_alias[key] or key
        local slot, mon
        for _, m in ipairs(party.mons) do
            if not m.is_egg and mon_key(m) == target then
                if slot then return nil, nil, party, "ambiguous key" end
                slot, mon = m.slot, m
            end
        end
        if slot or not death then return slot, mon, party end
        local stable, old = Client.key_identity(target)
        for _, m in ipairs(party.mons) do
            if stable and not m.is_egg and Client.key_identity(mon_key(m)) == stable then
                if mon then return nil, nil, party, "ambiguous evolved match" end
                mon = m
            end
        end
        if not mon then return nil, nil, party end
        if not (old and descends(old, mon.species_id)) then
            return nil, nil, party, "DV/OT match " .. mon_key(mon) .. " is not a descendant"
        end
        log("[SLink-gen2] " .. death .. " matched evolved " .. target .. "->" .. mon_key(mon))
        return mon.slot, mon, party
    end

    -- O-30 ruling (a): the Bug-Catching Contest hides every party mon but the lead
    -- (ContestDropOffMons) until ContestReturnMons; a death for a hidden mon waits for the return.
    local contest = p.contest_mask
    local function contest_masked()
        if not contest or io.bank_valid(contest.bank, contest.address, 1) ~= true then return false end
        return math.floor(io.read_u8(contest.address, "System Bus") / 2 ^ contest.bit) % 2 == 1
    end

    local function hud_color(cmd)
        if type(cmd.color) == "table" then return cmd.color[1], cmd.color[2], cmd.color[3], cmd.duration end
        return cmd.r, cmd.g, cmd.b, cmd.frames
    end

    -- The one native-SFX gate (P4.2b, Gen 1's shape): a server `play_sound` and every LOCAL cue
    -- request through this. panel:sfx_code_for maps the Gen 3 SE id on the wire to this
    -- cartridge's semantic code; an id with no Gen 2 sound is dropped silently, never sent. The
    -- panel's arbiter keeps one cue per frame (lua/sfx_arbiter.lua), so no per-frame set here.
    function self:request_sfx_local(gen3_id)
        local code = panel and panel:sfx_code_for(gen3_id)
        if panel and not code then return end
        if code and self.config and self.config.native_sounds == true and panel:sfx_present() then
            panel:request_sfx(code)
        elseif not self.sfx_unavailable_logged then
            self.sfx_unavailable_logged = true
            log("[SLink-gen2] play_sound: native sounds off or no SFX-capable SLink cartridge")
        end
    end

    function self:handle_command(cmd)
        local c_ = cmd.cmd
        if dev_trade and dev_trade.command(cmd) then return end
        if c_ == "noop" then return end
        -- P4.5c: an optional "phone" tag rides force_faint/msgbox; phone.lua drops it off a phone build
        -- Phone calls are their own run switch, default ON (owner 2026-10-02), not native_messages:
        -- only an explicit phone_calls=false silences the ring; the HUD prompt below always shows.
        if phone and cmd.phone ~= nil and not (self.config and self.config.phone_calls == false) then
            phone:request(cmd.phone, cmd.phone_data) -- + PHONE-NAMES
        end
        self.arrivals = self.arrivals + 1 -- Gen 1 parity: every command's arrival order (defer_held)
        if settle and (c_ == "force_faint" or c_ == "force_explode") then return FS.receive(cmd, c_) end
        if c_ == "force_faint" or c_ == "force_explode" then
            -- W-3: force_explode rides the same queue; only the active battler at the battle hold explodes
            -- (at_battle_hold), a bench mon or a checkpoint death is the plain faint (Gen 1 parity).
            local slot, mon, party, why = find_party_slot(cmd.key, c_)
            if why then log("[SLink-gen2] " .. c_ .. ": " .. why .. " " .. tostring(cmd.key)) return end
            local entry = { cmd = c_, key = cmd.key, nickname = cmd.nickname, arrival = self.arrivals }
            -- a key not in the party is deferred, never dropped on receipt: ContestDropOffMons masks the
            -- party before ENGINE_BUG_CONTEST_TIMER is set and ContestReturnMons unmasks it after the flag
            -- is cleared (O-30 review MINOR-3); the checkpoint holds it through the contest or drops it
            if not party or not slot then self.deferred[#self.deferred + 1] = entry return end
            local battle = reads.read_battle()
            if battle and battle.mode ~= 0 then
                if p.battle_hold then
                    -- O-30 (Gen 1 pending_battle_writes): the death lands at the next battle hold,
                    -- active battler and bench alike; a battle that ends first hands it to the checkpoint.
                    -- O-32: a bench death lands this frame instead (land_bench_deaths, from frame_end)
                    self.pending_battle_writes[#self.pending_battle_writes + 1] = entry
                    self.bench_owed = true
                    -- p.battle_release_poll: the composition cannot land an active faint at the hold (it is handed to the
                    -- checkpoint after the battle); retain a diagnostic without a speculative KO banner
                    if p.battle_release_poll and c_ == "force_faint" and battle.active_slot == slot then
                        log("[SLink-gen2] " .. c_ .. " held for the checkpoint: no in-battle active faint composed " .. cmd.key)
                        -- Hold diagnostics stay in the log; only a proved KO gets a player banner.
                    end
                    return
                end
                if battle.active_slot == slot then
                    -- no battle hold composed (production before the battle_faint receipt): the
                    -- command waits for the checkpoint (protocol §5: deferred to battle end)
                    log("[SLink-gen2] " .. c_ .. " held for the checkpoint: no in-battle active faint composed " .. cmd.key)
                    hud.show("KO held: " .. nick_label(cmd.key, mon and mon.nickname) .. " is battling", 255, 160, 64, 300)
                end
            end
            self.deferred[#self.deferred + 1] = entry
            return
        end
        if BOX_NACK[c_] then
            if not boxes then
                log("[SLink-gen2] " .. c_ .. " refused: " .. BOX_OPEN .. " " .. tostring(cmd.key))
                send(BOX_NACK[c_], { key = cmd.key, reason = BOX_OPEN })
                return
            end
            -- Gen 1 parity: queued in arrival order with the faints; run one per checkpoint hold
            self.deferred[#self.deferred + 1] = { cmd = c_, key = cmd.key, nickname = cmd.nickname,
                                                  arrival = self.arrivals }
            return
        end
        if c_ == "replace_rival_team" then
            self:replace_rival_team(cmd)
        elseif c_ == "msgbox" or c_ == "gui_prompt" then
            local r, g, b, f = hud_color(cmd)
            hud.prompt(cmd.text, r, g, b, f)
        elseif c_ == "hud_show" then
            local r, g, b, f = hud_color(cmd)
            hud.show(cmd.text, r, g, b, f)
        elseif c_ == "play_sound" then
            self:request_sfx_local(cmd.sound)
        elseif c_ == "resolved_areas" then
            self.resolved_areas = {}
            for _, a in ipairs(cmd.areas or {}) do self.resolved_areas[a] = true end
            self.seeded = true
        elseif c_ == "unresolve_area" then
            self.resolved_areas[cmd.area_id] = nil
        elseif c_ == "dead_keys" then
            self.dead_keys, self.dead_synced = {}, true
            for _, k in ipairs(cmd.keys or {}) do self.dead_keys[k] = true end
        elseif c_ == "key_change_ack" then
            local a = self.key_alias
            if a and a.old_key == cmd.old_key then self.key_alias = nil end
            self.retired_alias[cmd.old_key] = nil
        elseif c_ == "key_change_rejected" and Client.RETRYABLE_REJECTIONS[cmd.reason] then
            -- KEY-SCOPE-5: nothing was retired; the alias stays and the change is re-sent after the
            -- next complete box census (a census refusal asks for that census now)
            local a = self.key_alias
            if a and a.old_key == cmd.old_key then
                a.retry_gen = self.box_generation
                if cmd.reason == "box census unavailable" then self.pending_rescan = true end
            end
            log("[SLink-gen2] key_change refused (will retry): " .. tostring(cmd.reason) .. " " .. tostring(cmd.old_key))
        elseif c_ == "key_change_rejected" then
            -- U5 (a key collision): terminal
            -- the cartridge cannot roll back; the retirement the server queues under the old
            -- key must find the mon by the key it physically holds now
            local a = self.key_alias
            if a and a.old_key == cmd.old_key then
                self.retired_alias[a.old_key] = a.new_key
                self.key_alias = nil
            end
            log("[SLink-gen2] key_change rejected: " .. tostring(cmd.reason) .. " " .. tostring(cmd.old_key))
            hud.show("IDENTITY CHANGE REFUSED: " .. tostring(cmd.reason or "collision"), 255, 64, 64, 600)
        elseif c_ == "config" then
            self.config = cmd
            -- a cue accepted under the old setting must not post after it
            if cmd.native_sounds ~= true and panel then panel:clear_sfx() end
        elseif c_ == "game_over" then
            self:request_sfx_local(26)
            hud.set_game_over()
            self.game_over = true
        elseif c_ == "rebuild_start" then
            hud.set_rebuilding(cmd.text)
        elseif c_ == "rebuild_done" then
            hud.clear_rebuilding()
        elseif c_ == "show_menu" and self:trade_live() and cmd.blob_hex and cmd.slot ~= nil then
            self:trade_prompt(cmd)
        elseif c_ == "apply_trade" and self:trade_live() then
            self:trade_apply(cmd)
        elseif c_ == "apply_prepare" then
            self:trade_prepare_answer(cmd)
        elseif c_ == "withdraw_trade" then
            self:trade_withdraw(cmd)
        elseif c_ == "trade_mask" and self:trade_live() then
            self:trade_answer_query(cmd.mask or 0)
        elseif c_ == "trade_offer_ack" and self:trade_live() then
            self:trade_answer_offer(cmd.ok == true, type(cmd.token) == "string" and cmd.token or nil)
        elseif c_ == "show_choices" or c_ == "show_menu" or c_ == "choose_mon" then
            ack_cancel(cmd) -- Gen 2: no picker; a native trade prompt needs the trade build (above)
        elseif c_ == "apply_trade" then
            -- Gen 2: no SLINK trade path on this cartridge: "nothing changed" with the pre-trade key (§5)
            local slot, mon = find_party_slot(cmd.old_key)
            owe("trade_done", { token = cmd.token, slot = slot or cmd.slot, new_key = cmd.old_key,
                                new_species = mon and mon.species_id or 0 })
        elseif c_ == "link_panel" then
            -- P4.1f panel: held until the cartridge asks for it (panel.lua); no panel = nothing happened
            if panel then
                local pok, perr = panel:hold(cmd.rows)
                if not pok then log("[SLink-gen2] link_panel: " .. tostring(perr)) end
            end
        elseif c_ == "trade_mask" or c_ == "trade_offer_ack"
               or c_ == "ghost_pos" or c_ == "pending_keys" then
            -- Gen 2: P4 trade/panel/presence (pending_keys feeds Gen 1's APEX set only)
        else
            log("[SLink-gen2] unknown command " .. tostring(c_))
        end
    end

    -- ── in-game SLINK TRADE (P4.3b; O-27 D2-D4; Gen 1's shape, lua/gen1/client.lua trade_*) ──────
    -- The cartridge (patch/gen2/src/trade_*.asm) waits in the Trade Center for our lease bytes; the
    -- binder (p.trade) owns the frame, the staging and its own permit. Gen 2 differs from Gen 1:
    --   * ONE token per visit: minted at the query answer (proposer) or the PROMPT (responder) and
    --     reused through APPLY; the asm refuses an APPLY whose token is not the accepted one.
    --   * the role is the first command of the visit (trade_mask -> proposer, show_menu -> responder);
    --     there is no role byte.
    --   * the lease is owned, so a frame that changes before pickup, or closes without DONE, is the
    --     cartridge ending the visit (B, its own timeout, a reset): nothing was committed.
    -- Live only on a trade build (p.trade), an overlay kind, and a cartridge advertising the cap.
    local PROMPT, APPLY = 3, 5 -- gb_trade_lease commands
    function self:trade_live()
        if dev_trade then return dev_trade.advertised() end
        return trade ~= nil and (self.artifact_kind == "overlay" or self.artifact_kind == "rand_overlay")
            and trade:advertised()
    end
    local function hex_bytes(hex)
        local out = {}
        for pair in tostring(hex or ""):gmatch("%x%x") do out[#out + 1] = tonumber(pair, 16) end
        return out
    end
    local function new_token()
        local t = {}
        for i = 1, 4 do t[i] = math.random(1, 255) end
        return t
    end
    -- the cartridge waits <= SLINK_TRADE_QUERY/OFFER_FRAMES (600) for these answers: never hold them for a later hello
    local function send_now(event, fields)
        return self.hello_session:status().ready == true and send(event, fields)
    end
    local function traded(fn, what)
        local ok, a, b = pcall(fn)
        if not ok then a, b = nil, tostring(a) end
        if not a then log("[SLink-gen2] trade " .. what .. " refused: " .. tostring(b)) end
        return a, b
    end
    local function nothing_changed(token, slot, old_key, why)
        log("[SLink-gen2] apply_trade: " .. why .. "; nothing changed")
        owe("trade_done", { token = token, slot = slot, new_key = old_key, new_species = 0 })
    end

    function self:trade_answer_query(mask)
        local st = self.trade_state
        if not st or st.kind ~= "query" then return end
        local token = new_token()
        if contest_masked() then mask = 0 end -- the cartridge refuses a contest trade (9805ac1c)
        local ok = traded(function() return trade:answer_query(st.gen, mask, token) end, "query answer")
        self.trade_state = nil
        self.trade_visit = (ok and mask ~= 0) and { role = "proposer", token = token } or nil
    end

    -- The server holds an offer until the partner answers; a proposer whose cartridge has left
    -- withdraws it (menu_result choice 0 under the ack's token), or the partner's YES would apply
    -- the partner side alone. Owed like trade_done: sent once the hello is ready (frame_end).
    local function withdraw_offer(server_token, why)
        log("[SLink-gen2] trade offer withdrawn: " .. why)
        self.trade_owed[#self.trade_owed + 1] = { event = "menu_result",
                                                  fields = { token = server_token, choice = 0, withdraw = true } }
    end

    function self:trade_answer_offer(accept, server_token)
        local st, visit = self.trade_state, self.trade_visit
        if not st or st.kind ~= "offer" then return end
        local ok = traded(function() return trade:answer_offer(st.gen, accept) end, "offer answer")
        self.trade_state = nil
        if ok and accept and visit then
            visit.slot, visit.accepted, visit.server_token = st.slot, true, server_token
        else
            self.trade_visit = nil
            -- the ack came after the cartridge's offer wait (SLINK_TRADE_OFFER_FRAMES): the server accepted, the game did not
            if accept and server_token then withdraw_offer(server_token, "the offer wait expired before the ack") end
        end
    end

    -- Responder: stage the proposer's mon and let the cartridge ask its own YES/NO.
    function self:trade_prompt(cmd)
        if contest_masked() then
            -- 9805ac1c: SlinkTradeCheckParty refuses during the Bug-Catching Contest; never arm it
            self.trade_visit = nil
            send("menu_result", { token = cmd.token, choice = 0 })
            return
        end
        local token = new_token()
        local gen = traded(function()
            return trade:arm(PROMPT, cmd.slot, token, { blob = hex_bytes(cmd.blob_hex) })
        end, "prompt arm")
        if not gen then
            self.trade_visit = nil
            send("menu_result", { token = cmd.token, choice = 0 })
            return
        end
        self.trade_visit = { role = "responder", token = token, slot = cmd.slot }
        self.trade_state = { kind = "prompt", gen = gen, token = cmd.token, frame = self.frame }
    end

    -- MAJOR-1 prepare round: before either side may commit, can THIS cartridge still take its APPLY?
    -- The checks trade_apply makes, moved ahead of both commits; nothing is staged. A side that
    -- cannot answers ok = false and forgets the visit (the server cancels both).
    function self:trade_prepare_answer(cmd)
        local visit = self.trade_visit
        local ok = self:trade_live() and visit ~= nil and visit.accepted == true and self.trade_state == nil
                   and find_party_slot(cmd.old_key) == visit.slot and not trade:closed()
        if not ok then self.trade_visit = nil end
        send("apply_ready", { token = cmd.token, ok = ok })
    end

    -- Both sides: stage the OTHER mon under the accepted visit token; the cartridge commits.
    function self:trade_apply(cmd)
        local visit = self.trade_visit
        local slot = find_party_slot(cmd.old_key)
        if not visit or not visit.accepted then
            return nothing_changed(cmd.token, slot or cmd.slot, cmd.old_key, "no accepted trade visit")
        end
        if slot == nil or slot ~= visit.slot then
            self.trade_visit = nil
            return nothing_changed(cmd.token, slot or cmd.slot, cmd.old_key, "old_key is not the visit's slot")
        end
        if trade:closed() then
            self.trade_visit = nil
            return nothing_changed(cmd.token, slot, cmd.old_key, "the cartridge already left the Trade Center")
        end
        local gen = traded(function()
            return trade:arm(APPLY, slot, visit.token, { blob = hex_bytes(cmd.blob_hex),
                                                         partner_name = cmd.partner_name })
        end, "apply arm")
        if not gen then
            self.trade_visit = nil
            return nothing_changed(cmd.token, slot, cmd.old_key, "apply arm refused")
        end
        self.trade_state = { kind = "apply", gen = gen, token = cmd.token, old_key = cmd.old_key, slot = slot,
                             frame = self.frame }
    end

    -- An entered native commit that never published DONE may have mutated or saved: Gen 1's result-2
    -- handling (no release, no key claim). It is DECLARED, trade_done{token, uncertain = true}, once
    -- the hello is ready (after a reset: the post-reset hello, frame_end); the server journals it and
    -- that side's next party snapshot settles the link, instead of the applying watchdog.
    -- Mirrored by lua/gen1/client.lua trade_uncertain.
    -- after_reset (MAJOR-5): a native result 2 holds until a reset, and its RAM party was never
    -- saved; the server then takes evidence only from the reloaded save (the post-reset hello).
    local function trade_uncertain(st, why, after_reset)
        hud.show("TRADE UNCERTAIN - CHECK PARTY", 255, 64, 64, 600)
        log("[SLink-gen2] apply_trade uncertain: " .. why .. "; no release, no key claim")
        if st.declared then return end
        st.declared = true
        self.trade_owed[#self.trade_owed + 1] = { event = "trade_done",
            fields = { token = st.token, uncertain = true, after_reset = after_reset or nil } }
    end
    function self:trade_forget(why)
        local st, visit = self.trade_state, self.trade_visit
        if st and st.kind == "apply" and trade and trade.committing then trade_uncertain(st, tostring(why))
        elseif st and st.kind == "apply" then
            -- review m2: before SlinkTradeCommit nothing was mutated or saved: a certain none, owed
            log("[SLink-gen2] apply_trade forgotten before the commit entry (" .. tostring(why) .. "); nothing changed")
            self.trade_owed[#self.trade_owed + 1] = { event = "trade_done",
                fields = { token = st.token, slot = st.slot, new_key = st.old_key, new_species = 0 } }
        elseif st and st.kind == "prompt" then
            self.trade_owed[#self.trade_owed + 1] = { event = "menu_result", fields = { token = st.token, choice = 0 } }
        elseif not st and visit and visit.server_token then withdraw_offer(visit.server_token, tostring(why)) end
        self.trade_state, self.trade_visit = nil, nil
    end

    local trade_end
    -- MAJOR-4 (review e9d5e136): the server asks a silent side to withdraw its APPLY. Unpicked: pull
    -- it and report nothing changed (certain). Past the commit entry: uncertain. Picked up but not
    -- committing yet: the cartridge answers on its own (DONE or a close) within frames.
    -- Mirror of lua/gen1/client.lua trade_withdraw.
    function self:trade_withdraw(cmd)
        local st = self.trade_state
        if not (trade and st and st.kind == "apply" and st.token == cmd.token) then return end
        if trade.phase == "armed" then
            traded(function() trade:withdraw() return true end, "withdraw")
            return trade_end(st, "the server withdrew the unpicked APPLY")
        end
        if trade.committing then trade_uncertain(st, "the server asked for a withdrawal") end
    end

    function trade_end(st, why)
        if st.kind == "prompt" then
            log("[SLink-gen2] trade prompt ended: " .. why)
            send("menu_result", { token = st.token, choice = 0 })
        else
            nothing_changed(st.token, st.slot, st.old_key, why)
        end
        self.trade_state, self.trade_visit = nil, nil
    end

    -- Per-frame: watch the lease for the receptionist questions and the native completions.
    function self:trade_tick()
        if not self:trade_live() then return end
        local st = self.trade_state
        if st and (st.kind == "prompt" or st.kind == "apply") then
            local done = trade:poll_done()
            if not done then
                if trade:clobbered() or trade:closed() then
                    if st.kind == "apply" and trade.committing then
                        return self:trade_forget("the visit ended after SlinkTradeCommit was entered")
                    end
                    -- before the commit entry the asm refused (precommit checks) or timed out
                    return trade_end(st, "the cartridge ended the visit")
                end
                if trade.phase == "armed" and self.frame - st.frame > Client.TRADE_PICKUP_FRAMES then
                    traded(function() trade:withdraw() return true end, "withdraw")
                    return trade_end(st, "never picked up (timeout)")
                end
                return
            end
            if st.kind == "prompt" then
                local released = traded(function() return trade:release(st.gen) end, "prompt release")
                local yes = done.result == 0 and released == true
                send("menu_result", { token = st.token, choice = yes and 1 or 0 })
                if yes then self.trade_visit.accepted = true else self.trade_visit = nil end
                self.trade_state = nil
                return
            end
            if done.result == 2 then
                -- native append uncertain (T-5 limit): the cartridge keeps the lease; no release, no claim
                if not st.warned then
                    st.warned = true
                    trade_uncertain(st, "native result 2; holding until a reset", true)
                end
                return
            end
            traded(function() return trade:release(st.gen) end, "apply release")
            local party = done.result == 0 and current_party() or nil
            local received = party and party.mons[#party.mons]
            if received and not received.is_egg then
                -- REMOVE+compact then APPEND: the received (possibly evolved) mon is the last slot
                owe("trade_done", { token = st.token, slot = received.slot, new_key = mon_key(received),
                                    new_species = received.species_id })
            else
                nothing_changed(st.token, st.slot, st.old_key, "native result " .. tostring(done.result))
            end
            self.trade_state, self.trade_visit = nil, nil
            self.pending_rescan = true
            return
        end
        local visit = self.trade_visit
        if not st and visit and visit.server_token and trade:closed() then
            -- SLINK_TRADE_APPLY_FRAMES ran out (or B) before the APPLY: the proposer left its offer
            self.trade_visit = nil
            return withdraw_offer(visit.server_token, "the cartridge left before APPLY")
        end
        local q = trade:poll_query()
        if q and not (st and st.kind == "query" and st.gen == q.gen) then
            self.trade_state, self.trade_visit = { kind = "query", gen = q.gen }, nil
            if not send_now("trade_query", {}) then
                traded(function() return trade:answer_query(q.gen, 0, new_token()) end, "offline query answer")
                self.trade_state = nil
            end
            return
        end
        local o = trade:poll_offer()
        if o and not (st and st.kind == "offer" and st.gen == o.gen) then
            self.trade_state = { kind = "offer", gen = o.gen, slot = o.slot }
            if not send_now("trade_offer", { slot = o.slot }) then
                traded(function() return trade:answer_offer(o.gen, false) end, "offline offer answer")
                self.trade_state, self.trade_visit = nil, nil
            end
        end
    end

    -- C4b (docs/polished/TRADE_PUMP.md): Polished pump, with advertisement owned by the binder. Entry builds the
    -- binder AFTER this client, so it may install {binder, charmap} once, before start().
    -- The historical install_dev name remains compatible; vanilla state is not borrowed by this interface.
    function self:install_dev_trade_pump(spec)
        assert(dev_trade == nil and trade == nil, "ambiguous trade composition")
        assert(self.foundation == "gen2_polished" and (self.artifact_kind == "overlay" or self.artifact_kind == "rand_overlay"),
               "dev trade requires admitted Polished overlay")
        local binder, charmap = assert(spec.binder), assert(spec.charmap)
        for _, name in ipairs({"poll_query", "poll_offer", "poll_done", "phase", "disposition",
                               "answer_query", "answer_offer", "arm", "release", "closed", "reset", "cancel"}) do
            assert(type(binder[name]) == "function", "dev trade missing " .. name)
        end
        assert(type(charmap.encoding) == "table" and charmap.terminator == profile.overlay.panel.terminator,
               "dev trade requires admitted charmap")
        local family = profile.overlay.trade
        local visit, serial, stopped, last_token = nil, 0, false, nil
        local D = {}
        function D.advertised() return binder:advertised() == true end
        local function call(name, ...)
            local ok, a, b, why = pcall(binder[name], binder, ...)
            if not ok then return nil, "UNCERTAIN", tostring(a) end
            return a, b, why
        end
        local poll_failures = {}
        local function poll_request(name)
            local request, disposition, reason = call(name)
            if not request and (disposition ~= nil or reason ~= nil) then
                local diagnostic = tostring(disposition) .. ": " .. tostring(reason)
                if poll_failures[name] ~= diagnostic then
                    log("[SLink-gen2] trade " .. name .. " refused (will retry): " .. diagnostic)
                    poll_failures[name] = diagnostic
                end
                return nil, true -- read failure is not an absent request or an APPLY outcome
            end
            poll_failures[name] = nil
            return request, false
        end
        local function report(event, fields) owe(event, fields) end
        local function finish(why, uncertain, after_reset)
            local v = visit
            if not v or v.terminal then return end
            v.terminal, v.prepare, v.apply = true, nil, nil
            last_token = v.server_token or last_token
            if not v.attempted and v.gen ~= nil and why ~= "offer declined" and why ~= "prompt declined" then
                local _, kind = call("cancel", why)
                if kind == "UNCERTAIN" then v.retired = true end
            end
            log("[SLink-gen2] dev trade " .. tostring(why))
            if not v.server_token then return end
            if v.attempted then
                local fields = {token=v.server_token}
                if uncertain and v.attempted then
                    fields.uncertain, fields.after_reset = true, after_reset or nil
                else
                    fields.slot, fields.new_key, fields.new_species = v.slot, v.old_key, 0
                end
                report("trade_done", fields)
            else
                report("menu_result", {token=v.server_token, choice=0,
                                      withdraw=v.role == "proposer" or nil})
            end
        end
        -- A poisoned published APPLY cannot use live RAM as recovery evidence. Retire only
        -- after reporting the same reset-only contract used by native DONE result 2.
        local function finish_poison(why)
            local v = visit
            if not v or v.terminal then return end
            if v.attempted then hud.show("TRADE UNCERTAIN - CHECK PARTY", 255, 64, 64, 600) end
            finish(why, v.attempted, v.attempted)
            v.retired = true
        end
        function D.forget(why, after_reset)
            finish(why, true, after_reset)
            -- Cancel pre-APPLY visits above; an attempted/poisoned visit remains owned by the ROM.
            -- Never release/reset poison; a verified closed cancelled visit may reset normally.
            if visit then visit.retired = true end
        end
        function D.busy()
            return (visit ~= nil and not visit.terminal) or call("closed") ~= true
        end
        local function begin(role)
            serial = serial + 1
            visit = {role=role, serial=serial, epoch=self.epoch, identity=hello_identity(),
                     token=new_token(), deadline=self.frame + family.timeouts.QUERY}
            return visit
        end
        local function current(v)
            return not v.terminal and not v.retired and v.epoch == self.epoch
                   and v.identity ~= nil and v.identity == hello_identity()
        end
        local function slot_key(slot)
            local party = current_party()
            for _, mon in ipairs(party and party.mons or {}) do
                if mon.slot == slot then return mon_key(mon) end
            end
        end
        local function payload(cmd)
            if type(cmd.blob_hex) ~= "string" or #cmd.blob_hex ~= 140
               or not cmd.blob_hex:match("^%x+$") then return nil end
            local blob, sender = hex_bytes(cmd.blob_hex), {}
            local size, term = family.staging.sender.size, charmap.terminator
            if cmd.partner_name ~= nil and cmd.partner_name ~= "" then
                if type(cmd.partner_name) ~= "string" then return nil end
                for ch in cmd.partner_name:gmatch(utf8.charpattern) do
                    local byte = charmap.encoding[ch]
                    if type(byte) ~= "number" or byte < family.validation.glyph_floor or byte > 255 then return nil end
                    if #sender < size - 1 then sender[#sender + 1] = byte end
                end
            else
                -- PROMPT has no partner_name: use ONLY OT text, never its three metadata bytes.
                for i=1,profile.derived.player_name_length do
                    local byte = blob[family.staging.party.size+i]
                    if byte == term then break end
                    sender[#sender+1] = byte
                end
            end
            while #sender < size do sender[#sender+1] = term end
            return {blob=blob, sender=sender}
        end
        local function matching(v, cmd)
            return current(v) and v.accepted and cmd.token == v.server_token
                   and cmd.slot == v.slot and cmd.old_key == v.old_key
                   and find_party_slot(cmd.old_key) == v.slot and not contest_masked()
        end
        local function ready(v)
            if not matching(v, {token=v.server_token, slot=v.slot, old_key=v.old_key}) then return false end
            local role, phase = call("phase")
            if role ~= v.role or phase ~= (v.role == "proposer" and "offer" or "released") then return false end
            local l, f = family.lease, family.lease.fields
            local bytes = io.read_range(l.base, l.size)
            if type(bytes) ~= "table" or #bytes ~= l.size then return false end
            for i=1,4 do
                if bytes[f.magic+i] ~= l.magic[i] or bytes[f.token+i] ~= v.token[i] then return false end
            end
            return bytes[f.version+1] == l.version and bytes[f.generation+1] == v.gen
                   and bytes[f.ack+1] == v.gen and bytes[f.slot+1] == v.slot and bytes[f.result+1] == 0
                   and bytes[f.command+1] == (v.role == "proposer" and family.commands.OFFER or family.commands.RELEASE)
        end
        function D.command(cmd)
            local name, v = cmd.cmd, visit
            if name == "trade_mask" then
                if v and current(v) and v.phase == "query" then
                    local mask = cmd.mask
                    if type(mask) ~= "number" or mask % 1 ~= 0 or mask < 0 or mask > 63 then
                        finish("invalid query mask"); return true
                    end
                    if contest_masked() then mask = 0 end
                    local ok, kind, why = call("answer_query", v.gen, mask, v.token)
                    if not ok then finish(why or kind); v.retired = v.retired or kind == "UNCERTAIN"
                    elseif mask == 0 then finish("no eligible pair")
                    else v.phase = "picking" end
                end
            elseif name == "trade_offer_ack" then
                if v and current(v) and v.phase == "offer" then
                    local accept = cmd.ok == true and type(cmd.token) == "string" and cmd.token ~= ""
                    v.server_token = accept and cmd.token or nil
                    local ok, kind, why = call("answer_offer", v.gen, accept)
                    if not ok then finish(why or kind); v.retired = v.retired or kind == "UNCERTAIN"
                    elseif not accept then finish("offer declined")
                    else
                        v.accepted, v.phase, v.gap = true, "accepted", io.framecount()
                        v.deadline = self.frame + family.timeouts.APPLY
                    end
                end
            elseif name == "show_menu" and (cmd.blob_hex ~= nil or cmd.slot ~= nil) then
                if cmd.token ~= nil and cmd.token == last_token then return true end
                if v then
                    if cmd.token ~= v.server_token then report("menu_result", {token=cmd.token, choice=0}) end
                    return true
                end
                local data = payload(cmd)
                if stopped or not data or type(cmd.token) ~= "string" or cmd.token == ""
                   or not slot_key(cmd.slot) or contest_masked() or call("closed") ~= true then
                    report("menu_result", {token=cmd.token, choice=0}); return true
                end
                v = begin("responder")
                v.server_token, v.slot, v.old_key = cmd.token, cmd.slot, slot_key(cmd.slot)
                v.deadline, v.phase = self.frame + family.timeouts.APPLY, "prompt"
                local gen, kind, why = call("arm", PROMPT, v.slot, v.token, data)
                if gen == nil then finish(why or kind); v.retired = v.retired or kind == "UNCERTAIN"
                else v.gen = gen end
            elseif name == "apply_prepare" or name == "apply_trade" then
                -- Foreign tokens never cancel or redirect the active visit.
                if not v or cmd.token ~= v.server_token then return true end
                if v.terminal or v.retired then return true end
                if name == "apply_trade" then v.apply_received = true end
                if not matching(v, cmd) then
                    if name == "apply_prepare" then report("apply_ready", {token=cmd.token, ok=false}) end
                    finish("trade identity mismatch", v.attempted); return true
                end
                if name == "apply_prepare" then
                    if not v.prepared and not v.attempted then v.prepare = true end
                else
                    local data = payload(cmd)
                    if not data then finish("malformed incoming payload", v.attempted); return true end
                    local fingerprint = cmd.blob_hex:lower() .. ":" .. table.concat(data.sender, ",")
                    if v.fingerprint then
                        if v.fingerprint ~= fingerprint then finish("conflicting APPLY", v.attempted); v.retired = true end
                        return true
                    end
                    v.fingerprint, v.apply_received, v.apply = fingerprint, true, data
                end
            elseif name == "withdraw_trade" then
                if v and cmd.token == v.server_token and not v.attempted then finish("server withdrew visit") end
                -- Once attempted, the binder alone decides DONE/uncertainty; never fabricate withdrawal.
            else return false end
            return true
        end
        function D.tick()
            local v = visit
            if v then
                if not current(v) and not v.terminal then D.forget("visit identity/epoch changed", true) end
                if not net.connected() and not v.terminal then D.forget("disconnected") end
                if v.retired and call("disposition") == "UNCERTAIN" then return end
                if v.terminal then
                    if call("closed") == true then
                        local ok = call("reset")
                        if ok then visit = nil; self.pending_rescan = true else v.retired = true end
                    end
                    return
                end
                local done, kind, why = call("poll_done")
                local role, phase = call("phase")
                if kind == "UNCERTAIN" or (done and done.disposition == "UNCERTAIN") then
                    -- MAJOR-5 parity (trade_uncertain): after APPLY a native result 2 / poison holds until a
                    -- reset and its RAM party was never saved; evidence comes only from the post-reset hello.
                    finish_poison(why or (done and done.reason) or kind); return
                end
                if done and v.phase == "prompt" and role == "responder"
                   and (phase == "consented" or phase == "declined") then
                    local ok, disposition, reason = call("release", v.gen)
                    if not ok then finish(reason or disposition); v.retired = v.retired or disposition == "UNCERTAIN"; return end
                    if done.disposition == "CONSENTED" then
                        v.accepted, v.phase, v.gap = true, "accepted", io.framecount()
                        report("menu_result", {token=v.server_token, choice=1})
                    else finish("prompt declined") end
                    return
                end
                if done and v.attempted then
                    if done.disposition == "COMPLETE" then
                        local party = current_party()
                        local received = party and party.mons[#party.mons]
                        if not received or received.is_egg then
                            -- Native success holds until RELEASE; a transient decoder refusal must not retire it.
                            v.postimage_deadline = v.postimage_deadline or (io.framecount() + family.timeouts.RELEASE)
                            if io.framecount() <= v.postimage_deadline then return end
                            finish("completed trade post-image unavailable", true); v.retired = true; return
                        end
                        local ok, disposition, reason = call("release", v.gen)
                        if not ok then finish(reason or disposition, true); v.retired = true; return end
                        -- Native REMOVE/compact/APPEND: reuse vanilla's owed trade_done shape and last-slot readback.
                        v.terminal, v.prepare, v.apply = true, nil, nil
                        last_token = v.server_token or last_token
                        report("trade_done", {token=v.server_token, slot=received.slot,
                                              new_key=mon_key(received), new_species=received.species_id})
                        self.pending_rescan = true
                        return
                    end
                    if done.disposition ~= "NOT_PERFORMED" then
                        finish("unexpected APPLY disposition", true); v.retired = true; return
                    end
                    local ok, disposition, reason = call("release", v.gen)
                    if not ok then finish(reason or disposition, true); v.retired = true; return end
                    finish("NOT_PERFORMED (commit disabled)")
                    return
                end
                local disposition, reason = call("disposition")
                if disposition == "UNCERTAIN" or disposition == nil then
                    finish(reason or "binder unavailable", v.attempted); v.retired = true; return
                end
                if call("closed") == true or (not v.attempted and self.frame > v.deadline) then
                    finish("visit closed or expired", v.attempted); return
                end
                -- Never answer OFFER / RELEASE PROMPT and publish APPLY in the same frame end.
                if v.accepted and io.framecount() > v.gap and (v.prepare or v.apply) then
                    local valid = ready(v)
                    if v.prepare then
                        v.prepare, v.prepared = nil, true
                        report("apply_ready", {token=v.server_token, ok=valid})
                    end
                    if not valid then finish("parked trade identity changed"); return end
                    if v.apply then
                        local data = v.apply
                        v.apply = nil
                        local gen, state, problem = call("arm", APPLY, v.slot, v.token, data)
                        v.attempted = gen ~= nil or state == "UNCERTAIN" -- no proof of non-publication stays held
                        if gen == nil then
                            if state == "UNCERTAIN" then finish_poison(problem or state)
                            else finish(problem or state, v.attempted) end
                        else v.gen, v.phase = gen, "apply" end
                    end
                    return
                end
            end
            if stopped or (visit and visit.terminal) then return end
            local q, failed = poll_request("poll_query")
            if failed then return end
            if q and not visit then
                v = begin("proposer")
                v.phase, v.gen = "query", q.gen
                if not send_now("trade_query", {}) then
                    call("answer_query", q.gen, 0, v.token); finish("offline query")
                end
                return
            end
            local offer, failed_offer = poll_request("poll_offer")
            if failed_offer then return end
            v = visit
            if offer and v and v.phase == "picking" then
                v.phase, v.gen, v.slot, v.old_key = "offer", offer.gen, offer.slot, slot_key(offer.slot)
                v.deadline = self.frame + family.timeouts.OFFER
                if not v.old_key or not send_now("trade_offer", {slot=offer.slot}) then
                    call("answer_offer", offer.gen, false); finish("offline offer")
                end
            end
        end
        function D.stop() stopped = true; D.forget("shutdown") end
        dev_trade = D
    end
    if p.dev_trade_pump ~= nil then self:install_dev_trade_pump(p.dev_trade_pump) end

    -- MINOR-7: a landed death is remembered under the server's key and the key the mon holds now (an
    -- evolved match), so the exact-key sweep below needs no evolution fallback (and logs nothing per frame)
    local function mark_dead(key, mon)
        self.dead_keys[key] = true
        if mon then self.dead_keys[mon_key(mon)] = true end
    end
    local function revived_dead()
        if not self.dead_synced then return nil end
        for key in pairs(self.dead_keys) do
            local slot, mon = find_party_slot(key)
            if slot and mon.hp > 0 and not (settle and FS.find_owner(key, mon_key(mon))) then return key end
        end
    end

    if FS then -- F2 settlement: defined only when the composition supplied the interface
    -- ── F2: Polished active-faint settlement (docs/polished/PLAIN_FAINT_F2.md) ─────────────────────────────
    -- ONLY when the composition supplied p.active_faint_settlement (compose_polished; vanilla never does, so FS is nil
    -- and every `settle` branch in this file is dead). One OBLIGATION per owed death is owned HERE (FS.owed);
    -- self.deferred / self.pending_battle_writes carry only scheduling REFERENCES ({ob = obligation}) to it, and a
    -- reference is borrowed by a hold, never the owner. States: pending (no write landed, retried at the next
    -- qualified hold), awaiting (an attempt was staged; NEVER retried, no KO shown, waits for a bound native-
    -- consumption witness), partial / lost (an attempt of unknown or partial effect: quarantined, no retry, no
    -- completion, no re-zero), stale (the save identity changed under it), done (removed from FS.owed). There is no
    -- force-faint ACK/NACK on the wire and none is invented here (docs/protocol.md section 5).
    local function nick_of(ob, mon) return nick_label(ob.key, ob.nickname or (mon and mon.nickname)) end
    local function note_once(ob, what, text)
        local k = ob.id .. ":" .. what
        if FS.logged[k] then return end
        FS.logged[k] = true
        log(text)
    end
    -- the open owner (current identity only) that names this logical or physical key
    function FS.find_owner(key, phys)
        for _, ob in ipairs(FS.owed) do
            if ob.gen == FS.gen and ob.identity == FS.last_identity
               and (ob.key == key or ob.phys == key or (phys and (ob.key == phys or ob.phys == phys))) then
                return ob
            end
        end
    end
    function FS.new_owner(fields)
        FS.next_id = FS.next_id + 1
        local ob = { id = FS.next_id, gen = FS.gen, epoch = self.epoch, state = "pending", attempts = {}, scheduled = false,
                     identity = FS.last_identity,
                     cmd = fields.cmd, key = fields.key, nickname = fields.nickname, arrival = fields.arrival,
                     quiet = fields.quiet }
        FS.owed[#FS.owed + 1] = ob
        return ob
    end
    function FS.reference(ob)
        ob.scheduled = true
        return { cmd = ob.cmd, key = ob.key, nickname = ob.nickname, arrival = ob.arrival, quiet = ob.quiet, ob = ob }
    end
    -- a quiet re-zero (a revived completed death): nil while any open owner already names the key
    function FS.quiet_entry(key, mon)
        if FS.find_owner(key, mon and mon_key(mon)) then return nil end
        local ob = FS.new_owner({ cmd = "force_faint", key = key, quiet = true, arrival = self.arrivals })
        ob.phys = mon and mon_key(mon) or nil
        return FS.reference(ob)
    end
    function FS.requeue(ob, entry)
        ob.scheduled = true
        self.deferred[#self.deferred + 1] = entry
    end
    function FS.remove(ob)
        for i, o in ipairs(FS.owed) do
            if o == ob then
                table.remove(FS.owed, i)
                return
            end
        end
    end
    function FS.next_seq()
        FS.seq = FS.seq + 1
        return FS.seq
    end
    function FS.awaiting_any()
        for _, ob in ipairs(FS.owed) do
            if ob.gen == FS.gen and ob.state == "awaiting" and ob.kind == "explode" then return true end
        end
        return false
    end

    -- the command's arrival: ONE owner per death; a re-sent command never starts a second attempt
    function FS.receive(cmd, c_)
        local slot, mon, party, why = find_party_slot(cmd.key, c_)
        local phys = mon and mon_key(mon) or nil
        local ob = FS.find_owner(cmd.key, phys)
        if ob then
            if ob.state == "pending" and #ob.attempts == 0 and ob.cmd == "force_faint" and c_ == "force_explode" then
                ob.cmd = c_ -- an unattempted owner takes the stronger order
            end
            note_once(ob, "dup", "[SLink-gen2] " .. c_ .. " already owed (" .. ob.state .. "): " .. tostring(cmd.key))
            return
        end
        if why then log("[SLink-gen2] " .. c_ .. ": " .. why .. " " .. tostring(cmd.key) .. " (retained)") end
        ob = FS.new_owner({ cmd = c_, key = cmd.key, nickname = cmd.nickname, arrival = self.arrivals })
        ob.phys = phys
        local entry = FS.reference(ob)
        local battle = reads.read_battle()
        if party and slot and battle and battle.mode ~= 0 and p.battle_hold then
            self.pending_battle_writes[#self.pending_battle_writes + 1] = entry
            self.bench_owed = true
        else
            self.deferred[#self.deferred + 1] = entry
        end
    end

    function FS.complete(ob, mon)
        ob.state = "done"
        -- Selfdestruct copies HP0 before its later native faint. Retire the write obligation,
        -- but keep this one echo receipt for the same identity/epoch/battle visit.
        if ob.kind == "explode" and ob.suppress and FS.in_battle then
            local echoes = FS.explode_echoes
            if not echoes or echoes.gen ~= FS.gen or echoes.epoch ~= self.epoch or echoes.visit ~= FS.visit then
                echoes = {gen=FS.gen, epoch=self.epoch, visit=FS.visit, keys={}}
                FS.explode_echoes = echoes
            end
            echoes.keys[ob.suppress.phys] = ob.identity
        end
        ob.suppress = nil
        FS.remove(ob)
        mark_dead(ob.key, mon)
        if ob.quiet then
            log("[SLink-gen2] re-zeroed a revived dead mon " .. ob.key .. " -> " .. mon_key(mon))
        else
            hud.show("!! " .. nick_of(ob, mon) .. " KO'd", 255, 80, 80, 360)
        end
        -- dead stays dead (facts doc section 2): a native attempt installs the quiet re-zero a landed battle write does
        local in_battle = false
        for _, a in ipairs(ob.attempts) do in_battle = in_battle or a.kind ~= "overworld" end
        if in_battle and not ob.quiet then
            local e = FS.quiet_entry(ob.key, mon)
            if e then
                e.arrival = ob.arrival
                defer_held(e)
            end
        end
    end
    -- an attempt of partial or unknown effect: kept as evidence, never retried, never reported done or "not changed"
    function FS.quarantine(ob, state, kind, err)
        ob.state, ob.scheduled = state, false
        ob.evidence = { kind = kind, error = tostring(err), frame = self.frame }
        log("[SLink-gen2] ACTIVE FAINT " .. string.upper(state) .. " QUARANTINED (no retry, no completion claimed, server not told): "
            .. tostring(ob.key) .. " " .. tostring(kind) .. ": " .. tostring(err))
    end

    -- CPU callbacks run before the next hello poll. Epoch alone cannot bind a write to the accepted save.
    function FS.check_identity(ob)
        local ok, identity = pcall(hello_identity)
        local expected = FS.last_identity
        if ob then expected = ob.identity end -- nil is unaccepted, never adopt a later hello for this owner
        if not ok or identity == nil or expected == nil or identity ~= expected then
            for _, o in ipairs(FS.owed) do
                if o.gen == FS.gen and o.state ~= "stale" and (not ob or o == ob) then
                    o.was, o.suppress = o.state, nil
                    local previous_evidence = o.evidence
                    FS.quarantine(o, "stale", "identity", "save changed or unreadable at settlement seam")
                    o.identity_evidence = o.evidence
                    o.evidence = previous_evidence or o.identity_evidence -- retain the original partial-write receipt
                end
            end
            return false
        end
        -- A transient unreadable poll need not re-hello inside battle. Only the same previously accepted
        -- identity can resume; identity_changed clears last_identity until the new hello is ready.
        return not FS.suspended and FS.last_identity == expected
    end

    -- the overworld seam: borrow ONE reference without retiring its owner (docs section 2, central ownership)
    function FS.overworld(entry)
        local ob = entry.ob
        ob.scheduled = false
        if ob.gen ~= FS.gen or ob.state ~= "pending" then return end -- stale or already settled elsewhere
        if not FS.check_identity(ob) then
            if ob.state == "pending" then FS.requeue(ob, entry) end
            return
        end
        local slot, mon, _, why = find_party_slot(ob.key, ob.cmd)
        if not slot and not why and contest_masked() then
            note_once(ob, "contest", "[SLink-gen2] " .. ob.cmd .. " held until the contest returns the party " .. tostring(ob.key))
            return FS.requeue(ob, entry)
        end
        if not slot then
            note_once(ob, "absent", "[SLink-gen2] " .. ob.cmd .. " retained at the checkpoint: " .. (why or "key not in party")
                      .. " " .. tostring(ob.key))
            return FS.requeue(ob, entry)
        end
        ob.phys = mon_key(mon)
        -- already zero: this fresh, exact, keyed read IS the readback (never the legacy unchecked shortcut)
        if mon.hp == 0 and (ob.quiet or self.dead_keys[ob.key]) then return FS.complete(ob, mon) end
        local battle = reads.read_battle()
        local snapshot = battle and { mode = battle.mode, battle_type = battle.battle_type,
                                      active_slot = battle.active_slot, link_mode = wram_byte("wLinkMode") } or nil
        local mark = settle.attempt_mark()
        if not FS.check_identity(ob) then
            if ob.state == "pending" then FS.requeue(ob, entry) end
            return
        end
        local ok, err = pcall(function()
            writes:arm("overworld")
            writes:faint_party_slot(slot, snapshot)
            writes:disarm()
        end)
        writes:disarm()
        if not ok then
            if settle.attempted_since(mark) == false then -- a proved zero-write refusal: still owed
                note_once(ob, "refused", "[SLink-gen2] " .. ob.cmd .. " refused by the write gate (retained): " .. tostring(err)
                          .. " " .. tostring(ob.key))
                -- Keep the obligation pending without presenting a refusal as a player outcome.
                return FS.requeue(ob, entry)
            end
            return FS.quarantine(ob, "partial", "overworld", err)
        end
        ob.attempts[#ob.attempts + 1] = { kind = "overworld", seq = FS.next_seq(), epoch = self.epoch,
                                         identity = ob.identity, frame = self.frame }
        local s2, m2, _, w2 = find_party_slot(ob.key, ob.cmd)
        if s2 and not w2 and mon_key(m2) == ob.phys and m2.hp == 0 and (type(m2.status) ~= "number" or m2.status == 0) then
            return FS.complete(ob, m2)
        end
        FS.quarantine(ob, "partial", "overworld", "keyed readback did not show HP 0 after the write")
    end

    function FS.track(battle)
        local now = battle ~= nil and battle.mode ~= 0
        if now and not FS.in_battle then FS.visit = FS.visit + 1 end
        if battle then FS.in_battle = now end
        if battle and not now then FS.explode_echoes = nil end
    end

    -- witnessed survival of an Explosion attempt (a LATER turn's hold finds the same mon alive and active)
    function FS.survival(battle)
        for _, ob in ipairs(FS.owed) do
            local a = ob.attempts[#ob.attempts]
            if ob.gen == FS.gen and ob.state == "awaiting" and ob.kind == "explode" and a.epoch == self.epoch
               and a.visit == FS.visit and FS.hold_serial > a.serial and io.framecount() > a.frame then
                local slot, mon, _, why = find_party_slot(ob.key, ob.cmd)
                local bm = reads.read_battle_mon and reads.read_battle_mon("player")
                if not why and slot == battle.active_slot and slot == a.slot and mon and mon_key(mon) == ob.phys
                   and mon.hp > 0 and bm and bm.hp and bm.hp > 0 then
                    ob.state, ob.fallback = "pending", true
                    log("[SLink-gen2] Explosion attempt survived (the mon acted on a later turn): plain faint fallback "
                        .. tostring(ob.key))
                    self.pending_battle_writes[#self.pending_battle_writes + 1] = FS.reference(ob)
                end
            end
        end
    end
    -- one hold = one dispatch token; false while the save identity cannot be verified
    function FS.begin_hold(battle)
        if FS.suspended then return false end
        if not FS.check_identity() then return false end
        FS.track(battle)
        FS.hold_serial = FS.hold_serial + 1
        FS.survival(battle)
        return true
    end
    -- ONE operation per obligation per hold token: a second dispatch under the same token is refused here
    function FS.dispatch(ob, kind, fn)
        if FS.spent[ob.id] == FS.hold_serial then
            note_once(ob, "token" .. FS.hold_serial, "[SLink-gen2] " .. kind .. " refused: this hold's dispatch token is spent "
                      .. tostring(ob.key))
            return false, "dispatch token already spent"
        end
        FS.spent[ob.id] = FS.hold_serial
        return pcall(fn)
    end

    function FS.active(w, ob, slot, mon, battle, snapshot, target, keep)
        local species = target("wBattleMonSpecies")
        local same, matches = species == mon.species_id, p.battle_species_matches
        if matches then same = matches(mon, species) == true end -- the effective-species compare, as the legacy hold
        if not same then -- the battle struct is not this slot's: pending, never another slot
            ob.scheduled = true
            keep[#keep + 1] = w
            return
        end
        -- a copy per target (never the shared loop snapshot), keyed with the CURRENTLY resolved physical record
        local snap = {}
        for k, v in pairs(snapshot) do snap[k] = v end
        snap.player_action = target("wBattlePlayerAction")
        snap.key = mon_key(mon)
        ob.phys = snap.key
        local explode = ob.cmd == "force_explode" and not ob.explode_spent and snap.player_action == 0
        local kind = explode and "explode" or "plain"
        local attempt = { kind = kind, seq = FS.next_seq(), serial = FS.hold_serial, frame = io.framecount(),
                          visit = FS.visit, slot = slot, epoch = self.epoch, identity = ob.identity,
                          pre = { hp = mon.hp, native = settle.native and settle.native() } }
        -- echo suppression is bound to this physical identity/epoch BEFORE a possible native callback
        local previous = ob.suppress
        ob.suppress = { phys = snap.key, epoch = self.epoch, gen = FS.gen }
        local mark = settle.attempt_mark()
        if not FS.check_identity(ob) then
            ob.suppress = nil
            if ob.state == "pending" then ob.scheduled = true; keep[#keep + 1] = w end
            return
        end
        local ok, err = FS.dispatch(ob, kind, function()
            writes:arm("battle_hold")
            if explode then writes:explode_active_battler(slot, snap)
            else writes:faint_active_battler(slot, snap) end
        end)
        writes:disarm()
        if ok then
            ob.attempts[#ob.attempts + 1] = attempt
            ob.state, ob.kind, ob.scheduled = "awaiting", kind, false
            if kind == "explode" then ob.explode_spent = true end
            log("[SLink-gen2] active " .. kind .. " attempt staged (awaiting native consumption): " .. tostring(ob.key))
            return
        end
        if settle.attempted_since(mark) == false then
            ob.suppress = previous
            note_once(ob, "refused:" .. kind, "[SLink-gen2] active " .. kind .. " refused (retained): " .. tostring(err) .. " "
                      .. tostring(ob.key))
            ob.scheduled = true
            keep[#keep + 1] = w
            return
        end
        ob.attempts[#ob.attempts + 1] = attempt
        FS.quarantine(ob, "partial", kind, err)
    end

    function FS.battle_hold(battle, snapshot, target)
        local keep = {}
        for _, w in ipairs(self.pending_battle_writes) do
            local ob = w.ob
            ob.scheduled = false
            if ob.gen == FS.gen and ob.state == "pending" then
                local slot, mon, _, why = find_party_slot(ob.key, ob.cmd)
                if why then
                    note_once(ob, "ambiguous", "[SLink-gen2] battle write retained: " .. why .. " " .. tostring(ob.key))
                    ob.scheduled = true
                    keep[#keep + 1] = w
                elseif not slot then
                    ob.scheduled = true
                    defer_held(w) -- a contest-hidden or departed mon: the checkpoint decides, the owner stays
                elseif slot == battle.active_slot then
                    FS.active(w, ob, slot, mon, battle, snapshot, target, keep)
                else
                    -- a bench death: no in-battle writer is composed (immediate enforcement needs its own proof); it
                    -- waits for the checkpoint after the battle, retained, never consumed
                    note_once(ob, "bench", "[SLink-gen2] bench death retained for the checkpoint: " .. tostring(ob.key))
                    ob.scheduled = true
                    keep[#keep + 1] = w
                end
            end
        end
        self.pending_battle_writes = keep
    end

    -- evidence: a party-bearing tick that was actually SENT (a held pre-hello message is not evidence). A `safe` event
    -- carries no party and never reaches here. F1 itself writes both HP mirrors: only a bound AFTER-copy witness
    -- can settle it. No such producer is composed yet. Explosion writes no HP, so its later HP 0 rule is unchanged.
    function FS.after_tick(party, in_battle)
        if FS.suspended then return end
        local seq = FS.next_seq()
        local hp_of, dup = {}, {}
        for _, e in ipairs(party) do
            if hp_of[e.key] ~= nil then dup[e.key] = true end
            hp_of[e.key] = e.hp
        end
        local list = {}
        for _, ob in ipairs(FS.owed) do list[#list + 1] = ob end
        for _, ob in ipairs(list) do
            local a = ob.attempts[#ob.attempts]
            if ob.gen == FS.gen and ob.state == "awaiting" and ob.phys and not dup[ob.phys] and a.epoch == self.epoch
               and seq > a.seq and FS.check_identity(ob) then
                local hp = hp_of[ob.phys]
                if hp == 0 then
                    if a.kind == "explode" or (ob.obs_seq and seq > ob.obs_seq) then
                        local slot, mon, _, why = find_party_slot(ob.key, ob.cmd)
                        if slot and not why and mon_key(mon) == ob.phys and mon.hp == 0 then FS.complete(ob, mon) end
                    end
                elseif hp ~= nil and hp > 0 and a.kind == "explode" and not in_battle then
                    ob.state, ob.fallback = "pending", true
                    log("[SLink-gen2] Explosion attempt survived the battle: plain faint fallback " .. tostring(ob.key))
                    FS.requeue(ob, FS.reference(ob))
                end
            end
        end
    end
    -- The post-copy producer borrows only this read-only authority, never an owner or the FS clock.
    -- Select and bind synchronously at the CPU callback; drain must not refresh an old sample.
    function FS.capture_faint(b, held)
        if FS.suspended or type(held) ~= "table" or held.generation ~= self.epoch
           or type(b) ~= "table" or (b.mode ~= 1 and b.mode ~= 2)
           or b.hp ~= 0 or b.status ~= 0 or b.link_mode ~= 0 or b.fainted ~= true then return nil end
        local candidate
        for _, ob in ipairs(FS.owed) do
            local a = ob.attempts[#ob.attempts]
            if ob.gen == FS.gen and ob.state == "awaiting" and a and a.kind == "plain"
               and a.epoch == self.epoch and a.visit == FS.visit and a.slot == b.slot then
                if candidate then return nil end -- no ambiguous attempt selection
                candidate = ob
            end
        end
        if not candidate then return nil end
        local ob, a = candidate, candidate.attempts[#candidate.attempts]
        if not FS.check_identity(ob) or ob.identity ~= a.identity or ob.identity ~= FS.last_identity then return nil end
        local slot, mon, _, why = find_party_slot(ob.key, ob.cmd)
        if why or slot ~= a.slot or not mon or mon.is_egg ~= false or mon_key(mon) ~= ob.phys
           or mon.hp ~= 0 or mon.status ~= 0 then return nil end
        if not FS.check_identity(ob) or ob.gen ~= FS.gen or ob.state ~= "awaiting"
           or a.epoch ~= self.epoch or a.visit ~= FS.visit then return nil end
        local seq = FS.next_seq()
        if seq <= a.seq or seq <= (FS.evidence_floor or 0) then return nil end
        return {identity=ob.identity, generation=ob.gen, epoch=a.epoch, visit=a.visit,
                key=ob.phys, attempt_seq=a.seq, seq=seq}
    end
    -- Reserved consumer contract for the future, separately pinned AFTER-copy observer. The existing 0f:44c8
    -- pre-copy event has no capture binding and can never settle F1. A producer must allocate capture.seq from
    -- FS.next_seq AT CAPTURE (the same clock as attempt/tick), and freeze identity/gen/epoch/visit/key/attempt_seq
    -- there. Never manufacture these fields or a fresh sequence when an old batch is drained.
    function FS.observe(ev)
        if FS.suspended then return end
        local b, captured = ev.battle, ev.capture
        if type(b) ~= "table" or type(captured) ~= "table" then return end
        if ev.batch_generation ~= self.epoch then
            log("[SLink-gen2] stale battle_faint observation dropped (generation " .. tostring(ev.batch_generation)
                .. " vs " .. self.epoch .. ")")
            return
        end
        for _, ob in ipairs(FS.owed) do
            local a = ob.attempts[#ob.attempts]
            if ob.gen == FS.gen and ob.state == "awaiting" and a.kind == "plain" and a.epoch == self.epoch
               and a.visit == FS.visit and FS.check_identity(ob)
               and ev.phase == "after_party_copyback"
               and captured.identity == a.identity and captured.identity == ob.identity
               and captured.generation == ob.gen and captured.epoch == a.epoch
               and captured.visit == a.visit and captured.key == ob.phys and captured.attempt_seq == a.seq
               and type(captured.seq) == "number" and captured.seq % 1 == 0
               and captured.seq > a.seq and captured.seq <= FS.seq and captured.seq > (FS.evidence_floor or 0)
               and b.slot == a.slot and b.hp == 0 and b.status == 0 and b.link_mode == 0 and b.fainted == true then
                local native = settle.native and settle.native()
                local slot, mon, _, why = find_party_slot(ob.key, ob.cmd)
                if native and native.fainted == true and native.hp == 0 and native.status == 0
                   and slot == a.slot and not why and mon_key(mon) == ob.phys and mon.hp == 0 and mon.status == 0 then
                    ob.obs_seq = ob.obs_seq or captured.seq
                end
            end
        end
    end
    -- a commanded death's native faint echo: scoped to the exact physical identity, identity generation and epoch
    function FS.echo(key)
        local echoes = FS.explode_echoes
        if echoes and echoes.keys[key] then
            local battle = reads.read_battle()
            if echoes.gen == FS.gen and echoes.epoch == self.epoch and echoes.visit == FS.visit
               and echoes.keys[key] == FS.last_identity and battle and battle.mode ~= 0 and FS.check_identity() then
                echoes.keys[key] = nil
                log("[SLink-gen2] faint echo of a completed Explosion dropped " .. key)
                return true
            end
        end
        for _, ob in ipairs(FS.owed) do
            local s = ob.suppress
            if s and s.phys == key and s.gen == FS.gen and s.epoch == self.epoch then
                ob.suppress = nil
                log("[SLink-gen2] faint echo of a commanded death dropped " .. key)
                return true
            end
        end
        return false
    end
    function FS.suspend()
        FS.suspended, FS.resume_identity = true, FS.last_identity
        FS.evidence_floor = FS.next_seq()
        for _, ob in ipairs(FS.owed) do ob.obs_seq = nil end
    end
    -- once per frame: battle visit, identity-suspension release, one diagnostic for a witness that never came
    function FS.frame()
        FS.track(reads.read_battle())
        -- identity_unavailable suspends every attempt and every settlement; it ends when the SAME identity reads again
        -- (a different one arrives as identity_changed: invalidated, never resumed). In a battle no new hello can be
        -- sent (p.hello_unheld waits for the overworld), so waiting for one would suspend the whole battle.
        local identity = hello_identity()
        local accepted = self.hello_session:status()
        local renewed = identity ~= nil and accepted.ready and accepted.identity == identity
        if FS.suspended then
            if identity ~= nil and (identity == FS.resume_identity or (FS.resume_identity == nil and renewed)) then
                if FS.resume_identity == nil then FS.last_identity = accepted.identity end
                FS.evidence_floor = FS.next_seq() -- samples captured while identity was unavailable are stale too
                FS.suspended = false
            end
        elseif renewed then
            FS.last_identity = accepted.identity
        end
        for _, ob in ipairs(FS.owed) do
            local a = ob.attempts[#ob.attempts]
            if ob.gen == FS.gen and ob.state == "awaiting" and a and self.frame - a.frame > (settle.deadline_frames or 1800) then
                note_once(ob, "deadline", "[SLink-gen2] no native-consumption witness for the " .. a.kind .. " attempt after "
                          .. (self.frame - a.frame) .. " frames: still awaiting (never retried, never completed) "
                          .. tostring(ob.key))
            end
        end
    end

    -- boundaries
    function FS.boundary(kind)
        for _, ob in ipairs(FS.owed) do
            if ob.gen == FS.gen and ob.state == "awaiting" then
                FS.quarantine(ob, "lost", ob.kind, "the " .. kind .. " boundary ended the observation of a staged attempt")
            end
        end
    end
    function FS.abandon(why)
        for _, ob in ipairs(FS.owed) do
            if ob.gen == FS.gen then
                if ob.state == "awaiting" then
                    FS.quarantine(ob, "lost", ob.kind, why .. " abandoned the timeline of a staged attempt")
                elseif ob.state == "pending" then
                    defer_held(FS.reference(ob)) -- the legacy clear dropped the reference; the owner is still owed
                end
            end
        end
    end
    function FS.identity_changed()
        FS.suspended, FS.resume_identity, FS.last_identity = false, nil, nil
        FS.gen = FS.gen + 1
        self.epoch = self.epoch + 1 -- every stamp and batch of the old save now fails generation validation
        if self.signals then self.signals:abandon("identity_changed") end
        for _, ob in ipairs(FS.owed) do
            if ob.gen < FS.gen and ob.state ~= "stale" then
                ob.was, ob.state, ob.scheduled, ob.suppress = ob.state, "stale", false, nil
                note_once(ob, "stale", "[SLink-gen2] old-identity " .. tostring(ob.cmd) .. " obligation quarantined (was "
                          .. tostring(ob.was) .. "), not transferred to the new save: " .. tostring(ob.key))
            end
        end
        for _, queue in ipairs({ "deferred", "pending_battle_writes" }) do
            local kept = {}
            for _, e in ipairs(self[queue]) do
                if not e.ob then kept[#kept + 1] = e end
            end
            self[queue] = kept
        end
    end
    -- a conflicting box operation on a target whose death is still unsettled: retained, never replied to
    function FS.guard_box(cmd, phys)
        if cmd.cmd ~= "box_mon" and cmd.cmd ~= "memorialize" and cmd.cmd ~= "party_mon" then return false end
        local ob = FS.find_owner(cmd.key, phys)
        if not ob then return false end
        note_once(ob, "box:" .. cmd.cmd, "[SLink-gen2] " .. cmd.cmd .. " retained: the death of " .. tostring(cmd.key)
                  .. " is unsettled (" .. ob.state .. ")")
        self.deferred[#self.deferred + 1] = cmd
        return true
    end
    end -- if FS

    -- Deferred queue: one command per frame, only at the verified checkpoint, inside the permit.
    function self:run_deferred()
        if dev_trade and dev_trade.busy() then return end
        local armed
        for i, s in ipairs(self.settle) do if s.armed then armed = armed or i end end
        if (#self.deferred == 0 and not armed and not (self.dead_synced and next(self.dead_keys))) or not self.writes_enabled
           or not net.connected() then return end
        local safe = safety.check(PARTY_HP)
        if not safe then return end
        if settle and FS.suspended then return end -- the save identity cannot be verified: no attempt, no settlement
        if armed then
            -- one op per hold: the saved party now holds the mon, so its backing copy goes (full-record
            -- match); a reset before the save left it in the box only, and settle is a no-op
            local s = table.remove(self.settle, armed)
            if s.memorial then return self:settle_memorial(s) end
            local done, why = boxes.settle(s.key)
            log("[SLink-gen2] box copy settle " .. s.key .. ": " .. (done and "done" or tostring(why)))
            return
        end
        local revived = revived_dead()
        if revived and settle then
            local e = FS.quiet_entry(revived)
            if e then table.insert(self.deferred, 1, e) end
        elseif revived then
            table.insert(self.deferred, 1, { cmd = "force_faint", key = revived, quiet = true })
        end
        if #self.deferred == 0 then return end
        local cmd = table.remove(self.deferred, 1)
        if cmd.ob then return FS.overworld(cmd) end
        if BOX_NACK[cmd.cmd] then return self:run_box(cmd) end
        local slot, mon, _, why = find_party_slot(cmd.key, cmd.cmd)
        if not slot and not why and contest_masked() then
            -- ruling (a): kill on return; the hidden mon is back at the first checkpoint after
            -- ContestReturnMons (tail requeue, bounded by the contest itself)
            if not cmd.contest_logged then
                cmd.contest_logged = true
                log("[SLink-gen2] " .. cmd.cmd .. " held until the contest returns the party " .. tostring(cmd.key))
            end
            self.deferred[#self.deferred + 1] = cmd
            return
        end
        if not slot then
            -- the mon left the party before the checkpoint (PC deposit) or its key is ambiguous
            log("[SLink-gen2] " .. cmd.cmd .. " dropped at the checkpoint: " .. (why or "key not in party")
                .. " " .. tostring(cmd.key))
            return
        end
        -- the re-zero behind a landed battle write (O-30), or a death this client already landed (MINOR-7,
        -- the server's O-24 re-issue): a mon still at HP 0 needs nothing more
        local quiet = cmd.quiet or self.dead_keys[cmd.key]
        if quiet and mon.hp == 0 then return end
        local battle = reads.read_battle()
        -- Gen 2: faint_party_slot takes the battle snapshot and refuses the active slot and
        -- any linked/special battle itself; the client never pre-empts that decision.
        local snapshot = battle and { mode = battle.mode, battle_type = battle.battle_type,
                                      active_slot = battle.active_slot, link_mode = wram_byte("wLinkMode") } or nil
        local ok, err = pcall(function()
            writes:arm("overworld")
            writes:faint_party_slot(slot, snapshot)
            writes:disarm()
        end)
        writes:disarm()
        if ok then mark_dead(cmd.key, mon) end
        if ok and quiet then
            -- EvolveAfterBattle's max-HP gain or the Battle Tower's reload revived it: dead stays dead
            log("[SLink-gen2] re-zeroed a revived dead mon " .. cmd.key .. " -> " .. mon_key(mon))
        elseif ok then
            hud.show("!! " .. nick_label(cmd.key, cmd.nickname or (mon and mon.nickname)) .. " KO'd", 255, 80, 80, 360)
        else
            -- no force_faint NACK exists (protocol §5): the refusal is logged and shown, never
            -- reported as a KO
            log("[SLink-gen2] " .. cmd.cmd .. " refused by the write gate: " .. tostring(err) .. " " .. cmd.key)
            hud.show("X KO refused: " .. nick_label(cmd.key, mon and mon.nickname), 255, 80, 80, 300)
        end
    end

    local function burial_waiting()
        for _, s in ipairs(self.settle) do if s.memorial then return true end end
        return false
    end
    local function show_burial() hud.show(BURIAL_TEXT, 255, 200, 64, 300) end
    -- BURIAL-VISIBLE: true while a burial waits, false once when it clears, absent otherwise (an idle tick
    -- stays byte-identical to master's); the server also resets it at every accepted hello
    local function awaiting_save_field()
        local waiting, flag = burial_waiting(), nil
        if waiting then flag = true elseif self.burial_reported then flag = false end
        self.burial_reported = waiting
        return flag
    end

    function self:memorial_done(key, phys, name)
        self.pending_rescan, self.retired_alias[key] = true, nil
        self.dead_keys[key], self.dead_keys[phys] = nil, nil -- buried: MINOR-7 is done with it
        send("memorialize_done", { key = key, box = boxes.memorial_box })
        hud.show("† " .. name .. " buried", 255, 140, 40, 300)
    end
    -- BOX-MEMORIAL-2: after a native save witnessed the box edits, finish a boxed memorial; ack only once durable
    function self:settle_memorial(s)
        local done, why = boxes.settle_memorial(s.key)
        if done and why then
            s.armed = false
            self.settle[#self.settle + 1] = s
            log("[SLink-gen2] memorial settle " .. s.key .. ": " .. why)
        elseif done then
            self:memorial_done(s.memorial.key, s.key, nick_label(s.memorial.key, s.memorial.nickname))
        else
            log("[SLink-gen2] memorial settle refused: " .. tostring(why) .. " " .. s.memorial.key)
            send("memorialize_failed", { key = s.memorial.key, reason = tostring(why) })
        end
    end

    -- One box command inside the held checkpoint (lua/gen1/client.lua run_deferred is the reference):
    -- stats_cache before a deposit; replies keep cmd.key (what the server tracks) while the executor
    -- looks the mon up by the key the cartridge holds (a rejected key change's alias). Tail requeues
    -- (A5): a rebuild's party_mon refused "party full" waits behind the memorials that free a slot,
    -- bounded by the queue length at its first refusal; a memorial of the last party mon waits for a
    -- party_mon, or is dropped after game_over.
    function self:run_box(cmd)
        local phys = self.retired_alias[cmd.key] or cmd.key
        -- O-30: a memorial follows the dead mon through an evolution like its force_faint did
        local _, mon, party, party_why = find_party_slot(cmd.key, cmd.cmd == "memorialize" and cmd.cmd or nil)
        if mon and cmd.cmd == "memorialize" then phys = mon_key(mon) end
        local name = nick_label(cmd.key, cmd.nickname or (mon and mon.nickname))
        if settle and FS.guard_box(cmd, phys) then return end -- F2: an unsettled death owns the target
        -- INVARIANT (gen2-box-durability, BOX-MEMORIAL-2): a settle already waiting for a key owns it until the next
        -- native save. The server re-sends a command until it is acked, and a duplicate that reached the box executor
        -- would act on the half-done state: a memorial would report done (memorial copy made, volatile active source
        -- gone) and ack before the save made it durable; a withdraw would delete its DURABLE backing copy before the
        -- save persisted the party (a reset then loses the mon). Matched by the resolved physical key AND, for a
        -- memorial, by the server's own key (the alias that resolved `phys` can be reset while the settle waits).
        local function waiting(memorial)
            for i, s in ipairs(self.settle) do
                if memorial and s.memorial and (s.key == phys or s.memorial.key == cmd.key) then return s end
                if not memorial and not s.memorial and s.key == phys then return s, i end
            end
        end
        local function note_duplicate(s)
            if not s.duplicate_logged then
                s.duplicate_logged = true
                log("[SLink-gen2] " .. cmd.cmd .. " " .. cmd.key .. ": a settle for this key is already waiting for the save")
            end
        end
        if cmd.cmd == "box_mon" then
            if mon then send("stats_cache", { key = cmd.key, stats = { level = mon.level, maxHP = mon.max_hp } }) end
            local done, why = boxes.deposit(phys)
            if done then
                -- F2: the active sBox half persists only at SaveBox (a native save or box change); a reset
                -- before it reverts the deposit, which the reconnect reconcile heals. Recorded, not blocking.
                self.box_save_pending = true
                self.pending_rescan = true
                hud.show("↓ " .. name .. " boxed", 100, 180, 255, 200)
            else
                log("[SLink-gen2] box_mon refused: " .. tostring(why) .. " " .. cmd.key)
                send("box_mon_failed", { key = cmd.key, reason = tostring(why) })
                hud.show("X Box fail: " .. name, 255, 80, 80, 240)
            end
        elseif cmd.cmd == "party_mon" then
            local pending, pending_index = waiting(false)
            if pending then
                if mon then
                    note_duplicate(pending)
                    send("sync_retrieve_done", { key = cmd.key })
                    return
                end
                if not party or party_why then
                    local why = party_why or "party unavailable"
                    log("[SLink-gen2] party_mon refused: " .. why .. " " .. cmd.key)
                    send("sync_retrieve_failed", { key = cmd.key, reason = why })
                    return
                end
                table.remove(self.settle, pending_index)
                log("[SLink-gen2] party_mon " .. cmd.key .. ": party copy missing, retrying withdrawal")
            end
            local done, why = boxes.withdraw(phys, { defer_backing = true })
            if done and why then
                -- F1: the backing copy stays until a native SAVE persists the party (never a loss)
                self.settle[#self.settle + 1] = { key = phys, armed = false }
                log("[SLink-gen2] party_mon " .. cmd.key .. ": " .. why)
            elseif done then
                self.box_save_pending = true
            end
            if done then
                self.pending_rescan = true
                send("sync_retrieve_done", { key = cmd.key })
                hud.show("↑ " .. name .. " unboxed", 100, 255, 160, 200)
            elseif why == "party full" and (cmd.full_retries or 0) < (cmd.full_budget or (#self.deferred + 1)) then
                cmd.full_budget = cmd.full_budget or (#self.deferred + 1)
                cmd.full_retries = (cmd.full_retries or 0) + 1
                self.deferred[#self.deferred + 1] = cmd
                log("[SLink-gen2] party_mon " .. cmd.key .. ": party full, retry " .. cmd.full_retries .. "/"
                    .. cmd.full_budget .. " after the queue")
            else
                log("[SLink-gen2] party_mon refused: " .. tostring(why) .. " " .. cmd.key)
                send("sync_retrieve_failed", { key = cmd.key, reason = tostring(why) })
            end
        else
            -- a repeated memorialize must NOT reach the box executor while its burial waits: with the memorial copy made
            -- and the volatile active-box source already removed it reports a plain done, and the client would ack
            -- memorialize_done before the native save made the removal durable (sweep ffd54b44, gen2_pc_ops)
            local pending = waiting(true)
            if pending then return note_duplicate(pending) end
            local done, why = boxes.memorialize(phys)
            if done and why then
                -- a boxed memorial that touched the volatile active sBox is not durable until
                -- a native save; memorialize_done waits for the settle after the save witness
                self.pending_rescan = true
                log("[SLink-gen2] memorialize " .. cmd.key .. ": " .. why)
                self.settle[#self.settle + 1] = { key = phys, armed = false,
                                                  memorial = { key = cmd.key, nickname = cmd.nickname } }
                show_burial()
            elseif done then
                self:memorial_done(cmd.key, phys, name)
            elseif why == "last party mon" and self.game_over then
                log("[SLink-gen2] memorialize dropped: last mon after game over " .. cmd.key)
            elseif why == "last party mon" then
                self.deferred[#self.deferred + 1] = cmd
            else
                -- INV-CLIENT-2: a key no longer in the party is forgotten (a later mon under it is another
                -- mon); one still there (memorial box full) stays dead
                if not mon then self.dead_keys[cmd.key], self.dead_keys[phys] = nil, nil end
                log("[SLink-gen2] memorialize refused: " .. tostring(why) .. " " .. cmd.key)
                send("memorialize_failed", { key = cmd.key, reason = tostring(why) })
                hud.show("X Mem fail: " .. name, 255, 80, 80, 300)
            end
        end
    end

    -- ── signals → events ─────────────────────────────────────────────────────────────
    local function announce_whiteout()
        self:request_sfx_local(22)
        send("whiteout", {})
    end

    -- Gen 2: a capture arrives complete from the binder (acquisition latch + final name),
    -- so there is no pending acquisition to settle as in Gen 1's settle_pending_change.
    local function publish_capture(ev)
        local m = ev.mon
        local key = not m.is_egg and mon_key(m) or nil
        if key == nil or key ~= m.key then
            log("[SLink-gen2] capture refused: " .. (m.is_egg and "unhatched egg"
                or "key disagreement " .. tostring(key) .. " vs " .. tostring(m.key)))
            if self.battle then self.battle.capture_refused = true end
            return
        end
        -- Gen 2: the hatch is the gift (O-15, gift_daycare) and so is a qualified direct givepoke
        -- (card U1G: area from the gifts.json row); roamer (legend_<species>, O-17) and contest
        -- (national_park_contest, O-18) ids come from the binder unchanged
        local gift = ev.acquisition == "egg_hatch" or ev.acquisition == "gift"
        local in_box = ev.destination == "box"
        send("capture", { key = key, area_id = ev.area_id, species_id = m.species_id, level = m.level,
                          hp = m.hp, maxHP = m.max_hp, nickname = m.nickname, held_item_id = m.held_item,
                          gift = gift, in_box = in_box, stats = { level = m.level, maxHP = m.max_hp } })
        if self.battle and not gift then self.battle.captured = true end
        self.resolved_areas[ev.area_id] = true
        if in_box then self.pending_rescan = true end
    end

    function self:on_observation(ev)
        local k = ev.site_id
        if k == "wild_ready" then
            local area_id, name = area_of()
            local foe = reads.read_battle_mon("enemy")
            local battle = reads.read_battle()
            local scripted = wram_byte("wBattleScriptFlags")
            self.battle = { wild = true, area_id = area_id, species = foe and foe.species_id,
                            level = foe and foe.level, captured = false,
                            -- Gen 2 (N3-3): the binder's count of acquisitions it refused after
                            -- the engine inserted a mon, stamped in engine order on each observation
                            refused_base = ev.refused_acquisitions,
                            -- scripted/static and special types resolve nothing (as the binder)
                            resolves = battle ~= nil and area_battle_types[battle.battle_type] == true
                                       and scripted ~= nil and math.floor(scripted / 128) % 2 == 0 }
            self.bench_owed = true -- O-32: a death deferred before the battle lands at its first frame
            if self.battle.resolves and self.has_pokeballs and self.seeded and area_id ~= ""
               and not self.resolved_areas[area_id] then
                hud.show("** NEW ENCOUNTER **\n" .. name, 255, 220, 60, 360)  -- area on its own line, as Gen 1
                self:request_sfx_local(25)
            end
        elseif k == "trainer_ready" then
            -- trainer_battle_start is announced by rival_tick (frame end), not here: the U1 receipts
            -- never proved this site, so production has no trainer_ready observation
            self.battle = { wild = false }
            self.bench_owed = true -- O-32: as wild_ready
        elseif k == "battle_end" then
            local b = self.battle
            if b and b.wild and b.resolves and not b.captured and b.area_id ~= ""
               and not self.resolved_areas[b.area_id] then
                if b.capture_refused or ev.refused_acquisitions ~= b.refused_base then
                    log("[SLink-gen2] a capture was refused in this battle: no_catch withheld, "
                        .. b.area_id .. " stays open")
                    hud.show("CATCH NOT REPORTED - SEE LOG", 255, 160, 64, 600)
                elseif not self.has_pokeballs then
                    log("[SLink-gen2] no Poke Balls yet: no_catch withheld, " .. b.area_id .. " stays open")
                else
                    send("no_catch", { area_id = b.area_id, species_id = b.species, level = b.level })
                    self.resolved_areas[b.area_id] = true
                end
            end
            -- Gen 1 parity: a battle write that never reached a battle hold (the battle ended first,
            -- a link battle) is still owed: the checkpoint zeroes it
            for _, w in ipairs(self.pending_battle_writes) do defer_held(w) end
            -- an echo that never came (the binder refused the observation) must not eat a later faint
            self.pending_battle_writes, self.commanded = {}, {}
            self.battle = nil
            self.pending_safe = true
        elseif k == "bag_ball_received" then
            local had = self.has_pokeballs
            self.has_pokeballs = true
            if not had then announce_nuzlocke_start() end
        elseif k == "save_completed" then
            if io.saveram then pcall(io.saveram) end
            -- the native save persisted the party: deferred backing removals may run (gen2-box-durability)
            for _, s in ipairs(self.settle) do s.armed = true end
            if self.box_save_pending then log("[SLink-gen2] box edits persisted by the native save") end
            self.box_save_pending = false
        elseif k == "battle_faint" and settle then
            FS.observe(ev)
        elseif k == "soft_reset" or k == "new_game" then
            self:boundary("reset", "save_reset")
        elseif k == "continue_confirmed" then
            self:boundary("reload", "save_reload")
        end
    end

    function self:on_event(ev)
        local k = ev.kind
        if dev_trade and dev_trade.busy() and (k == "capture" or k == "key_change"
           or k == "party_to_box" or k == "box_to_party") then return end
        local m = ev.mon
        if m and m.is_egg and k ~= "capture" then
            -- PC ops may move an egg (allow_egg); an egg never goes on the wire (O-15)
            self.pending_rescan = true
            return
        end
        if k == "capture" then publish_capture(ev)
        elseif k == "whiteout" then announce_whiteout()
        elseif k == "faint" then
            if settle and FS.echo(m.key) then return end
            if self.commanded[m.key] then
                -- O-30: HandlePlayerMonFaint after our own battle write; the server commanded this death
                self.commanded[m.key] = nil
                log("[SLink-gen2] faint echo of a commanded death dropped " .. m.key)
                return
            end
            self.faint_latches[#self.faint_latches + 1] = { key = m.key, cause = ev.cause, frame = self.frame,
                                                            area_id = (area_of()) }
        elseif k == "party_to_box" then
            send("party_to_box", { key = mon_key(m), stats = { level = m.level } })
            self.pending_rescan = true
        elseif k == "box_to_party" then
            send("box_to_party", { key = mon_key(m), area_id = (area_of()) })
            self.pending_rescan = true
        elseif k == "pc_release" then
            -- owner ruling O-35 (server 8f662994): a PC release loses the mon, from the party or a box; the
            -- server kills a linked partner and ignores an unlinked key. Mirrored by lua/gen1/client.lua.
            log(string.format("[SLink-gen2] RELEASE_SEEN key=%s %s", mon_key(m),
                              ev.collection == "party" and "party" or "box=" .. tostring(ev.box_index)))
            self.dead_keys[mon_key(m)] = nil -- the mon is gone: a later mon under this key is another mon
            owe("release", { key = mon_key(m) }) -- INV-CLIENT-2: durable, like a trade report
            self.pending_rescan = true
        elseif k == "key_change" then
            local new_key = mon_key(m)
            self.key_alias = { old_key = ev.old_key, new_key = new_key,
                               msg = { old_key = ev.old_key, new_key = new_key, new_species = m.species_id,
                                       reason = ev.reason, new_nickname = m.nickname } }
            send("key_change", self.key_alias.msg)
            self.pending_rescan = true
        elseif k == "box_change" then
            self.pending_rescan = true
        elseif k == "observation" then
            self:on_observation(ev)
        end
    end

    -- A faint latch settles once the one record carrying its key reads HP 0 (the copy-back or
    -- the poison store has landed); a latch older than MAX_PENDING_FRAMES is dropped, logged.
    function self:settle_faints()
        if #self.faint_latches == 0 then return end
        local keep = {}
        for _, f in ipairs(self.faint_latches) do
            local _, mon = find_party_slot(f.key)
            if mon and mon.hp == 0 then
                send("faint", { key = f.key, area_id = f.area_id })
            elseif self.frame - f.frame > Client.MAX_PENDING_FRAMES then
                log("[SLink-gen2] " .. tostring(f.cause) .. " faint latch expired unsettled " .. tostring(f.key))
            else
                keep[#keep + 1] = f
            end
        end
        self.faint_latches = keep
    end

    -- ── hello / tick ─────────────────────────────────────────────────────────────────
    function self:send_hello(expected_identity)
        if expected_identity == nil then
            local sent, why = self.hello_session:send_now(io.framecount())
            if sent then self.hello_sent = true end
            return sent, why
        end
        if hello_identity() ~= expected_identity then return false, "hello identity changed or unavailable" end
        local party, battle = snapshot_party()
        if not party then return false, "hello party snapshot unavailable" end
        local area_id, loc = area_of()
        local had_balls = self.has_pokeballs
        self.has_pokeballs = self.has_pokeballs or ball_count() > 0
        if not had_balls and self.has_pokeballs then
            self.nuzlocke_announced = true
            log("[SLink-gen2] nuzlocke ACTIVE (pokeballs already in bag at startup)")
        end
        self:rescan_boxes()
        local player = reads.read_player()
        local badges = reads.read_badges()
        local payload = {
            rom_type = self.rom_type, foundation = self.foundation, artifact_kind = self.artifact_kind,
            instrumentation_key = SLINK_INSTRUMENTATION_KEY or os.getenv("SLINK_INSTRUMENTATION_KEY"),
            party = party, ot_id = player.ot_id, trainer_name = player.player_name,
            has_pokeballs = self.has_pokeballs, ball_count = ball_count(),
            -- Gen 2: two badge bytes; Kanto rides the protocol's second-region field
            badges = badges and badges.johto, kanto_badges = badges and badges.kanto,
            area_id = area_id, loc_name = loc, pc_boxes = pc_boxes_wire(), pc_boxes_generation = box_generation(),
            writes_enabled = self.writes_enabled, rom_sha1 = self.rom_sha1,
            in_battle = battle.mode ~= 0,
            -- Gen 2: no rom_content (gen2_gsc.rom_content_fingerprint refuses: not qualified)
            -- P4.1f panel / P4.2b sound: per CARTRIDGE, only a live SLink build with the cap bit.
            panel = panel and panel:present() or false, panel_abi = panel and panel:abi() or 0,
            sfx = panel and panel:sfx_present() or false,
            -- companion evidence for the server (GameRulesAdapter.companion_refusal): the live service's own
            -- ABI byte, absent on a cartridge with no live SLNK service
            companion_abi = panel and panel:companion_abi() or nil,
            -- MAJOR-1 (review e9d5e136): this client answers apply_prepare before any APPLY
            trade_prepare = self:trade_live(),
        }
        if hello_identity() ~= expected_identity then return false, "hello identity changed during snapshot" end
        return send("hello", payload)
    end

    local HELLO_UNHELD_POLLS, hello_sig, hello_polls = 8, nil, 0 -- p.hello_unheld readiness (see ready)
    self.hello_session = HelloSession.new({
        connected = function() return net.connected() end,
        identity = hello_identity,
        ready = function(identity)
            local live, why = game_is_live()
            if not live then return false, why end
            local battle = reads.read_battle()
            if not battle then return false, "battle state unavailable" end
            -- p.hello_unheld (Polished dev composition only): no checkpoint exists to hold, so a live game suffices.
            if p.hello_unheld then
                -- (F-4) no checkpoint to hold, so wait for what a hold would have proved: out of battle AND the same
                -- identity + party signature on HELLO_UNHELD_POLLS consecutive polls (one per frame; ready is asked
                -- every frame until the hello is queued), so a mid-script/transition frame never sends the first hello.
                local sig = {identity}
                for _, m in ipairs(reads.read_party().mons) do sig[#sig + 1] = tostring(mon_key(m)) end
                sig = table.concat(sig, ";")
                -- docs/polished/HELLO_GATE.md: the overworld loop must be RUNNING (not the Continue screen at frame ~286)
                local gate = reads.read_overworld_gate and reads.read_overworld_gate()
                local gate_ok = gate ~= nil and gate.running == true
                if battle.mode ~= 0 or not gate_ok or sig ~= hello_sig then hello_polls = 0 end
                hello_sig = sig
                if battle.mode ~= 0 then return false, "waiting for the battle to end" end
                if not gate_ok then return false, "waiting for the overworld to run" end
                hello_polls = hello_polls + 1
                if hello_polls < HELLO_UNHELD_POLLS then return false, "waiting for a stable party" end
                return hello_identity() == identity, "identity changed while checking readiness"
            end
            -- PLAN §5.4: the first hello waits for the OWPlayerInput checkpoint or a running battle
            if battle.mode == 0 and not (self.checkpoint_held or safety.check(PARTY_HP)) then
                return false, "waiting for Gen 2 checkpoint or battle"
            end
            return hello_identity() == identity, "identity changed while checking readiness"
        end,
        send = function(identity) return self:send_hello(identity) end,
        retry_delay = function() return 1 end, -- Gen 1's next-frame retry policy
        clock_rewind = "keep", callback_error = "raise", -- Gen 1 parity
        on_invalidate = function(reason)
            self.hello_sent = false
            if dev_trade then dev_trade.forget("hello " .. tostring(reason)) end
            -- Gen 2: another save's identity; what was held belongs to the previous one. A transient
            -- identity_unavailable keeps the queue (the same identity returning is not a change).
            if settle and reason == "identity_unavailable" and not FS.suspended then
                FS.suspend()
            end
            if reason == "identity_changed" then
                if settle then FS.identity_changed() end
                self.key_alias, self.retired_alias, self.dead_keys, self.dead_synced = nil, {}, {}, false
                drop_held("identity change")
            end
        end,
        on_error = function(stage, why) log("[SLink-gen2] hello " .. stage .. ": " .. tostring(why)) end,
    })

    self.replies = ReplyDispatch.new({
        budget = math.huge, -- Gen 1 parity: drain every queued line each frame
        receive = function()
            local line = net.receive()
            if line ~= nil then self.owed:line_received() end
            return line
        end,
        decode = function(line) return json.decode(line) end,
        validate = function(reply)
            if type(reply) == "table" and type(reply.commands) == "table" then
                self.owed:answer(reply.commands) -- retires the owed report on that line, unless refused
                return reply.commands
            end
            return nil, "unreadable reply line"
        end,
        handle = function(cmd) return self:handle_command(cmd) end,
        on_error = function(stage, why, subject)
            if stage == "handle" then
                log("[SLink-gen2] command " .. tostring(type(subject) == "table" and subject.cmd or nil) .. ": " .. why)
            else
                log("[SLink-gen2] unreadable reply line")
            end
        end,
    })

    function self:send_tick(event)
        local party, battle = snapshot_party()
        if not party then return end
        local area_id, loc = area_of()
        local in_battle = battle.mode ~= 0
        -- Gen 2: no area-entry NEW ENCOUNTER banner: it needs rom_content's wild map set,
        -- which Gen 2 does not send (see send_hello); the battle-start banner stays
        if self.last_area ~= nil and self.last_area ~= area_id then
            send("area_enter", { area_id = area_id, loc_name = loc })
        end
        self.last_area = area_id
        local had_balls = self.has_pokeballs
        self.has_pokeballs = self.has_pokeballs or ball_count() > 0
        if not had_balls and self.has_pokeballs then announce_nuzlocke_start() end
        local player = reads.read_player()
        local badges = reads.read_badges()
        local sent = send(event or "tick", {
            party = party, has_pokeballs = self.has_pokeballs, ball_count = ball_count(),
            area_id = area_id, loc_name = loc, in_battle = in_battle,
            -- the packed (class, instance) id, as trainer_battle_start (gen2_gsc.trainer_info decodes it)
            trainer_id = battle.trainer and trainer_id_of(battle.trainer) or nil,
            is_trainer_battle = battle.mode == c.TRAINER_BATTLE,
            enemy_party = enemy_party(battle),
            badges = badges and badges.johto, kanto_badges = badges and badges.kanto,
            trainer_name = player and player.player_name, pc_boxes = pc_boxes_wire(), pc_boxes_generation = box_generation(),
            -- the contest-masked party refuses any trade (9805ac1c): the server offers none meanwhile
            trade_blocked = contest_masked(),
            awaiting_save = awaiting_save_field(), -- BURIAL-VISIBLE: the pair board's "awaiting save"
        })
        if settle and sent then FS.after_tick(party, in_battle) end -- F2: only a SENT party-bearing tick is HP evidence
        resend_refused_change()
    end

    -- ── per-frame driver ─────────────────────────────────────────────────────────────
    -- Gen 2: the binder is built with the client's epoch authority and has no synchronous
    -- in-hook handlers (no qualified in-battle write site): every signal is consumed here.
    local authority = {
        kind = "MODEL_PROBE", allow_model_registration = true,
        capture = function() return { generation = self.epoch, operation = "epoch-" .. self.epoch } end,
        valid = function(stamp) return type(stamp) == "table" and stamp.generation == self.epoch end,
    }
    if settle then
        authority.capture_faint = function(battle, held) return FS.capture_faint(battle, held) end
    end

    function self:start()
        if self.signals then
            assert(self.signals:close(), "previous engine signals could not be released")
            self.signals = nil
        end
        local binder, why = signals_factory(authority)
        if not binder then error("Gen 2 engine signals refused: " .. tostring(why), 0) end
        self.signals = binder
        self.signal_failure_logged = nil
        if p.checkpoint_pc and not self.checkpoint_hook then
            self.checkpoint_hook = io.on_bus_exec(function()
                local ok, err = pcall(self.at_checkpoint, self)
                if not ok then log("[SLink-gen2] checkpoint hold: " .. tostring(err)) end
            end, p.checkpoint_pc,
                                                  "SLink-gen2-checkpoint", "System Bus")
        end
        if p.battle_hold and not self.battle_hook then
            self.battle_hook = io.on_bus_exec(function()
                -- p.battle_hold_entry (optional): the composed writer learns it is running INSIDE this exec callback,
                -- i.e. with the CPU at execution_before.pc, and refuses a battle write made anywhere else
                local entry = p.battle_hold_entry
                if entry then entry.enter("explode") end
                local ok, err = pcall(self.at_battle_hold, self)
                if entry then entry.leave() end
                if not ok then log("[SLink-gen2] battle hold: " .. tostring(err)) end
            end, p.battle_hold.execution_before.pc, "SLink-gen2-battle-hold", "System Bus")
        end
        if trade and not self.trade_hooks then
            -- P4.3b: SlinkTradePromptEntry / SlinkTradeApplyPickup run BEFORE the cartridge writes
            -- the ACK; from pickup the frame is the cartridge's and clobber checks stop (Lease:picked_up).
            -- SlinkTradeCommit latches the native commit entry (trade_overlay commit_entered).
            self.trade_hooks = {}
            for label, site in pairs(trade.hooks) do
                self.trade_hooks[#self.trade_hooks + 1] = io.on_bus_exec(function()
                    if io.read_u8(profile.ram.hROMBank, "System Bus") ~= site.bank then return end
                    local ok, err = pcall(site.on, trade)
                    if not ok then log("[SLink-gen2] trade pickup: " .. tostring(err)) end
                end, site.addr, "SLink-gen2-trade-" .. label, "System Bus")
            end
        end
    end

    -- O-30, mirroring lua/gen1/client.lua on_battle_loop_head: inside the synchronous CPU hold before
    -- `call DetermineMoveOrder` (the player committed this turn), land every owed death. Gen 2 differs
    -- in the site (Gen 2 has no HP test at its loop head) and in W-2's action suppression (USEITEM, not
    -- CANNOT_MOVE; lua/gen2/writes.lua). Link battles never write (the other Game Boy would desync).
    local transformed_bit = p.battle_hold and p.battle_hold.transformed_bit
    -- O-30 review MAJOR-1/2 (Gen 1 on_battle_loop_head lifts the same way): a death deferred before the
    -- battle (a script window refused the checkpoint) and a quiet re-zero whose mon was revived with no
    -- checkpoint between battles (Battle Tower: HealParty/LoadPokemonData around every StartBattle,
    -- battle_tower.asm:228-235, inside one Script_BattleRoomLoop) both land at this hold, not at battle end.
    local function lift_deferred_deaths()
        local stay = {}
        for _, e in ipairs(self.deferred) do
            local slot, mon
            if e.cmd == "force_faint" or e.cmd == "force_explode" then slot, mon = find_party_slot(e.key, e.cmd) end
            if slot and not (e.quiet and mon.hp == 0) then
                self.pending_battle_writes[#self.pending_battle_writes + 1] = e
            else
                stay[#stay + 1] = e
            end
        end
        self.deferred = stay
        -- MINOR-7: a dead key revived since its death landed (the tower's HealParty before battle 1) dies here too
        for key in pairs(self.dead_synced and self.dead_keys or {}) do
            local queued = false
            for _, w in ipairs(self.pending_battle_writes) do queued = queued or w.key == key end
            local slot, mon = find_party_slot(key)
            if slot and mon.hp > 0 and not queued then
                if settle then
                    local e = FS.quiet_entry(key, mon) -- nil while an open owner already names the key
                    if e then self.pending_battle_writes[#self.pending_battle_writes + 1] = e end
                else
                    self.pending_battle_writes[#self.pending_battle_writes + 1] = { cmd = "force_faint", key = key, quiet = true,
                                                                                   arrival = self.arrivals }
                end
            end
        end
    end
    function self:at_battle_hold()
        if (#self.pending_battle_writes == 0 and #self.deferred == 0 and not (self.dead_synced and next(self.dead_keys))
            and not (settle and FS.awaiting_any())) or not self.writes_enabled then return end
        -- a bus-exec hit at this PC in another ROM bank is not BattleTurn: cheapest refusal first
        if io.read_u8(profile.ram.hROMBank, "System Bus") ~= p.battle_hold.execution_before.bank then return end
        if not safety.check(BATTLE_FAINT) then return end
        -- an unreadable party keeps the queue (Gen 1: "gone" and "not readable yet" look alike)
        if not current_party() then return end
        local battle, link = reads.read_battle(), wram_byte("wLinkMode")
        if not battle or battle.mode == 0 or link ~= 0 then return end
        if settle and not FS.begin_hold(battle) then return end -- one dispatch token per hold; Explosion survival check
        lift_deferred_deaths()
        if #self.pending_battle_writes == 0 then return end
        local targets = p.battle_hold.write.targets
        local function target(name)
            local t = targets[name]
            -- a game without that field (Polished has no wPlayerSubStatus5) simply has no such target
            if t == nil or io.bank_valid(t.bank, t.address, 1) ~= true then return nil end
            return io.read_u8(t.address, "System Bus")
        end
        local snapshot = { mode = battle.mode, battle_type = battle.battle_type,
                           active_slot = battle.active_slot, link_mode = link }
        if settle then return FS.battle_hold(battle, snapshot, target) end
        local keep = {}
        for _, w in ipairs(self.pending_battle_writes) do
            local slot, mon, _, why = find_party_slot(w.key, w.cmd)
            local landed, err = false, nil
            if why then
                log("[SLink-gen2] battle write dropped: " .. why .. " " .. tostring(w.key))
            elseif not slot then
                defer_held(w) -- a contest-hidden or departed mon: the checkpoint decides
            elseif slot == battle.active_slot then
                -- Gen 1 active_faint_guard: the battle struct must be this slot's (Transform excepted)
                local species, sub5 = target("wBattleMonSpecies"), target("wPlayerSubStatus5")
                local transformed = sub5 ~= nil and math.floor(sub5 / 2 ^ transformed_bit) % 2 == 1
                -- p.battle_species_matches (optional): a game whose mon.species_id is not the raw battle-struct byte
                -- (Polished: the 9-bit effective id of a variant form) compares in its own terms; vanilla compares raw
                local same = species == mon.species_id
                if p.battle_species_matches then same = p.battle_species_matches(mon, species) == true end
                if same or transformed then
                    -- W-3: a committed move becomes EXPLOSION; an item/switch already spent the turn and
                    -- a landed bench write that got switched in is already dead: both take W-2
                    snapshot.player_action = target("wBattlePlayerAction")
                    local explode = w.cmd == "force_explode" and not w.landed and snapshot.player_action == 0
                    local ok, e = pcall(function()
                        writes:arm("battle_hold")
                        if explode then writes:explode_active_battler(slot, snapshot)
                        else writes:faint_active_battler(slot, snapshot) end
                    end)
                    writes:disarm()
                    landed, err = ok, e
                else
                    keep[#keep + 1] = w
                end
            elseif mon.hp == 0 then
                -- Gen 1 parity (NIT-7): a bench mon already fainted needs no write, KO line or re-zero
                log("[SLink-gen2] battle write skipped: already at HP 0 " .. tostring(w.key))
            else
                local ok, e = pcall(function()
                    writes:arm("battle_hold")
                    writes:faint_party_slot(slot, snapshot)
                end)
                writes:disarm()
                landed, err = ok, e
            end
            if landed then
                -- only the active battler runs HandlePlayerMonFaint (the echo); a bench write has none
                if slot == battle.active_slot then self.commanded[mon_key(mon)] = true end
                local quiet = w.quiet or self.dead_keys[w.key]
                mark_dead(w.key, mon)
                if quiet then
                    log("[SLink-gen2] re-zeroed a revived dead mon in battle " .. w.key .. " -> " .. mon_key(mon))
                else
                    hud.show("!! " .. nick_label(w.key, w.nickname or mon.nickname) .. " KO'd", 255, 80, 80, 360)
                end
                -- dead stays dead: EvolveAfterBattle and the Battle Tower reload can revive it (facts doc §2)
                defer_held({ cmd = "force_faint", key = w.key, nickname = w.nickname, arrival = w.arrival, quiet = true })
            elseif err ~= nil then
                -- p.battle_release_poll compositions refuse the same queued write at every hold: say so once
                if not (p.battle_release_poll and w.refusal_logged) then
                    log("[SLink-gen2] battle write refused: " .. tostring(err) .. " " .. tostring(w.key))
                end
                w.refusal_logged = true
                keep[#keep + 1] = w
            end
        end
        self.pending_battle_writes = keep
    end

    -- O-32 (Gen 1 handle_command: a benched mon dies the frame the command arrives, owner 2026-09-22 "so it
    -- can never be switched in"): at the battle frame end after the command's receipt, never inside a CPU
    -- hold (gen2_write_safety evaluate_frame). Frame end is the game's DelayFrame boundary: every in-battle
    -- read of a bench mon's party HP is a live read (CheckIfCurPartyMonIsFitToFight, C engine/battle/
    -- core.asm:3650-3656, from TryPlayerSwitch :5176 and PickPartyMonInBattle :2852-2860; the party list
    -- only draws it), so a death landed first refuses the switch. The entry stays queued, as Gen 1 keeps it:
    -- a switch already past that check (TryPlayerSwitch :5176-5192 -> BattleMonEntrance -> InitBattleMon
    -- :5249-5271, frames apart) is the active battler at the next hold and dies there by W-2 before it acts;
    -- a revival by GiveExperiencePoints (fainted test :7004, level-up HP gain after the exp text :7121-7240;
    -- G :6764/:6881-6990) is lifted by the quiet re-zero every landing leaves. A refused frame (an
    -- animation's SVBK, the permit, the evaluation) retries the next one. Link battles never write (O-30).
    function self:land_bench_deaths()
        if not self.bench_owed or not p.battle_bench or not self.writes_enabled then return end
        local battle, link = reads.read_battle(), wram_byte("wLinkMode")
        if not battle or link == nil then return end
        if battle.mode == 0 or link ~= 0 then self.bench_owed = false return end
        if not safety.check(BATTLE_BENCH) or not current_party() then return end
        self.bench_owed = false
        lift_deferred_deaths()
        local snapshot = { mode = battle.mode, battle_type = battle.battle_type,
                           active_slot = battle.active_slot, link_mode = link }
        for _, w in ipairs(self.pending_battle_writes) do
            local slot, mon, _, why = find_party_slot(w.key, w.cmd)
            if not w.landed and not w.ob and slot and not why and slot ~= battle.active_slot and mon.hp > 0 then
                local ok, err = pcall(function()
                    writes:arm(BATTLE_BENCH)
                    writes:faint_party_slot(slot, snapshot)
                end)
                writes:disarm()
                if ok then
                    local quiet = w.quiet or self.dead_keys[w.key]
                    mark_dead(w.key, mon)
                    if quiet then
                        log("[SLink-gen2] re-zeroed a revived dead mon on receipt " .. w.key .. " -> " .. mon_key(mon))
                    else
                        log("[SLink-gen2] bench write landed on receipt: slot " .. slot .. " " .. tostring(w.key))
                        hud.show("!! " .. nick_label(w.key, w.nickname or mon.nickname) .. " KO'd", 255, 80, 80, 360)
                    end
                    -- still queued (landed, quiet): the hold settles a switch-in that beat the write
                    w.landed, w.quiet = true, true
                    defer_held({ cmd = "force_faint", key = w.key, nickname = w.nickname, arrival = w.arrival, quiet = true })
                else
                    self.bench_owed = true
                    if not w.bench_refused then
                        w.bench_refused = true
                        log("[SLink-gen2] bench write refused on receipt, retried: " .. tostring(err) .. " " .. tostring(w.key))
                    end
                end
            end
        end
    end

    -- A composition with a battle hold but NO battle_end engine signal (Polished binds only capture_party) never runs
    -- the battle_end hand-off above, so a death queued in battle and refused at every hold would stay queued for
    -- good. p.battle_release_poll opts into the same hand-off from the battle mode itself: out of battle, every
    -- owed write goes to the checkpoint. Vanilla does not set it (its battle_end signal does this, with the echo
    -- bookkeeping), so its behaviour is unchanged.
    function self:release_battle_writes()
        if not p.battle_release_poll or #self.pending_battle_writes == 0 then return end
        local battle = reads.read_battle()
        if not battle or battle.mode ~= 0 then return end
        for _, w in ipairs(self.pending_battle_writes) do defer_held(w) end
        self.pending_battle_writes = {}
    end

    -- ── W-4 Rival Team Swap (lua/gen1/client.lua replace_rival_team / rival_window_tick) ──────────
    -- The window: a trainer battle whose enemy party ReadTrainerParty already loaded and nobody sent
    -- out yet (wCurOTMon = $FF from InitEnemyTrainer until LoadEnemyMon; lua/gen2/writes.lua header).
    -- Announced at the first battle frame end that shows it, answered from the partner's party at a
    -- battle frame end (battle_bench's evaluate_frame: in battle, not linked, WRAMX bank 1; the intro
    -- slide switches WRAMX away, C engine/battle/sliding_intro.asm:1-13, so those frames retry).
    -- RIVAL_FRAMES bounds a reply that never finds a writable frame; the RIVAL_WINDOW log line
    -- measures the real window (frames from the announcement to the send-out) for the live gate.
    local function rival_reply(cmd, ids, err)
        send("rival_team_replaced", { trainer_id = cmd.trainer_id, species_ids = ids or arr({}), error = err })
    end
    local native_rival_window = p.rival_swap and writes and type(writes.rival_gate_enabled) == "function"
                                and writes:rival_gate_enabled()
    local function flush_rival_reply(r)
        if r and r.callback_reply then
            local reply = r.callback_reply
            r.callback_reply, r.pending = nil, nil
            rival_reply(reply.cmd, reply.ids, reply.error) -- network stays outside the exec callback
            if not reply.error then
                pcall(log, "[SLink-gen2] Polished rival team written: " .. #reply.ids .. " mon(s)")
            end
        end
    end
    if native_rival_window then
        authority.rival_write_window = function()
            local r = self.rival
            if not r or r.gate_used then return nil end
            r.gate_used, r.closed = true, true -- the first send-in consumes this battle visit, even if no reply arrived
            local pend = r.pending
            if not pend then return nil end
            local ok, err = pcall(function()
                assert(self.writes_enabled, "rival callback: writes disabled")
                assert(r.epoch == self.epoch, "rival callback: stale visit")
                local ctx = writes:rival_gate_context(r.trainer_id)
                ctx.trainer_id = r.trainer_id
                writes:arm("rival_swap")
                writes:write_enemy_party(pend.mons, ctx)
            end)
            writes:disarm()
            local ids
            if ok then
                r.applied, ids = true, arr({})
                for i, mon in ipairs(pend.mons) do ids[i] = mon.record[c.MON_SPECIES + 1] end
            end
            r.callback_reply = {cmd=pend.cmd, ids=ids, error=not ok and tostring(err) or nil}
            -- Keep pending until frame-end flush; gate_used is the once-per-visit fence between callbacks.
            if ok then return {ok=true, result={reason="rival_swap", count=#pend.mons}} end
            return nil
        end
    end
    function self:rival_close(why)
        local r = self.rival
        flush_rival_reply(r)
        self.rival = nil
        if r and r.pending then
            log("[SLink-gen2] rival swap closed unanswered: " .. tostring(why))
            rival_reply(r.pending.cmd, nil, "late_reply")
        end
    end
    -- A rival window needs a writer that carries the W-4 SYM facts AND the rival_swap write kinds. A writer
    -- composed for a game with neither (the Polished overworld writer: force_faint + box_mon only) announces
    -- nothing here, exactly as a composition with no writer at all does.
    local function cur_ot_mon()
        local sym = writes and writes.sym
        if not (type(sym) == "table" and type(sym.wCurOTMon) == "table") then return nil end
        local pt = sym.wCurOTMon
        if io.bank_valid(pt[1], pt[2], 1) ~= true then return nil end
        return io.read_u8(pt[2], "System Bus")
    end

    function self:replace_rival_team(cmd)
        local r = self.rival
        if not p.rival_swap then return rival_reply(cmd, nil, "unsupported") end
        if not r or r.trainer_id ~= cmd.trainer_id then return rival_reply(cmd, nil, "not_in_battle") end
        if r.applied then return rival_reply(cmd, nil, "already_applied") end
        if r.closed then return rival_reply(cmd, nil, "late_reply") end
        -- decode now so a bad payload is answered at once, not when the window opens
        local n, rl, ol = type(cmd.blobs_hex) == "table" and #cmd.blobs_hex or 0, c.PARTYMON_STRUCT_LENGTH, c.NAME_LENGTH
        if n < 1 or n > c.PARTY_LENGTH then return rival_reply(cmd, nil, "bad_blob_count") end
        local living, fainted = {}, {}
        for i = 1, n do
            local hex = cmd.blobs_hex[i]
            if type(hex) ~= "string" or #hex ~= (rl + ol + c.MON_NAME_LENGTH) * 2 or not hex:match("^%x+$") then
                return rival_reply(cmd, nil, "bad_blob_length")
            end
            local b = hex_bytes(hex)
            local m = { record = { table.unpack(b, 1, rl) }, ot = { table.unpack(b, rl + 1, rl + ol) },
                        nick = { table.unpack(b, rl + ol + 1, #b) } }
            local hp = b[c.MON_HP + 1] * 256 + b[c.MON_HP + 2]
            if hp > 0 then living[#living + 1] = m else fainted[#fainted + 1] = m end
        end
        -- living first, order kept: the engine sends out the first mon with HP (writes.lua W-4)
        for _, m in ipairs(fainted) do living[#living + 1] = m end
        if r.pending then rival_reply(r.pending.cmd, nil, "late_reply") end -- one parked reply (Gen 1)
        r.pending = { cmd = cmd, mons = living }
        -- rival_tick runs later this same frame end (after replies:step) and lands it if it can
    end

    function self:rival_tick()
        flush_rival_reply(self.rival)
        local battle = reads.read_battle()
        if not battle then return end
        if battle.mode == 0 then return self:rival_close("battle over") end
        local link, cur = wram_byte("wLinkMode"), cur_ot_mon()
        if battle.mode ~= c.TRAINER_BATTLE or link ~= 0 or not battle.trainer or battle.trainer.class_raw == 0
           or cur == nil then return end
        local id, r = trainer_id_of(battle.trainer), self.rival
        if not r or r.trainer_id ~= id then
            if cur ~= 0xFF then return end -- joined after the send-out: no window to announce
            self:rival_close("another trainer battle")
            r = { trainer_id = id, frame = self.frame, epoch = self.epoch }
            self.rival = r
            send("trainer_battle_start", { trainer_id = id })
        end
        if cur ~= 0xFF and not r.sendout_frame then
            r.sendout_frame = self.frame
            log("[SLink-gen2] RIVAL_WINDOW frames=" .. (self.frame - r.frame))
        end
        if cur ~= 0xFF or self.frame - r.frame > Client.RIVAL_FRAMES then
            r.closed = true
            if r.pending then
                local cmd = r.pending.cmd
                r.pending = nil
                rival_reply(cmd, nil, "late_reply")
            end
            return
        end
        if native_rival_window then return end -- preserve the parked command for the native exec window
        if not r.pending or not self.writes_enabled or not safety.check(BATTLE_BENCH) then return end
        local pend = r.pending
        r.pending = nil
        local ok, err = pcall(function()
            writes:arm("rival_swap")
            writes:write_enemy_party(pend.mons, { mode = battle.mode, link_mode = link, cur_ot_mon = cur,
                                                 trainer_id = r.trainer_id })
        end)
        writes:disarm()
        if ok then
            r.applied = true
            local ids = arr({})
            for i, m in ipairs(pend.mons) do ids[i] = m.record[c.MON_SPECIES + 1] end
            log("[SLink-gen2] rival team replaced: " .. #pend.mons .. " mon(s), trainer " .. id)
            rival_reply(pend.cmd, ids)
        else
            rival_reply(pend.cmd, nil, tostring(err))
        end
    end

    -- Production: the CPU sits at the checkpoint PC (OWPlayerInput, before `call CheckAPressOW`),
    -- the only place check(kind) can accept. An accepted hold arms this frame's hello readiness
    -- and runs one deferred write inside the hold, exactly where the U2 gate wrote.
    function self:at_checkpoint()
        if not safety.check(PARTY_HP) then return end
        self.checkpoint_held = true
        self:run_deferred()
    end

    function self:frame_end()
        local now = io.framecount()
        -- Gen 2: onframeend runs once per emulated frame (lua/gen2/run.lua), so any other step of
        -- the frame counter is a savestate load or rewind: everything queued describes another timeline.
        if self.last_frame ~= nil and now ~= self.last_frame + 1 then self:abandon_timeline("savestate load") end
        self.last_frame, self.frame = now, now
        net.pump()
        if not net.connected() and self.hello_session:status().connected then
            self.hello_session:invalidate("disconnected")
        end
        -- Gen 2 (gen2-hello-flap): frame end can land mid-routine with WRAMX switched away (battle
        -- animations select SVBK = BANK(wBGPals1) = 5: C engine/battle_anims/anim_commands.asm:1413,
        -- bg_effects.asm:2562,2589). Every frame-end read below would find its bank unmapped; the old
        -- nil identity made HelloSession invalidate (identity_unavailable) and re-hello every few frames
        -- in battle. Skip game reads, not transport invalidation; the next mapped frame
        -- catches up without publishing a half-read (signals stay latched).
        if io.bank_valid(profile.ram_bank.wPlayerID, profile.ram.wPlayerID, 1) ~= true then return end
        -- P4.1f panel: every mapped frame, before the hello step (the player may sit in START).
        if panel then
            local pok, perr = panel:service()
            if not pok then log("[SLink-gen2] panel: " .. tostring(perr)) end
        end
        if phone then -- P4.5c: after the panel refreshed freshness this frame
            local pok, perr = pcall(phone.service, phone)
            if not pok then log("[SLink-gen2] phone: " .. tostring(perr)) end
        end
        local connected = self.hello_session:step(self.frame)
        self.checkpoint_held = false -- one frame's hold arms one frame's readiness
        self.hello_sent = connected == true
        if self.frame % Client.VALIDATE_EVERY == 0 then self:validate() end
        connected = connected and self.hello_session:status().ready
        if connected and #self.held > 0 then
            local held = self.held
            self.held, self.held_full = {}, false
            for _, m in ipairs(held) do send(m.event, m.fields) end
        end
        self.owed:step(net.connected(), connected == true, send) -- owed reports, after any held messages
        for _, batch in ipairs(self.signals and self.signals:drain() or {}) do
            for _, ev in ipairs(batch.events or {}) do
                if settle then ev.batch_generation = batch.generation end -- F2: validated at consumption
                local ok, err = pcall(self.on_event, self, ev)
                if not ok then log("[SLink-gen2] signal " .. tostring(ev.site_id) .. ": " .. tostring(err)) end
            end
        end
        -- A binder fault latches the shared registry (no later signal is captured): say so
        -- once, loudly, rather than run on as if events were still being observed.
        local st = self.signals and self.signals:status()
        -- A refusal is the binder declining one observation (no event); each distinct
        -- site/reason is logged once, never per frame.
        for site, why in pairs(st and st.refusals or {}) do
            local seen = site .. ": " .. tostring(why)
            if not self.refusals_logged[seen] then
                self.refusals_logged[seen] = true
                log("[SLink-gen2] engine signal refused " .. seen)
            end
        end
        if st and st.failed and st.failed ~= self.signal_failure_logged then
            self.signal_failure_logged = st.failed
            log("[SLink-gen2] engine signals STOPPED: " .. tostring(st.failed))
            hud.show("SLINK STOPPED - SEE LOG", 255, 64, 64, 600)
        end
        self:settle_faints()
        if settle then FS.frame() end
        -- p.rescan_every (Polished only, compose_polished): no engine site, PC event or box number marks a box
        -- arrival there, so a battle end (wBattleMode nonzero -> 0) or the periodic interval re-arms the census,
        -- checked once per tick. nil (vanilla): never entered. The rescan stays read-only and fail-closed.
        if self.rescan_every and self.frame % Client.TICK_INTERVAL == 0 then
            local b = reads.read_battle()
            local mode = b and b.mode
            if (mode == 0 and (self.census_mode or 0) ~= 0)
                or math.abs(self.frame - (self.census_frame or 0)) >= self.rescan_every then
                self.pending_rescan, self.census_frame = true, self.frame
            end
            if mode then self.census_mode = mode end
        end
        -- KEY-SCOPE-5: an incomplete scan is retried, at most once per tick
        if self.pending_rescan or (not self.box_complete and self.frame % Client.TICK_INTERVAL == 0) then
            self.pending_rescan = false; self:rescan_boxes()
        end
        if connected and self.frame % Client.TICK_INTERVAL == 0 then self:send_tick("tick") end
        if self.pending_safe and connected then
            local battle = reads.read_battle()
            if battle and battle.mode == 0 then
                -- the battle-end rescan ran above this frame: its census rides the safe (KEY-SCOPE-5)
                self.pending_safe = false
                send("safe", { pc_boxes = pc_boxes_wire(), pc_boxes_generation = box_generation() })
            end
        end
        if trade then
            local tok, terr = pcall(self.trade_tick, self)
            if not tok then log("[SLink-gen2] trade: " .. tostring(terr)) end
        end
        if dev_trade then
            local ok, why = pcall(dev_trade.tick)
            if not ok then dev_trade.forget(tostring(why)) end
        end
        if self.frame % Client.BURIAL_NAG_FRAMES == 0 and burial_waiting() then show_burial() end
        self.replies:step()
        self:land_bench_deaths() -- O-32: a bench death lands the frame its command arrived (replies:step)
        self:rival_tick() -- W-4: announce a trainer battle; land a parked replace_rival_team (replies:step)
        self:release_battle_writes() -- Polished: no battle_end signal, so the battle mode hands the owed writes over
        self:run_deferred()
    end

    function self:stop()
        if dev_trade then dev_trade.stop() end
        if self.checkpoint_hook then io.unregister(self.checkpoint_hook); self.checkpoint_hook = nil end
        if self.battle_hook then io.unregister(self.battle_hook); self.battle_hook = nil end
        for _, id in ipairs(self.trade_hooks or {}) do io.unregister(id) end
        self.trade_hooks = nil
        if self.signals then self.signals:close() end
    end

    return self
end

return Client
