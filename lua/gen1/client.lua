-- lua/gen1/client.lua — the Gen 1 Soul Link client, as a state machine over injected parts.
--
-- Contract: docs/protocol.md (the Gen 3 client + server/state.py are the authority).
-- Parts (all injected, so lupa can drive the whole client against a fake server):
--   reads   lua/gen1/reads.lua      pure WRAM/SRAM decoders (differential vs the Python codec)
--   signals lua/gen1/signals.lua    engine execution sites -> typed signals
--   writes  lua/gen1/writes.lua     faint/explode/rival writes behind the armed window
--   boxes   lua/gen1/boxes.lua      PC/memorial box moves (same armed window)
--   safety  lua/gen1_write_safety   the verified overworld checkpoint
--   net     lua/connector.lua       newline-JSON TCP (init/send/receive/pump/connected)
--   json    lua/json_codec.lua      decode/encode
--   hud     lua/hud.lua             show/prompt/set_game_over/set_rebuilding/clear_rebuilding
--
-- Events are derived from engine signals, never from polling heuristics: a party change is
-- only read after a site that legitimately changes the party fired (AddPartyMon,
-- SendNewMonToBox, MoveMon, RemovePokemon, Evolution_PartyMonLoop's species publish,
-- InGameTrade_DoTrade).
-- Every write happens at the overworld checkpoint or inside the MainInBattleLoop hook.
local Client = { TICK_INTERVAL = 30, VALIDATE_EVERY = 60, MAX_INVALID = 5, MAX_PENDING_FRAMES = 600,
                 TRADE_HOLD_TERMINAL_FRAMES = 1800, TRADE_ARMED_TRIPWIRE_FRAMES = 1800, HOLD_REMINDER_SECONDS = 60,
                 -- A13: the two halves of the `replace_rival_team` window. ONE window measured
                 -- from `battle_begin` cannot work, because the transition alone outlasts any
                 -- figure small enough to be a write window (this was RIVAL_SWAP_FRAMES = 120,
                 -- unsatisfiable behind either spiral). Both numbers below are LOWER BOUNDS built
                 -- from the engine's EXPLICIT delays plus margin -- they deliberately leave out
                 -- the CopyVideoData waits, which cost "c/8 frames" each (home/copy2.asm:62-65,
                 -- :96): LoadBattleTransitionTile (battle_transitions.asm:161-164, c=1) and
                 -- _LoadTrainerPic (core.asm:6681 -> home/pics.asm:193-196, c=PIC_SIZE=49, ~7
                 -- frames). The real deltas are measured per battle and logged as
                 -- `RIVAL_WINDOW init_frames=N staged_frames=M`.
                 --
                 -- (a) battle_begin (InitBattleCommon offset 0, engine_signals.json:43-49) -> the
                 -- staging at engine/battle/core.asm:6689-6690. In between sits
                 -- DoBattleTransitionAndInitBattleVariables (:6680):
                 --   outward spiral  120 DelayFrames        battle_transitions.asm:194-205
                 --   inward spiral   51 x TransferDelay3    :213-260 with :616-622 (Delay3)
                 --                   = 153 frames
                 --   prefix          Delay3 + DelayFrame + Delay3 (:4,:9,:49) + core.asm:6164
                 --                   = 8 frames
                 -- so >= 128 and >= 161; 240 is the worse of the two with ~50% margin.
                 RIVAL_INIT_FRAMES = 240,
                 -- (b) staging -> the FIRST consumer of the enemy party. This is NOT a lower
                 -- bound like (a): it is the maximum AGE of a $FF observation that may still be
                 -- written through, and it is what makes the byte sufficient rather than merely
                 -- necessary.
                 --
                 -- Why the byte alone is not enough. wEnemyMonPartyPos is not cleared on entry to
                 -- EnemySendOutFirstMon: :1326 reads it to pick the slot, :1337-1341 reads that
                 -- mon's HP, :1343-1348 its level and :1349-1357 its species, and only
                 -- LoadEnemyMonData stores the new position at :6055 -- after CalcStats (:6027)
                 -- and the HP copy (:6046-6053). That span contains NO engine-initiated wait (its
                 -- only calls are AddNTimes, GetMonHeader and CalcStats; BattleRandom is the
                 -- wild-only branch), but a BizHawk frame boundary is a PPU event, not a code
                 -- event, so a frame-end callback CAN land inside it and still read $FF.
                 --
                 -- And the send-out is not even the first party read. In order from the staging:
                 --   1. DrawAllPokeballs   common_text.asm:21-29 (`ld c,20 / DelayFrames`, THEN
                 --      `callfar DrawAllPokeballs`) -> draw_hud_pokeball_gfx.asm:1-7 ->
                 --      SetupEnemyPartyPokeballs :33-45 reads wEnemyMons + wEnemyPartyCount, and
                 --      PickPokeball :69-95 reads each mon's HP and status.  <-- FIRST CONSUMER
                 --   2. StartBattle core.asm:139-148 scans wEnemyMon1HP for the first alive mon
                 --      before it calls EnemySendOutFirstMon (:154).
                 --   3. EnemySendOutFirstMon :1326+ as above.
                 -- Explicit floor from the staging to (1), all inside _InitBattleCommon (:6735)
                 -- -> SlidePlayerAndEnemySilhouettesOnScreen (:9-100, which `jpfar`s into
                 -- PrintBeginningBattleText at :100):
                 --   Delay3 / DelayFrame / Delay3   :58, :65, :96          =  7 frames
                 --   silhouette slide  :70-84, 72 ITERATIONS (c = $90, dec c twice), each
                 --     blocking on rLY at $40 then $60 within one frame, so >= 71 guaranteed
                 --     inter-iteration frame crossings plus a partial first and last
                 --                                                        >= 71 frames
                 --   TrainerWantsToFight DelayFrames  common_text.asm:22-23 = 20 frames
                 --                                                        -------------
                 --                                                        >= 98 frames
                 -- 60 is that with a wide margin. A $FF seen by the client can itself be one
                 -- frame stale (the store happens inside a frame; the callback for that frame may
                 -- already have run), so the last permitted write lands at most 61 frames after
                 -- the store -- still far under 98.
                 --
                 -- The `RIVAL_WINDOW staged_frames=M` line below measures staging -> the
                 -- POSITION BYTE CHANGING, which is (3), downstream of (1) and (2). It must NEVER
                 -- be used to raise this cutoff; only a measured staging -> DrawAllPokeballs
                 -- interval could. COST of the cutoff: a reply arriving more than 60 frames after
                 -- the staging is refused `late_reply` even though the byte still reads $FF. In
                 -- the live flow the reply is parked through the transition and applied ON the
                 -- staging frame, so this only refuses a server answering about a second late.
                 RIVAL_STAGED_FRAMES = 60 }

-- The ball set is profile.derived.ball_items (vanilla MASTER..POKE 1..4, item_constants.asm:10-13;
-- pureRGB adds 5 and 8) and profile.derived.opp_id_offset (constants/trainer_constants.asm:1):
-- built per client below, never a module literal.
local SRAM_BANK_SIZE = 0x2000     -- layout.link:195-202, one SRAM bank window
-- Pokemon Tower 1F-7F ($8E-$94, map_constants.asm:228-234). A wild battle on these maps
-- without the Silph Scope (item_constants.asm:84) in the bag is a "ghost": the engine
-- refuses both the fight and the throw, so the battle ending says nothing about whether the
-- player failed to catch anything -- it is NOT a failed encounter and must not dead-zone
-- the area (docs/gen1_requirements.md F-4/S-2).
local TOWER_MAPS = { [0x8E] = true, [0x8F] = true, [0x90] = true, [0x91] = true,
                     [0x92] = true, [0x93] = true, [0x94] = true }
local SILPH_SCOPE = 0x48
local MOVE_BOX_TO_PARTY, MOVE_PARTY_TO_BOX, MOVE_DAYCARE_TO_PARTY, MOVE_PARTY_TO_DAYCARE = 0, 1, 2, 3
-- Y-0: the two DEMONSTRATION battle types (constants/battle_constants.asm:43-46) —
-- BATTLE_TYPE_OLD_MAN (1, the Viridian catching tutorial) and BATTLE_TYPE_PIKACHU (4, Oak
-- catching the Pikachu in Yellow). Neither is the player's own encounter, so neither resolves
-- the area and neither reports a capture. BATTLE_TYPE_SAFARI (2) is NOT here: item_effects.asm
-- spends the player's Safari Balls (:130-137) and adds a Safari catch to the party/box
-- (:549-560), so it keeps both semantics.
local DEMO_BATTLE_TYPES = { [1] = true, [4] = true }
local TRANSFORMED_BIT = 8 -- bit 3 of wPlayerBattleStatus3 (battle_constants.asm:106)
-- BURIAL-VISIBLE: a boxed burial waiting on an in-game SAVE (BOX-MEMORIAL-2) is shown, every this many
-- frames while it waits (hud.show sanitizes; the tick's awaiting_save puts it on the pair board)
Client.BURIAL_NAG_FRAMES = 1200
local BURIAL_TEXT = "SAVE TO FINISH BURIAL"

local function hex_of(bytes)
    local out = {}
    for i = 1, #bytes do out[i] = string.format("%02X", bytes[i]) end
    return table.concat(out)
end
local function hex_bytes(hex)
    local out = {}
    for i = 1, #hex, 2 do out[#out + 1] = tonumber(hex:sub(i, i + 1), 16) end
    return out
end
-- key_change reasons by pending_change kind (PLAN §4 row 19: evolution, npc_trade, transform,
-- apex_chip); the settle sends `reason = KEY_CHANGE_REASON[pc.kind]`
local KEY_CHANGE_REASON = { evolution = "evolution", npc_trade = "npc_trade",
                            transform = "transform", apex_chip = "apex_chip" }
-- wBattleFunctionalFlags bit 1: the player ran (pureRGB; PLAN M2-b). †UNVERIFIED bit index
-- against the pinned pureRGB source (no local checkout); the profile symbol gates the read.
local RAN_FLAG_BIT = 2

-- Gen 1 raw stage 7 == neutral; the wire wants seven slots, 6 == neutral, SDEF blank.
local function wire_stages(raw)
    if not raw then return nil end
    local function s(v) return (v or 7) - 1 end
    return { s(raw.attack), s(raw.defense), s(raw.speed), s(raw.special), 6, s(raw.accuracy), s(raw.evasion) }
end

-- KEY-SCOPE-5: key_change refusals that retire nothing. The alias stays, and the change goes out
-- again after the next complete box census (send_tick). Every other reason is terminal (U5).
Client.RETRYABLE_REJECTIONS = { ["box census unavailable"] = true, ["ambiguous key (trade clash)"] = true }

function Client.new(p)
    local HelloSession = assert(p.hello_session, "shared hello_session factory required")
    local ReplyDispatch = assert(p.reply_dispatch, "shared reply_dispatch factory required")
    local OwedReports = assert(p.owed_reports, "shared owed_reports factory required")
    local reads, signals_mod, writes, safety = p.reads, p.signals, p.writes, p.safety
    local net, json, hud, io = p.net, p.json, p.hud, p.io
    local profile, sites, ws_profile, area_map = p.profile, p.sites, p.write_checkpoint, p.area_map
    local log = p.log or function(...) end
    local d = profile.derived
    local arr = json.array -- tag lists so an empty one encodes as [] not {}
    local BALL_ITEMS = {}
    for _, id in ipairs(assert(d.ball_items, "profile.derived.ball_items required")) do
        BALL_ITEMS[id] = true
    end
    local opp_id_offset = assert(d.opp_id_offset, "profile.derived.opp_id_offset required")
    local box_count = d.sram_boxes_per_bank * #d.sram_box_banks
    local sram_size = (d.sram_box_banks[#d.sram_box_banks] + 1) * SRAM_BANK_SIZE

    local self = {
        player = p.player, rom_type = p.rom_type, rom_sha1 = p.rom_sha1,
        foundation = p.foundation or "gen1_rby", artifact_kind = p.artifact_kind or "clean",
        seq = 0, frame = 0, hello_sent = false,
        writes_enabled = false, invalid_streak = 0, gate_revoked = false,
        known_keys = {}, box_cache = {}, resolved_areas = {}, config = {},
        box_generation = 0, box_complete = false, -- KEY-SCOPE-5: bumped per complete rescan_boxes
        -- old_key -> the physical key the cartridge now holds, for a key_change the server
        -- REJECTED: its retirement commands (force_faint / memorialize) name the old key the
        -- server still knows, the mon's bytes carry the new one (review cx-6aacc4f1 #1)
        retired_alias = {},
        deferred = {}, pending_battle_writes = {}, arrivals = 0,
        pending_change = nil, pending_rival = nil, battle = nil, has_pokeballs = false,
        nuzlocke_announced = false,
        signals = nil, boxes = p.boxes, rom = p.rom, statics = p.statics, panel = p.panel,
        -- deferred backing-box removals ({key, armed}) waiting for save_witness (gen1-box-durability)
        box_settle = {},
        trade = p.trade, trade_enabled = false, trade_state = nil,
        -- post-DONE trade reports and trade_uncertain: sent in order once the hello is ready and kept until
        -- the server answers them (lua/owed_reports.lua; review 2026-09-24 MAJOR-1, mirror of Gen 2)
        owed = OwedReports.new(),
        -- A1: server-seeded pending-capture keys (part of the APEX collision set) and the
        -- old->new alias held between a key_change and its ack
        pending_keys = {}, key_alias = nil, apex = nil, transforming = nil,
        -- review 2026-09-24 MINOR-7 (mirror of lua/gen2/client.lua dead_keys): every key whose death landed
        -- here, until its burial; a heal before the burial is re-zeroed at the checkpoint and the loop head,
        -- quietly. Kept across a reset (the client outlives it).
        dead_keys = {},
        -- INV-CLIENT-2: the server's dead/memorial keys for this player, sent at every accepted hello
        -- (`dead_keys`), REPLACE the set; after a reset/reload/identity change the sweep waits for that sync
        -- (another save with the same OT must never get a stale write)
        dead_synced = true,
    }
    self.trade_owed = self.owed.list

    -- ── outbound ─────────────────────────────────────────────────────────────────────
    local function send(event, fields)
        if not net.connected() then
            log("[SLink-gen1] drop " .. event .. ": not connected")
            return false
        end
        self.seq = self.seq + 1
        local msg = fields or {}
        msg.event, msg.player, msg.seq = event, self.player, self.seq
        -- connector.send queues successfully with no return value. Explicit false
        -- from an injected transport is refusal, not a successful hello.
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

    -- ── reads → wire shapes ──────────────────────────────────────────────────────────
    local function mon_key(m) return reads.key(m) end

    -- Gen 3 parity (nick_label, archive/gen3-old-client:lua/clients/gen3_frlge_client.lua:476-477): a nickname when the caller
    -- has one, else the key's short form -- shared by the local HUD moments below.
    local function nick_label(key, nickname)
        if nickname and nickname ~= "" then return nickname end
        return key and key:sub(1, 8) or "?"
    end

    local function party_entry(m, active_slot, stages)
        -- 66-byte blob: struct(44) + OT(11) + nick(11), what the adapter's party_blob_size says
        local blob = io.read_range(profile.ram.wPartyMons + m.slot * d.party_struct_size, d.party_struct_size, "System Bus")
        for i = 1, #m.ot_name_bytes do blob[#blob + 1] = m.ot_name_bytes[i] end
        for i = 1, #m.nickname_bytes do blob[#blob + 1] = m.nickname_bytes[i] end
        local e = {
            key = mon_key(m), slot = m.slot, species_id = m.species, nickname = m.nickname,
            level = m.level, hp = m.hp, maxHP = m.max_hp, status_cond = m.status,
            moves = arr(m.moves), pp = arr(m.pp), pp_ups = arr(m.pp_ups), blob_hex = hex_of(blob),
            active = (active_slot ~= nil and m.slot == active_slot) or false,
        }
        if e.active and stages then e.stat_stages = arr(wire_stages(stages)) end
        return e
    end

    local function area_of(map)
        local a = area_map[tostring(map)]
        if a then return a.area_id, a.name end
        return "", "map_" .. tostring(map)
    end

    -- The last two party reads that DECODED, by frame: which keys were party members before a
    -- given engine site fired. An evolution retires a key, and the only honest witness for
    -- "which key" is a party read taken BEFORE the species was published -- a read in the same
    -- frame as the site (validate/tick run after the hook) already shows the new species.
    local party_reads = {}
    local function remember_party(party)
        local keys = {}
        for _, m in ipairs(party) do keys[mon_key(m)] = true end
        if party_reads[1] and party_reads[1].frame == self.frame then party_reads[1].keys = keys
        else table.insert(party_reads, 1, { frame = self.frame, keys = keys }); party_reads[3] = nil end
    end
    local function party_keys_before(frame_no)
        for _, r in ipairs(party_reads) do if r.frame < frame_no then return r.keys end end
        return nil
    end

    local function current_party()
        local party, why = reads.read_party()
        if not party then return nil, why end
        remember_party(party)
        return party
    end

    local function snapshot_party()
        local party = current_party()
        if not party then return nil end
        local battle = reads.read_battle()
        local active = battle.in_battle ~= 0 and battle.player_mon_number or nil
        local stages = active and reads.read_stat_stages("player") or nil
        local out = arr({})
        for i, m in ipairs(party) do out[i] = party_entry(m, active, stages) end
        return out, battle
    end

    local function enemy_party(battle)
        if battle.in_battle == 0 then return arr({}) end
        local foe = { species_id = battle.enemy_species, level = battle.enemy_level,
                      hp = battle.enemy_hp, active = true }
        -- maxHP only when plausible: LoadEnemyMonData's transition frame can pair a new HP with a
        -- stale or zero max; without one the board shows the number alone
        local max_hp = battle.enemy_max_hp
        if math.type(max_hp) == "integer" and max_hp > 0 and max_hp <= 999 and battle.enemy_hp <= max_hp then
            foe.maxHP = max_hp
        end
        local stages = reads.read_stat_stages("enemy")
        if stages then foe.stat_stages = arr(wire_stages(stages)) end
        -- The live wEnemyMon struct is populated in wild AND trainer battles: moves, status
        -- and DVs always (maxHP stays the plausibility-gated read above). Only trainer battles have a party record to read stat exp
        -- from (wild wEnemyMons is a union with wild-encounter data -- HANDOFF task 6/
        -- reads.lua's read_enemy_battle_mon comment), so blob_hex is gated on is_trainer AND
        -- a party_pos the send-out has actually settled (see RIVAL_* window comment above).
        local bm = reads.read_enemy_battle_mon()
        foe.moves = arr(bm.moves)
        foe.status_cond, foe.dvs_raw, foe.pp = bm.status, bm.dvs.raw, arr(bm.pp)
        if battle.is_trainer and bm.party_pos >= 0 and bm.party_pos < d.party_capacity then
            local blob = io.read_range(profile.ram.wEnemyMons + bm.party_pos * d.party_struct_size,
                                        d.party_struct_size, "System Bus")
            foe.blob_hex = hex_of(blob)
        end
        return arr({ foe })
    end

    local function ball_count()
        local bag = reads.read_bag()
        local n = 0
        if bag then for _, it in ipairs(bag.items) do if BALL_ITEMS[it.id] then n = n + it.qty end end end
        return n
    end

    -- Gen 3 parity (archive/gen3-old-client:lua/clients/gen3_frlge_client.lua:4112-4119): banner + SFX once, on the transition
    -- INTO the run -- bag_received fires it directly, send_tick catches the ball-count edge
    -- for anything that reaches the bag another way. Never at hello (send_hello below), which
    -- can only ever observe a bag already stocked from a previous session.
    local function announce_nuzlocke_start()
        if self.nuzlocke_announced then return end
        self.nuzlocke_announced = true
        hud.nuzlocke_start("Nuzlocke Start!")
        self:request_sfx_local(95) -- SE_SHINY, Gen 3's default SE_NUZLOCKE_START (archive/gen3-old-client:lua/memory_gba.lua:1963)
    end

    local function pc_boxes_wire()
        local out = arr({})
        for _, e in ipairs(self.box_cache) do
            out[#out + 1] = { box = e.box, slot = e.slot, key = e.key, species_id = e.species_id,
                              nickname = e.nickname, level = e.level, moves = arr(e.moves) }
        end
        return out
    end

    -- KEY-SCOPE-5: pc_boxes is a complete census only alongside a generation. Omitted before any
    -- complete scan and whenever the latest scan was incomplete (box_cache still goes out for
    -- display), so the server never takes a partial cache for a fresh census.
    local function box_generation()
        return self.box_complete and self.box_generation or nil
    end

    -- Rescan every SRAM box (derived.sram_boxes_per_bank x banks) and the active box (WRAM
    -- mirror) into box_cache. The flat CartRAM image spans bank 0 through the last box bank.
    function self:rescan_boxes()
        local cache = {}
        local cur = reads.read_current_box_num()
        local active = reads.read_active_box()
        -- SRAM boxes are garbage until the game's first ChangeBox initialises them (bit 7 of
        -- wCurrentBoxNum; save.asm EmptyAllSRAMBoxes) — only the WRAM mirror is real before that
        local sram = (cur and cur.initialized) and io.read_range(0, sram_size, "CartRAM") or nil
        -- KEY-SCOPE-5: complete = every box read. Never-initialised SRAM boxes count as read: they
        -- really are empty (the game has never stored a mon there), so skipping them is not a gap.
        local complete = cur ~= nil and active ~= nil and (sram ~= nil or not cur.initialized)
        for box = 0, box_count - 1 do
            local mons
            if cur and box == cur.index then mons = active
            elseif sram then mons = reads.read_sram_box(sram, box); complete = complete and mons ~= nil end
            if mons then
                for _, m in ipairs(mons) do
                    cache[#cache + 1] = { box = box, slot = m.slot, key = mon_key(m), species_id = m.species,
                                          nickname = m.nickname, level = m.level, moves = m.moves }
                end
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
        local party = current_party()
        if not party then return false, "party unreadable" end
        if reads.read_player_id() == 0 and #party == 0 then return false, "pre-game (title/new game)" end
        return true
    end

    -- The scheduler sees only this opaque key. Save identity and the optional
    -- pureRGB version stamp are Gen 1 facts, not neutral scheduling defaults.
    local function hello_identity()
        local id = reads.read_player_id()
        if type(id) ~= "number" or id % 1 ~= 0 or id < 0 or id > 65535 then
            return nil, "player identity unavailable"
        end
        local version = reads.read_game_internal_version()
        return table.concat({tostring(self.player), self.foundation, self.artifact_kind,
                             tostring(self.rom_sha1), tostring(id), tostring(version)}, "|")
    end

    function self:validate()
        local ok, why = game_is_live()
        if ok then
            self.invalid_streak = 0
            if not self.writes_enabled and not self.gate_revoked then
                self.writes_enabled = true
                log("[SLink-gen1] writes ENABLED")
            end
            if self.gate_revoked and self.invalid_streak == 0 then
                self.gate_revoked, self.writes_enabled = false, true
                log("[SLink-gen1] writes re-enabled after a live validation")
            end
        else
            self.invalid_streak = self.invalid_streak + 1
            -- WRAM cleared (home/init.asm after a reset): whatever the player picks next is a
            -- new session for the server — CONTINUE re-hellos the same save (a reconnect),
            -- NEW GAME hellos a fresh wPlayerID and is refused (C-1)
            if reads.read_player_id() == 0 then
                self.hello_session:invalidate("save_reset")
                self.dead_synced = false -- INV-CLIENT-2: the next save waits for the hello's dead_keys
                self:trade_forget("save_reset") -- the lease went with the WRAM; DONE never comes
                self.pending_change = nil -- an acquisition cannot outlive a reset/new save
                -- ...and so does every identity alias: the record each pointed at is gone with
                -- the WRAM, and a reloaded pre-change save holds the OLD key again, which the
                -- server's re-queued retirement then names directly. The PENDING alias goes too,
                -- so a rejection that arrives after the clear cannot re-create a retirement
                -- from a record of the previous session (review cx-e54e6719 c).
                self.retired_alias = {}
                self.key_alias = nil
                self.pending_change = nil
            end
            if self.invalid_streak >= Client.MAX_INVALID and self.writes_enabled then
                -- pause, never drop: the queues survive (an unreadable party is a transient the
                -- engine creates itself, e.g. AddPartyMon's AskName prompt before the struct
                -- lands; a soft reset clears WRAM until the main menu reloads the save). Every
                -- command still needs its key to match at the checkpoint before a byte moves.
                self.writes_enabled, self.gate_revoked = false, true
                log("[SLink-gen1] writes PAUSED: " .. tostring(why))
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

    -- D-13 (MODEL): two party slots can answer to one identity key (same DVs, OT and species).
    -- Inbound commands then have no way to tell which mon the server meant, so the resolver
    -- refuses rather than guesses -- the same fail-closed rule boxes.lua:121-130 already applies.
    -- Returns slot, mon, party, why; `why` is "ambiguous key" when more than one slot matches.
    local function same_bytes(a, b)
        if not a or not b or #a ~= #b then return false end
        for i = 1, #a do if a[i] ~= b[i] then return false end end
        return true
    end
    -- What tells the changed record apart from a duplicate of its (new) key: the fields a
    -- key_change never touches and a party swap cannot forge -- nickname bytes and the move
    -- set. A slot is NOT evidence (the player can reorder the party between the change and the
    -- retirement, review cx-e54e6719).
    local function record_evidence(m) return { nick = m.nickname_bytes, moves = m.moves } end
    local function matches_evidence(m, e)
        return same_bytes(m.nickname_bytes, e.nick) and same_bytes(m.moves, e.moves)
    end
    -- The evidence is FROZEN at the change. A record edited before its retirement lands (a TM
    -- from the item menu, a rename) no longer matches and the retirement is refused with a
    -- failure reply -- never re-identified by similarity: a partial match cannot tell "the
    -- same record after one edit" from "the record left and an unrelated one shares a field"
    -- (review cx-c63e4289). Documented residual: such a retirement needs manual resolution.
    -- Ambiguity is LATCHED, never recomputed away: once two records have been seen carrying
    -- the new key and matching the evidence (at the key_change capture, at the rejection, or at
    -- any later resolution), the alias stays ambiguous even if one of them is later edited or
    -- leaves the party: a shrinking candidate set must not authorise the retirement of the
    -- survivor (review cx-1a22cbbd). Returns the sole matching record, or nil + reason.
    local function observe_alias(a, party)
        local slot, mon, n = nil, nil, 0
        for _, m in ipairs(party) do
            if mon_key(m) == a.new_key and matches_evidence(m, a.evidence) then
                n = n + 1
                slot, mon = m.slot, m
            end
        end
        if n > 1 then a.ambiguous = true end
        if n == 0 then a.lost = true end
        if a.ambiguous then return nil, nil, "ambiguous key (indistinguishable duplicate)" end
        if a.lost then return nil, nil, "retired record left the party" end
        return slot, mon
    end
    -- Continuity: a record carrying an aliased key leaving the party natively (PC deposit,
    -- daycare, release, a trade) ends the alias's authority for good -- an identical record
    -- withdrawn afterwards must not become "the" record (sequential replacement, review
    -- cx-fc0d91b7). An unreadable departure is treated as the aliased record's (conservative).
    local function alias_departure(key)
        local function hit(a) if a and (key == nil or key == a.new_key) then a.lost = true end end
        hit(self.key_alias)
        for _, r in pairs(self.retired_alias) do hit(r) end
    end
    -- The one identity resolver: dispatch (handle_command), battle writes and the deferred
    -- checkpoint all go through here. A key the server still tracks after a REJECTED
    -- key_change resolves to the record the cartridge physically holds: among the records
    -- carrying the new key, exactly one must match the evidence snapshotted when the change
    -- was observed and no second one may ever have been seen; anything else = refused
    -- (nothing is guessed).
    local function find_party_slot(key)
        local party = current_party()
        if not party then return nil end
        local r = self.retired_alias[key]
        if r then
            local slot, mon, why = observe_alias(r, party)
            if slot then return slot, mon, party end
            return nil, nil, party, why
        end
        local slot, mon
        for _, m in ipairs(party) do
            if mon_key(m) == key then
                if slot then return nil, nil, party, "ambiguous key" end
                slot, mon = m.slot, m
            end
        end
        return slot, mon, party
    end

    local function hud_color(cmd)
        if type(cmd.color) == "table" then return cmd.color[1], cmd.color[2], cmd.color[3], cmd.duration end
        return cmd.r, cmd.g, cmd.b, cmd.frames
    end

    -- The one native-SFX gate (panel present + sfx-capable + config.native_sounds): a server
    -- `play_sound` command and every LOCAL HUD moment (nuzlocke start, new encounter, game
    -- over, whiteout) request through this. `gen3_id` is the Gen 3 SE id on the wire
    -- (docs/protocol.md); panel:sfx_code_for maps it to this cartridge's semantic code.
    function self:request_sfx_local(gen3_id)
        local code = self.panel and self.panel:sfx_code_for(gen3_id)
        -- An id with no Gen 1 mapping (panel.lua's SFX_CODE_FOR_GEN3_ID) is not "unavailable" --
        -- it is simply not a sound this cartridge has (cx-6bedd222): fall through silently
        -- rather than tripping sfx_unavailable_logged, which would then never fire for a real
        -- unavailable case (sounds on, capable panel, but a mapped id) later in the run.
        if not code then return end
        if self.config and self.config.native_sounds == true and self.panel:sfx_present() then
            -- Coalesce: commands in the SAME frame can land the identical semantic code -- a terminal linked faint queues force_faint + play_sound 26 to the loser,
            -- then game_over's own local 26 lands in the same reply/frame (state.py:2884 with
            -- :2103/:3202) -- and panel.lua:156-161 would otherwise queue both as if two
            -- distinct cues were wanted. self.frame is set once at the top of frame_end, before
            -- any command this frame is processed, so it is stable for the whole turn.
            -- A per-frame SET, not the last code: 26 -> 25 -> 26 in one reply must still
            -- post the failure once (cx-3987357f). Different codes in one frame stay distinct.
            local seen = self.sfx_frame_codes
            if not seen or seen.frame ~= self.frame then
                seen = { frame = self.frame, codes = {} }
                self.sfx_frame_codes = seen
            end
            if seen.codes[code] then return end
            seen.codes[code] = true
            self.panel:request_sfx(code)
        elseif not self.sfx_unavailable_logged then
            self.sfx_unavailable_logged = true
            log("[SLink-gen1] play_sound: native sounds off or no SFX-capable patch on this cartridge")
        end
    end

    -- A battle-held write handed to `deferred` later keeps its ARRIVAL position: appended, it
    -- fell behind a later memorialize for the same key, which buried the live mon and then
    -- dropped the faint ("key not in party"). Gen 3 684bbb7a fixed the same shape.
    -- ponytail: mirrors Gen 3's lua/core/deferred.lua Deferred:push; folds into it at the
    -- post-G4 convergence card (owner ruling), not a third queue.
    local function defer_held(entry)
        entry.arrival = entry.arrival or self.arrivals   -- an unstamped entry counts as newest
        for i, queued in ipairs(self.deferred) do
            if queued.arrival and queued.arrival > entry.arrival then
                table.insert(self.deferred, i, entry)
                return
            end
        end
        self.deferred[#self.deferred + 1] = entry
    end

    -- MINOR-7 (mirror of lua/gen2/client.lua)
    local function mark_dead(key, mon)
        self.dead_keys[key] = true
        if mon then self.dead_keys[mon_key(mon)] = true end
    end
    local function revived_dead()
        if not self.dead_synced then return nil end
        for key in pairs(self.dead_keys) do
            local slot, mon = find_party_slot(key)
            if slot and mon.hp > 0 then return key end
        end
    end

    function self:handle_command(cmd)
        local c = cmd.cmd
        if c == "noop" then return end
        self.arrivals = self.arrivals + 1
        cmd.arrival = self.arrivals
        if c == "force_faint" or c == "force_explode" then
            local slot, mon, party, why = find_party_slot(cmd.key)
            if why then log("[SLink-gen1] " .. c .. ": " .. why .. " " .. tostring(cmd.key)) return end
            if not party or not slot then
                -- unreadable right now (AddPartyMon's AskName window), or a key not in the party:
                -- deferred, never dropped on receipt (Gen 2 parity, 4e6aea39 MINOR-3; review
                -- 2026-09-24 MINOR-9). The checkpoint re-finds the key or drops it with a log
                -- (a server command is never resent).
                self.deferred[#self.deferred + 1] = { cmd = c, key = cmd.key, nickname = cmd.nickname, arrival = cmd.arrival }
                self.known_keys[cmd.key] = true
                return
            end
            local battle = reads.read_battle()
            if battle.in_battle ~= 0 then
                -- in battle. The ACTIVE battler waits for the loop head (W-2: its HP lives in
                -- wBattleMon). A BENCHED mon in a normal battle dies the frame the command
                -- arrives, so it can never be switched in from the battle menu (owner
                -- 2026-09-22): its party struct has no battle shadow, HasMonFainted refuses it
                -- (core.asm:1473-1482, .notAlreadyOut :2402-2404) and GainExperience skips HP 0
                -- (experience.asm:10-13). Explode = faint on the bench (EXPLOSION needs the field).
                -- O-30: the old man tutorial / Safari (wBattleType ~= 0) never send a mon out and
                -- never reach MainInBattleLoop (core.asm:164-207), so every slot is bench, slot 0
                -- too (wPlayerMonNumber is InitBattleVariables' zero), and receipt is the only
                -- in-battle landing. Link battles stay held (the other Game Boy would desync).
                -- ponytail: a refused write in a special battle has no loop head to retry at; the
                -- battle_end flush lands it at the checkpoint. Add a per-frame retry if that shows.
                local w = { cmd = c, key = cmd.key, nickname = cmd.nickname, arrival = cmd.arrival }
                if self.writes_enabled and (battle.in_battle == 1 or battle.in_battle == 2)
                   and battle.link_state ~= 4 and (battle.type ~= 0 or slot ~= battle.player_mon_number) then
                    local ok, err = pcall(function()
                        writes:arm("battle_bench")
                        writes:faint_party_slot(slot)
                    end)
                    writes:disarm()
                    if ok then
                        w.landed = true
                        log("[SLink-gen1] bench write landed on receipt: slot " .. slot .. " " .. tostring(cmd.key))
                        if not self.dead_keys[cmd.key] then hud.show("!! " .. nick_label(cmd.key, cmd.nickname) .. " KO'd", 255, 80, 80, 360) end
                        mark_dead(cmd.key, mon)
                    else
                        log("[SLink-gen1] bench write refused on receipt, the loop head retries: " .. tostring(err))
                    end
                end
                -- still queued when landed: the race where the player already picked this mon
                -- (wWhichPokemon passed HasMonFainted, core.asm:2402) but SwitchPlayerMon has not
                -- yet set wPlayerMonNumber / run LoadBattleMonFromParty (:2425, :2433) is settled
                -- at the loop head by the active-battler path
                self.pending_battle_writes[#self.pending_battle_writes + 1] = w
            else
                self.deferred[#self.deferred + 1] = { cmd = c, key = cmd.key, nickname = cmd.nickname, arrival = cmd.arrival }
            end
            self.known_keys[cmd.key] = true
            return
        end
        if c == "box_mon" or c == "party_mon" or c == "memorialize" then
            self.deferred[#self.deferred + 1] = cmd
            return
        end
        if c == "replace_rival_team" then self:replace_rival_team(cmd) return end
        if c == "msgbox" or c == "gui_prompt" then
            local r, g, b, f = hud_color(cmd)
            hud.prompt(cmd.text, r, g, b, f)
        elseif c == "hud_show" then
            local r, g, b, f = hud_color(cmd)
            hud.show(cmd.text, r, g, b, f)
        elseif c == "play_sound" then
            -- Gen 3 SE ids on the wire (docs/protocol.md); request_sfx_local applies the same
            -- native-SFX gate the local HUD moments below use.
            self:request_sfx_local(cmd.sound)
        elseif c == "resolved_areas" then
            self.resolved_areas = {}
            for _, a in ipairs(cmd.areas or {}) do self.resolved_areas[a] = true end
            self.seeded = true
        elseif c == "unresolve_area" then
            self.resolved_areas[cmd.area_id] = nil
        elseif c == "dead_keys" then
            self.dead_keys, self.dead_synced = {}, true
            for _, k in ipairs(cmd.keys or {}) do self.dead_keys[k] = true end
        elseif c == "key_change_ack" then
            -- A1: the rename is committed server-side; the old key stops being an alias
            local a = self.key_alias
            if a and a.old_key == cmd.old_key then
                self.known_keys[a.old_key] = nil
                self.key_alias = nil
            end
            self.retired_alias[cmd.old_key] = nil
        elseif c == "key_change_rejected" and Client.RETRYABLE_REJECTIONS[cmd.reason] then
            -- KEY-SCOPE-5: nothing was retired; the alias stays and the change is re-sent after the
            -- next complete box census (a census refusal asks for that census now)
            local a = self.key_alias
            if a and a.old_key == cmd.old_key then
                a.retry_gen = self.box_generation
                if cmd.reason == "box census unavailable" then self:rescan_boxes() end
            end
            log("[SLink-gen1] key_change refused (will retry): " .. tostring(cmd.reason) .. " " .. tostring(cmd.old_key))
        elseif c == "key_change_rejected" then
            -- U5 (a key collision): the server kills the pair itself (force_faint/memorialize
            -- follow); here the alias is dropped and the player is told why
            local a = self.key_alias
            if a and a.old_key == cmd.old_key then
                -- the cartridge cannot be rolled back (the DVs / species are already written), so
                -- the retirement the server queues under the OLD key has to find the mon by
                -- the key it physically holds now; cleared once its memorial lands
                self.retired_alias[a.old_key] = { new_key = a.new_key, evidence = a.evidence,
                                                  ambiguous = a.ambiguous, lost = a.lost }
                local party = current_party()
                if party then observe_alias(self.retired_alias[a.old_key], party) end
                self.key_alias = nil
            end
            log("[SLink-gen1] key_change rejected: " .. tostring(cmd.reason) .. " " .. tostring(cmd.old_key))
            hud.show("IDENTITY CHANGE REFUSED: " .. tostring(cmd.reason or "collision"), 255, 64, 64, 600)
        elseif c == "pending_keys" then
            -- A1: keys the server holds as pending captures; part of the APEX collision set
            self.pending_keys = {}
            for _, k in ipairs(cmd.keys or {}) do self.pending_keys[k] = true end
        elseif c == "config" then
            self.config = cmd
            -- a queued notification accepted under the old setting must not post after it
            if cmd.native_sounds ~= true and self.panel then self.panel:clear_sfx() end
        elseif c == "game_over" then
            -- Gen 3 parity (archive/gen3-old-client:lua/clients/gen3_frlge_client.lua:1026-1027): the server's game_over command
            -- carries no play_sound of its own (server/state.py:2103, :3202) -- the client
            -- supplies the cue locally, same as the whiteout detection below.
            self:request_sfx_local(26) -- SE_FAILURE, Gen 3's default SE_GAME_OVER (archive/gen3-old-client:lua/memory_gba.lua:1964)
            hud.set_game_over()
            self.game_over = true
        elseif c == "rebuild_start" then
            hud.set_rebuilding(cmd.text)
        elseif c == "rebuild_done" then
            hud.clear_rebuilding()
        elseif c == "trade_mask" then
            self:trade_answer_query(cmd.mask or 0)
        elseif c == "trade_offer_ack" then
            self:trade_answer_offer(cmd.ok == true)
        elseif c == "show_menu" and self.trade_enabled and cmd.blob_hex and cmd.slot ~= nil then
            self:trade_prompt(cmd)
        elseif c == "show_choices" or c == "show_menu" or c == "choose_mon" then
            ack_cancel(cmd) -- no native picker outside the receptionist flow
        elseif c == "withdraw_trade" then
            self:trade_withdraw(cmd)
        elseif c == "apply_prepare" then
            -- MAJOR-1 prepare round (mirror of lua/gen2/client.lua trade_prepare_answer): the
            -- service picks APPLY up anywhere, so ready = the mon is still here and no native
            -- trade is in flight; nothing is staged
            local st = self.trade_state
            local ok = self.trade_enabled == true and find_party_slot(cmd.old_key) ~= nil
                       and not (st and (st.kind == "apply" or st.kind == "prompt"))
            send("apply_ready", { token = cmd.token, ok = ok })
        elseif c == "apply_trade" then
            if self.trade_enabled then self:trade_apply(cmd)
            else
                -- no trade path on this cartridge: "nothing changed" with the pre-trade key (§5)
                local slot, mon = find_party_slot(cmd.old_key)
                owe("trade_done", { token = cmd.token, slot = slot or cmd.slot, new_key = cmd.old_key,
                                    new_species = mon and mon.species or 0 })
            end
        elseif c == "link_panel" then
            -- Held, not painted: the cartridge asks for the screen when the player opens the
            -- menu, and only the panel module knows whether that ask is still fresh.
            if self.panel then
                local pok, perr = self.panel:hold(cmd.rows)
                if not pok then log("[SLink-gen1] link_panel: " .. tostring(perr)) end
            end
        elseif c == "ghost_pos" then
            -- presentation the Gen 1 client does not render yet
        else
            log("[SLink-gen1] unknown command " .. tostring(c))
        end
    end

    -- A13: is `b` the battle `cmd` names, and is it still inside the window? Before the party is
    -- staged that is RIVAL_INIT_FRAMES from `battle_begin`; after it, RIVAL_STAGED_FRAMES from
    -- the staging. Measuring the whole thing from `battle_begin` is what made the window
    -- unsatisfiable: the staging itself can be 161 frames in.
    local function rival_window_live(b, trainer_id)
        if not b or b.cur_opponent ~= trainer_id then return false end
        if b.pos_staged then return (self.frame - b.pos_staged) <= Client.RIVAL_STAGED_FRAMES end
        return (self.frame - b.frame) <= Client.RIVAL_INIT_FRAMES
    end

    function self:replace_rival_team(cmd)
        local battle = reads.read_battle()
        local b = self.battle
        local live = rival_window_live(b, cmd.trainer_id)
        -- wIsInBattle is NOT the gate. `battle_begin` is hooked at InitBattleCommon offset 0
        -- (engine_signals.json:43-49) and `trainer_battle_start` goes out from that hook, so the
        -- server answers in the same round trip -- while pret is still inside
        -- DoBattleTransitionAndInitBattleVariables (core.asm:6680). `ld a, $2 / ld [wIsInBattle]`
        -- is at :6691-6692, one instruction AFTER the staging at :6689-6690, so an early reply
        -- legitimately reads 0. Refuse `not_in_battle` only when no initializing battle for this
        -- trainer is live either.
        if not live and (battle.in_battle == 0 or battle.cur_opponent ~= cmd.trainer_id) then
            send("rival_team_replaced", { trainer_id = cmd.trainer_id, species_ids = arr({}), error = "not_in_battle" })
            return
        end
        if not live then -- the right battle, but past its window
            send("rival_team_replaced", { trainer_id = cmd.trainer_id, species_ids = arr({}), error = "late_reply" })
            return
        end
        -- One replacement per battle. A duplicate reply (a server retry, or the same command
        -- delivered twice) would otherwise write the party a second time, and the second write
        -- races the engine for no gain: ack it and move no byte.
        if b.rival_applied then
            send("rival_team_replaced", { trainer_id = cmd.trainer_id, species_ids = arr({}), error = "already_applied" })
            return
        end
        -- The enemy party may only be rewritten between InitBattleCommon staging it
        -- (core.asm:6689-6690 sets wEnemyMonPartyPos = $FF) and EnemySendOutFirstMon calling
        -- LoadEnemyMonData (:1358), which sets it at :6055 after HP reads (:6046-6053). $FF means nobody has been sent out: write now.
        -- Not $FF and never staged means the transition is still running: park the reply and let
        -- rival_window_tick apply it at the frame the byte flips. Not $FF after it HAS been $FF
        -- means the engine already read the bytes we would replace.
        if io.read_u8(profile.ram.wEnemyMonPartyPos, "System Bus") ~= 0xFF then
            if b.pos_staged then
                send("rival_team_replaced", { trainer_id = cmd.trainer_id, species_ids = arr({}), error = "late_reply" })
                return
            end
            if self.pending_rival then -- one hold at a time; the displaced reply gets its answer
                send("rival_team_replaced", { trainer_id = self.pending_rival.trainer_id,
                                              species_ids = arr({}), error = "late_reply" })
            end
            self.pending_rival = cmd
            return
        end
        b.pos_staged = b.pos_staged or self.frame
        local mons, ids = {}, arr({})
        for i, hex in ipairs(cmd.blobs_hex or {}) do
            -- `#hex` on a server-supplied non-string raises outside the pcall below, and an
            -- error thrown here leaves the server with no answer at all: check the type first.
            if type(hex) ~= "string" or #hex ~= (d.battle_struct_size + 2 * d.name_length) * 2 then
                send("rival_team_replaced", { trainer_id = cmd.trainer_id, species_ids = arr({}), error = "bad_blob_length" })
                return
            end
            local bytes = hex_bytes(hex)
            local blob, ot, nick = {}, {}, {}
            local bs, nl = d.battle_struct_size, d.name_length
            for j = 1, bs do blob[j] = bytes[j] end
            for j = 1, nl do ot[j] = bytes[bs + j]; nick[j] = bytes[bs + nl + j] end
            mons[i] = { species = blob[1], blob = blob, ot = ot, nick = nick }
            ids[i] = blob[1]
        end
        local ok, why = pcall(function()
            writes:arm("battle_intro")
            writes:write_enemy_party(mons)
            writes:disarm()
        end)
        writes:disarm()
        if ok then
            b.rival_applied = true
            send("rival_team_replaced", { trainer_id = cmd.trainer_id, species_ids = ids })
        else send("rival_team_replaced", { trainer_id = cmd.trainer_id, species_ids = arr({}), error = tostring(why) }) end
    end

    -- A13: watch wEnemyMonPartyPos for the frame InitBattleCommon stages the party ($FF), both to
    -- remember that this battle's window HAS opened (so a reply after EnemySendOutFirstMon cleared
    -- the byte again is late, not early) and to release a reply that beat the staging. A parked
    -- reply is answered `late_reply` the frame the window closes -- by the send-out, or by the
    -- belt -- rather than sitting unanswered.
    local function answer_pending_late()
        local cmd = self.pending_rival
        if not cmd then return end
        self.pending_rival = nil
        send("rival_team_replaced", { trainer_id = cmd.trainer_id, species_ids = arr({}), error = "late_reply" })
    end

    function self:rival_window_tick()
        local b = self.battle
        if not b then return answer_pending_late() end
        local pos = io.read_u8(profile.ram.wEnemyMonPartyPos, "System Bus")
        if pos == 0xFF and not b.pos_staged and (self.frame - b.frame) <= Client.RIVAL_INIT_FRAMES then
            b.pos_staged = self.frame
            log("[SLink-gen1] RIVAL_WINDOW init_frames=" .. (b.pos_staged - b.frame))
            local cmd = self.pending_rival
            if cmd then self.pending_rival = nil; self:replace_rival_team(cmd) end
        elseif b.pos_staged and pos ~= 0xFF and not b.sendout_frame then
            -- The measurement the constants above are only lower bounds for. Logged once per
            -- battle, whatever the swap did, so the lane run records the real numbers.
            b.sendout_frame = self.frame
            log("[SLink-gen1] RIVAL_WINDOW init_frames=" .. (b.pos_staged - b.frame)
                .. " staged_frames=" .. (b.sendout_frame - b.pos_staged))
        end
        -- a parked reply is answered the frame the window shuts: by the send-out, or by the belt
        if self.pending_rival and (not rival_window_live(b, b.cur_opponent)
                                   or (b.pos_staged and pos ~= 0xFF)) then
            answer_pending_late()
        end
    end

    local function burial_waiting()
        for _, s in ipairs(self.box_settle) do if s.memorial then return true end end
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

    local function memorial_done(key, phys, nickname)
        send("memorialize_done", { key = key, box = box_count - 1 }); self:rescan_boxes()
        self.retired_alias[key] = nil
        self.dead_keys[key], self.dead_keys[phys] = nil, nil -- buried: MINOR-7 is done with it
        hud.show("† " .. nick_label(key, nickname) .. " buried", 255, 140, 40, 300)
    end

    -- Deferred queue: one command per frame, only at the verified overworld checkpoint.
    function self:run_deferred()
        local armed
        for i, s in ipairs(self.box_settle) do if s.armed then armed = armed or i end end
        if (#self.deferred == 0 and not armed and not (self.dead_synced and next(self.dead_keys))) or not self.writes_enabled
           or not net.connected() then return end
        local safe, why = safety.check(ws_profile, io)
        if not safe then return end
        if armed then
            -- the native save persisted the party: re-running party_mon is the interrupted-withdraw
            -- replay (boxes.lua), which drops the box copy on a full-record match; a reset before the
            -- save left the mon in the box only, and the replay is a no-op there. One op per checkpoint.
            -- Mirror of lua/gen2/client.lua run_deferred's settle.
            local s = table.remove(self.box_settle, armed)
            local ok, done, reason = pcall(function()
                writes:arm("overworld")
                if s.memorial then return self.boxes:settle_memorial(s.key) end
                return self.boxes:withdraw(s.key)
            end)
            writes:disarm()
            if s.memorial then
                -- BOX-MEMORIAL-2: a boxed memorial is acked only once a native save made it durable
                if ok and done and reason then
                    s.armed = false
                    self.box_settle[#self.box_settle + 1] = s
                    log("[SLink-gen1] memorial settle " .. s.key .. ": " .. tostring(reason))
                elseif ok and done then
                    memorial_done(s.memorial.key, s.key, s.memorial.nickname)
                else
                    log("[SLink-gen1] memorial settle refused: " .. tostring(ok and reason or done) .. " " .. s.memorial.key)
                    send("memorialize_failed", { key = s.memorial.key, reason = tostring(ok and reason or done) })
                end
                self:rescan_boxes()
                return
            end
            log("[SLink-gen1] box copy settle " .. s.key .. ": " .. (ok and done and "done" or tostring(reason or done)))
            self:rescan_boxes()
            return
        end
        local revived = revived_dead()
        if revived then table.insert(self.deferred, 1, { cmd = "force_faint", key = revived, quiet = true }) end
        if #self.deferred == 0 then return end
        local cmd = table.remove(self.deferred, 1)
        -- the key the cartridge holds for cmd.key and, for a retired alias, the validated slot:
        -- the box module takes both so a duplicate of the new key elsewhere cannot block the
        -- retirement; replies keep cmd.key, which is what the server tracks
        local r = self.retired_alias[cmd.key]
        local phys, hint, refused = r and r.new_key or cmd.key, nil, nil
        if r then
            local slot, _, _, why = find_party_slot(cmd.key)
            -- evidence failure is TERMINAL for an aliased command: the box module's own key
            -- lookup would find whichever record carries the duplicated key, so it is never
            -- consulted without a positive match (review cx-4f2e28f8)
            hint, refused = slot, (not slot) and (why or "retired record not found") or nil
        end
        local ok, err = pcall(function()
            writes:arm("overworld")
            if refused and (cmd.cmd == "box_mon" or cmd.cmd == "memorialize") then
                log("[SLink-gen1] " .. cmd.cmd .. " refused for the retired key: " .. refused .. " " .. tostring(cmd.key))
                send(cmd.cmd .. "_failed", { key = cmd.key, reason = refused })
            elseif cmd.cmd == "force_faint" or cmd.cmd == "force_explode" then
                local slot, mon, _, why = find_party_slot(cmd.key)
                -- MINOR-7: a death this client already landed (the server's O-24 re-issue) is quiet, and a
                -- mon still at HP 0 needs nothing more
                local quiet = cmd.quiet or self.dead_keys[cmd.key]
                if slot and quiet and mon.hp == 0 then
                    mark_dead(cmd.key, mon)
                elseif slot then
                    writes:faint_party_slot(slot)
                    mark_dead(cmd.key, mon)
                    -- Gen 3 parity (archive/gen3-old-client:lua/clients/gen3_frlge_client.lua:760-800): text only, never a local
                    -- SFX -- but not for one uniform reason (cx-6bedd222). A terminal/linked
                    -- battle faint's force_faint already carries a play_sound 26 to this player
                    -- (state.py:2884); the whiteout-driven retire loop (state.py:2073) and the
                    -- dead-key requeue after a buried key_change (state.py:2708) carry no sound
                    -- at all -- the whiteout case gets its own local cue from announce_whiteout
                    -- above instead, and the dead-key requeue is silent by design (a link
                    -- already resolved, not a new event). One banner rule covers all three.
                    if not quiet then hud.show("!! " .. nick_label(cmd.key, cmd.nickname) .. " KO'd", 255, 80, 80, 360) end
                else
                    -- the mon left the party before the checkpoint (PC deposit), or a duplicate
                    -- arrived and the key no longer names one mon: no byte moves. The protocol
                    -- has no force_faint NACK.
                    log("[SLink-gen1] " .. cmd.cmd .. " dropped at the checkpoint: "
                        .. (why or "key not in party") .. " " .. tostring(cmd.key))
                end
            elseif cmd.cmd == "box_mon" then
                local slot, mon = find_party_slot(cmd.key)
                if slot then send("stats_cache", { key = cmd.key, stats = { level = mon.level, maxHP = mon.max_hp } }) end
                -- `x and f()` keeps only f's first value: bind both explicitly
                local done, reason = nil, "no box module"
                if self.boxes then done, reason = self.boxes:deposit(phys, hint) end
                if not done then
                    send("box_mon_failed", { key = cmd.key, reason = reason or "deposit refused" })
                    hud.show("X Box fail: " .. nick_label(cmd.key, mon and mon.nickname), 255, 80, 80, 240)
                else
                    self:rescan_boxes()
                    hud.show("↓ " .. nick_label(cmd.key, mon and mon.nickname) .. " boxed", 100, 180, 255, 200)
                end
            elseif cmd.cmd == "party_mon" then
                -- the withdrawn mon's stats are rebuilt from the cartridge's own base stats
                local species
                for _, e in ipairs(self.box_cache) do if e.key == cmd.key then species = e.species_id end end
                local base = species and self.rom and self.rom.base_stats_for(species) or nil
                local done, reason = nil, "no box module"
                if self.boxes then
                    done, reason = self.boxes:withdraw(cmd.key, cmd.stats, base, cmd.nickname, { defer_backing = true })
                end
                if done and reason then
                    -- F1: the non-current box copy stays until save_witness (never a loss on a reset)
                    self.box_settle[#self.box_settle + 1] = { key = cmd.key, armed = false }
                    log("[SLink-gen1] party_mon " .. tostring(cmd.key) .. ": " .. reason)
                end
                if done then
                    send("sync_retrieve_done", { key = cmd.key })
                    self:rescan_boxes()
                    hud.show("↑ " .. nick_label(cmd.key, cmd.nickname) .. " unboxed", 100, 255, 160, 200)
                elseif reason == "party full" and (cmd.full_retries or 0) < (cmd.full_budget or (#self.deferred + 1)) then
                    -- Whiteout rebuild with a full party: the blackout HEALED the dead mons
                    -- (HealParty runs before the blackout site), the server queues the
                    -- rebuild's party_mon BEFORE the memorializes (state.py:2020-2049), and
                    -- sync_retrieve_failed is final there (:341-362). So the withdraw must
                    -- wait behind the memorializes that free a slot: to the TAIL, like the
                    -- refused memorialize below, bounded by the queue length seen at the
                    -- first refusal (every command behind it gets one turn per retry). A
                    -- party that is genuinely full still fails with "party full".
                    cmd.full_budget = cmd.full_budget or (#self.deferred + 1)
                    cmd.full_retries = (cmd.full_retries or 0) + 1
                    self.deferred[#self.deferred + 1] = cmd
                    log("[SLink-gen1] party_mon " .. tostring(cmd.key) .. ": party full, retry "
                        .. cmd.full_retries .. "/" .. cmd.full_budget .. " after the queue")
                else send("sync_retrieve_failed", { key = cmd.key, reason = reason or "withdraw refused" }) end
            elseif cmd.cmd == "memorialize" then
                local _, mem_mon = find_party_slot(cmd.key)
                local done, reason = nil, "no box module"
                if self.boxes then done, reason = self.boxes:memorialize(phys, hint) end
                if done and reason then
                    -- BOX-MEMORIAL-2: a boxed memorial touching the WRAM box waits for a native save; the ack
                    -- goes out from the settle after the save witness (mirror of lua/gen2/client.lua)
                    log("[SLink-gen1] memorialize " .. tostring(cmd.key) .. ": " .. tostring(reason))
                    self:rescan_boxes()
                    local queued = false
                    for _, s in ipairs(self.box_settle) do queued = queued or (s.memorial ~= nil and s.key == phys) end
                    if not queued then
                        self.box_settle[#self.box_settle + 1] = { key = phys, armed = false,
                                                                  memorial = { key = cmd.key, nickname = cmd.nickname } }
                        show_burial()
                    end
                elseif done then
                    memorial_done(cmd.key, phys, mem_mon and mem_mon.nickname)
                elseif reason == "last party mon" and self.game_over then
                    log("[SLink-gen1] memorialize dropped: last mon after game over")
                elseif reason == "last party mon" then
                    -- block until a party_mon lands -- at the TAIL, because the rebuild that
                    -- makes the memorial legal is itself queued behind this command; re-inserting
                    -- at the head starves it and the pair never recovers (A5)
                    self.deferred[#self.deferred + 1] = cmd
                else
                    -- INV-CLIENT-2: a key no longer in the party is forgotten; one still there stays dead
                    if not mem_mon then self.dead_keys[cmd.key], self.dead_keys[phys] = nil, nil end
                    send("memorialize_failed", { key = cmd.key, reason = reason or "memorial refused" })
                    hud.show("X Mem fail: " .. nick_label(cmd.key, mem_mon and mem_mon.nickname), 255, 80, 80, 300)
                end
            end
            writes:disarm()
        end)
        writes:disarm()
        if not ok then log("[SLink-gen1] deferred " .. tostring(cmd.cmd) .. " failed: " .. tostring(err)) end
    end

    -- ── signals → events ─────────────────────────────────────────────────────────────
    local function key_at(party, slot)
        for _, m in ipairs(party) do if m.slot == slot then return mon_key(m), m end end
    end
    local function holds_key(list, key)
        if not list or not key then return false end
        for _, m in ipairs(list) do if mon_key(m) == key then return true end end
        return false
    end

    -- The key an evolved mon HAD: same DVs and OT (key prefix "DDDD:OOOO:", reads.lua:185-188),
    -- a different species, a PARTY member on the last read before the site fired, and no
    -- longer anywhere in the party. Candidates come from that read, not from known_keys:
    -- known_keys also holds the PC (hello/rescan), and a boxed mon that shares the party mon's
    -- DVs and OT (the two starters of a duo run do) made the prefix ambiguous and the
    -- migration was refused, leaving the link on the dead key (Codex cross-review, FIX-EVO-2).
    -- Refuses (nil, why) rather than guessing when zero or several keys still answer.
    -- Accepted limit: with no party read before the signal's frame (the script was started
    -- during the animation, or the site fired on the client's first frame) the pool falls back
    -- to known_keys and a boxed mon with the same DVs and OT makes it ambiguous again; reading
    -- wEvoOldSpecies at the site would remove that case (queued: it needs a profile symbol).
    local function evolved_from(party, new_key, sig_frame)
        local prefix, found = new_key:sub(1, 10), nil
        local pool = party_keys_before(sig_frame) or self.known_keys
        for k in pairs(pool) do
            if k ~= new_key and k:sub(1, 10) == prefix and not holds_key(party, k) then
                if found then return nil, "ambiguous old key" end
                found = k
            end
        end
        if not found then return nil, "old key unknown" end
        return found
    end

    local function party_from_snapshot(bytes)
        -- decode the party-block snapshot a battle hook captured (party is stale for the active
        -- mon's HP at RemoveFaintedPlayerMon; the hook's battle_hp is authoritative for it)
        -- bytes[k + 1] is offset k from wPartyCount: count @0, species list @1..7, structs @8,
        -- OT names @8 + 6*44, nicknames after those (ram/wram.asm party block)
        local count = bytes[1]
        -- nil/garbage must come back as "undecoded", not as an error: the pcall around on_signal
        -- (frame_end) would swallow the whole signal and nothing would classify it at all
        if type(count) ~= "number" or count > d.party_capacity then return nil end
        local out = {}
        local structs = 1 + (d.party_capacity + 1)
        for slot = 0, count - 1 do
            local base = structs + slot * d.party_struct_size
            local raw = {}
            for i = 1, d.party_struct_size do raw[i] = bytes[base + i] end
            local mon = reads.decode_party_mon(raw, false)
            if not mon then return nil end
            mon.slot = slot
            local otb = structs + d.party_capacity * d.party_struct_size + slot * d.name_length
            mon.ot_name_bytes, mon.nickname_bytes = {}, {}
            for i = 1, d.name_length do
                mon.ot_name_bytes[i] = bytes[otb + i]
                mon.nickname_bytes[i] = bytes[otb + d.party_capacity * d.name_length + i]
            end
            out[#out + 1] = mon
        end
        return out
    end

    -- Gen 3 parity (archive/gen3-old-client:lua/clients/gen3_frlge_client.lua:3804-3816): the client's OWN whiteout detection
    -- gets a local cue -- the server's reply to `whiteout` (state.py _handle_whiteout) never
    -- queues a play_sound, so there is nothing to double up with.
    local function announce_whiteout()
        if self.whiteout_sent then return end
        self.whiteout_sent = true
        self:request_sfx_local(22) -- SE_BOO
        send("whiteout", {})
    end

    local function emit_faint(party, slot, area_id, hook_hp_zero_slot)
        local key = key_at(party, slot)
        if not key then return end
        send("faint", { key = key, area_id = area_id })
        -- whiteout: nothing alive once this faint is applied (HealParty runs before the
        -- blackout site, so the party bytes after the battle never show it)
        local alive = 0
        for _, m in ipairs(party) do
            if m.slot ~= slot and m.slot ~= hook_hp_zero_slot and m.hp > 0 then alive = alive + 1 end
        end
        if alive == 0 then announce_whiteout() end
    end

    function self:on_signal(sig)
        local k, pt = sig.kind, sig.point or {}
        local map_id = pt.map or reads.read_map().map
        local area_id, area_disp_name = area_of(map_id)
        if k == "battle_begin" or k == "wild_begin" then
            -- A wild encounter never sets wCurOpponent (both foundations stage the species in
            -- wEnemyMonSpecies2, engine/battle/wild_encounters.asm), so wild_begin is the wild
            -- witness and battle_begin (the trainer staging site) counts only with a trainer id
            -- >= derived.opp_id_offset (200 vanilla, 197 pureRGB). Live 2: InitBattleCommon
            -- fires once with wCurOpponent == 0 right after the starter pick (a non-battle
            -- caller) -- that is what the trainer-id gate ignores.
            local trainer = pt.cur_opponent >= opp_id_offset
            if k == "battle_begin" and pt.cur_opponent == 0 then
                log("[SLink-gen1] battle_begin with no opponent: ignored")
            elseif not self.battle or self.battle.frame ~= sig.frame then
                self.battle = { frame = sig.frame, wild = not trainer, species = pt.species,
                                level = pt.level, area_id = area_id, map = map_id,
                                cur_opponent = pt.cur_opponent, captured = false,
                                demo = DEMO_BATTLE_TYPES[pt.battle_type] and pt.battle_type or false }
                self.whiteout_sent = false
                if trainer then send("trainer_battle_start", { trainer_id = pt.cur_opponent }) end
                if self.battle.wild then
                    -- A scripted, fixed-species encounter owns its own slot and must not consume
                    -- the map's wild area: data/games/gen1_rby/static_encounters.json lists the
                    -- species per map, and the server recognises static_<map>_<dex>.
                    local ids = self.statics and self.statics[tostring(map_id)]
                    for _, s in ipairs(ids or {}) do
                        if s == pt.species then
                            local dex = self.rom.natdex(pt.species)
                            if dex then
                                self.battle.static = true
                                self.battle.area_id = "static_" .. tostring(map_id) .. "_" .. tostring(dex)
                            end
                            break
                        end
                    end
                    -- Gen 3 parity (archive/gen3-old-client:lua/clients/gen3_frlge_client.lua:2840-2852): flag a wild encounter in
                    -- an area this run hasn't resolved yet. self.battle.area_id already carries
                    -- the static-encounter override above, matching the id no_catch/capture key
                    -- off of; area_of() never returns a "gift_map_*" id (that prefix only exists
                    -- for a settled non-battle acquisition), so no separate gift check is needed.
                    -- Excluded like battle_end's own no_catch logic below (cx-6bedd222): a
                    -- static/scripted encounter (self.battle.static) and a demonstration battle
                    -- (self.battle.demo, Y-0) resolve nothing and are not the player's own
                    -- encounter; a Tower ghost without the Silph Scope can be neither fought nor
                    -- caught (the exact TOWER_MAPS/SILPH_SCOPE predicate used at battle_end).
                    if self.has_pokeballs and self.seeded and self.battle.area_id ~= ""
                       and not self.battle.static and not self.battle.demo
                       and not (TOWER_MAPS[self.battle.map] and reads.has_item(SILPH_SCOPE) ~= true)
                       and not self.resolved_areas[self.battle.area_id] then
                        hud.show("** NEW ENCOUNTER **\n" .. area_disp_name, 255, 220, 60, 360)
                        self:request_sfx_local(25) -- SE_SUCCESS
                    end
                end
            end
        elseif k == "battle_end" then
            -- EndOfBattle is after AddPartyMon/SendNewMonToBox returned. Settle the
            -- acquisition before deciding no_catch, even if its final writes and this
            -- hook drained in the same frame (pokered item_effects.asm:550-568).
            self:settle_pending_change(true)
            local b = self.battle
            local acquiring = self.pending_change and self.pending_change.kind == "acquire"
                              and self.pending_change.in_battle
            -- M2-b witness: EndOfBattle is the terminal site. A wild battle that ends without a
            -- capture is a failed encounter on BOTH foundations -- running away included (the
            -- deadzone rule; PLAN §2.3). pureRGB's wBattleFunctionalFlags RUN bit is recorded
            -- as the escape witness for the log/receipt, never as a reason to keep the area open.
            local ran = pt.functional_flags ~= nil and math.floor(pt.functional_flags / RAN_FLAG_BIT) % 2 == 1
            -- An in-battle acquisition that has not settled yet (the party can stay unreadable
            -- through the naming prompt and, on pureRGB, past EndOfBattle) is still a capture:
            -- whiteout_new on the pure lane sent no_catch a frame before the capture settled.
            if b and acquiring then b.captured = true end
            if b and b.demo then
                log("[SLink-gen1] demonstration battle (type " .. tostring(b.demo) .. "): nothing resolved")
            elseif b and b.wild and not b.captured and not acquiring and not self.resolved_areas[b.area_id] and b.area_id ~= "" then
                -- A Tower ghost without the Scope: the battle cannot be won or caught, so it
                -- is not evidence of a failed encounter. `has_item` returns nil when the bag
                -- cannot be read (reads.lua:200-207), and the safe reading of "cannot tell" is
                -- to leave the area unresolved rather than to dead-zone it on a guess.
                if TOWER_MAPS[b.map] and reads.has_item(SILPH_SCOPE) ~= true then
                    log("[SLink-gen1] tower ghost battle without the Silph Scope: no_catch suppressed")
                elseif not self.has_pokeballs then
                    -- D-2: nothing resolves before the first Poke Ball. The server has no ball
                    -- gate by design (test_state.py: the client is responsible), and the parcel
                    -- walk crosses Route 1 grass with an empty bag (ball_gate_new, 2026-09-17).
                    log("[SLink-gen1] no Poke Balls yet: no_catch withheld, " .. b.area_id .. " stays open")
                else
                    if ran then log("[SLink-gen1] player ran (wBattleFunctionalFlags): failed encounter in " .. tostring(b.area_id)) end
                    send("no_catch", { area_id = b.area_id, species_id = b.species, level = b.level })
                    self.resolved_areas[b.area_id] = true
                end
            end
            -- a battle write that never reached a loop head (the battle ended first) is still
            -- owed: the checkpoint zeroes it, instead of it waiting for some later battle
            for _, w in ipairs(self.pending_battle_writes) do
                -- a bench write that landed on receipt is re-zeroed as a backstop, silently
                defer_held({ cmd = "force_faint", key = w.key, nickname = w.nickname, quiet = w.landed, arrival = w.arrival })
            end
            self.pending_battle_writes = {}
            self.battle = nil
            self.pending_safe = true
        elseif k == "battle_faint" then
            local party = party_from_snapshot(pt.party or {})
            if party then emit_faint(party, pt.active_slot, area_id, pt.battle_hp == 0 and pt.active_slot or nil) end
        elseif k == "poison_faint" then
            local party = party_from_snapshot(pt.party or {})
            if party then emit_faint(party, pt.which, area_id, pt.which) end
        elseif k == "blackout" then
            announce_whiteout()
        elseif k == "bag_received" then
            local had_balls = self.has_pokeballs
            self.has_pokeballs = true
            if not had_balls then announce_nuzlocke_start() end
        elseif k == "add_party_mon" or k == "capture_box" then
            local loc = pt.mon_location or 0
            if k == "add_party_mon" and loc % 16 ~= 0 then
                -- ReadTrainer building the ENEMY party through the same routine: not ours
            elseif self.pending_change and (self.pending_change.kind == "npc_trade"
                                            or self.pending_change.kind == "daycare_withdraw") then
                -- the NPC trade's own append ($80, no naming) or the daycare's: that signal
                -- owns it and the readback settles it. wMonDataLocation == $80 alone is NOT the
                -- discriminator: Bill's Garden Pikachu is a wild capture with exactly $80
                -- (PLAN §4 row 10); a capture is wIsInBattle == 1.
            else
                -- Freshness witness (FIX-EVO-2, Codex cross-review): the record the engine is
                -- about to write lands in ONE known slot -- the party slot wPartyCount named at
                -- AddPartyMon entry (add_mon.asm:11-27 publish count and species before
                -- AskName), or box slot 0 (SendNewMonToBox shifts the box and inserts at the
                -- front, item_effects.asm:2649-2760). That slot's bytes at this instant are
                -- whatever was there before: after a deposit's compaction (remove_mon.asm:59-107)
                -- the old last record survives intact and, once its key has been retired by an
                -- evolution, reads as an unknown mon of the right species the moment the count
                -- is published. Only a record that DIFFERS from this snapshot is the catch.
                local slot = (k == "capture_box") and 0 or (pt.party_count or 0)
                local base, size
                if k == "capture_box" then base, size = profile.ram.wBoxMons, d.box_struct_size
                else base, size = profile.ram.wPartyMons + slot * d.party_struct_size, d.party_struct_size end
                self.pending_change = { kind = "acquire", frame = sig.frame, in_battle = pt.in_battle ~= 0,
                                        to_box = (k == "capture_box"),
                                        area_id = (self.battle and self.battle.static and self.battle.area_id) or area_id,
                                        map = pt.map, slot = slot, witness_base = base, witness_size = size,
                                        witness = hex_of(io.read_range(base, size, "System Bus")),
                                        -- stamped now: the demo battle can end before this settles
                                        demo = (self.battle and self.battle.demo) or false }
            end
        elseif k == "move_mon" then
            -- Every MoveMon is the PLAYER's: boxes.lua moves the client's own box bytes with
            -- io.write_bytes (:345-346,:455-456) and never calls _MoveMon, so a server-ordered
            -- box_mon/party_mon produces no signal to echo and needs no per-key suppression.
            -- A per-key mark that outlived its write instead swallowed the player's own Bill's
            -- PC deposit of a quarantined key (PC-1, whiteout_new receipt 2026-09-17).
            -- both sides come from the point's snapshot: _MoveMon has already copied the mon and
            -- _RemovePokemon shifts the rest down, so a live read here names the wrong slot (or
            -- none at all, when the last slot moved)
            if pt.move_type == MOVE_PARTY_TO_BOX or pt.move_type == MOVE_PARTY_TO_DAYCARE then
                local party = party_from_snapshot(pt.party or {})
                local key, mon
                if party then key, mon = key_at(party, pt.which) end
                if self.key_alias or next(self.retired_alias) then alias_departure(key) end
                if key then
                    send("party_to_box", { key = key, stats = { level = mon.level, maxHP = mon.max_hp } })
                end
            elseif pt.move_type == MOVE_DAYCARE_TO_PARTY and sites.daycare_withdraw then
                -- the pack pins the daycare site: it snapshots wDayCareMon itself (row 24)
            elseif pt.move_type == MOVE_BOX_TO_PARTY or pt.move_type == MOVE_DAYCARE_TO_PARTY then
                local box = reads.box_from_snapshot(pt.box or {})
                local key = box and key_at(box, pt.which)
                if key then send("box_to_party", { key = key, area_id = area_id }) end
            end
            local pk = self.pending_change and self.pending_change.kind
            if pk ~= "daycare_withdraw" then self.pending_change = { kind = "rescan", frame = sig.frame } end
        elseif k == "daycare_withdraw" then
            -- PLAN §4 row 24: MoveMon(DAYCARE_TO_PARTY) copies wDayCareMon and appends it at
            -- wPartyCount-1; the box is never touched. The key is read back at settle.
            local mon = pt.daycare and reads.decode_party_mon(pt.daycare, false) or nil
            self.pending_change = { kind = "daycare_withdraw", frame = sig.frame, area_id = area_id,
                                    expect_key = mon and mon_key(mon) or nil }
        elseif k == "remove_pokemon" then
            -- the native SLINK apply removes the offered mon through _RemovePokemon and appends
            -- the received one; trade_done accounts for both (receipt: a spurious party_to_box
            -- for the INCOMING key went out mid-apply and the server ordered a box_mon back)
            -- The removal happens EARLY in the apply (patch/gen1/src/native_trade.asm:157-158,
            -- vanilla cable_club.asm:799-800) and the DONE that ends the trade is published a
            -- hundred-odd frames later (:179-180 DelayFrames 100, :204 save, then
            -- trade_service.asm:106-113): only the APPLY state spans that gap, which is why this
            -- is a state test and not an age window.
            local pc0 = self.pending_change
            -- the commit boundary (mirror of Gen 2's SlinkTradeCommit latch, trade_overlay
            -- commit_entered): this RemovePokemon is the apply's first mutation, after every
            -- .refused check (native_trade.asm:156-158); from here a missing DONE is uncertain
            if self.trade_state and self.trade_state.kind == "apply" then self.trade_state.committing = true end
            local trading = (self.trade_state and self.trade_state.kind == "apply")
                            -- an NPC trade owns this removal: vanilla's repinned npc_trade site
                            -- (before RemovePokemon) and pureRGB's npc_trade_remove alike (row 25)
                            or (pc0 and pc0.kind == "npc_trade") or false
            -- What separates a removal that finishes a MoveMon from a standalone one is WHERE
            -- THE MON IS, not how many frames ago the MoveMon fired: _MoveMon runs to completion
            -- before RemovePokemon is called, and both collections are in this point's snapshot.
            -- The frame distance is not usable — pc_ops_new (2026-09-17) drained the withdraw's
            -- two hooks in different frame_end batches and the 2-frame age window called Bill's
            -- WITHDRAW a release.
            local party = party_from_snapshot(pt.party or {})
            local box = reads.box_from_snapshot(pt.box or {})
            local moved = self.moved_this_frame and (self.frame - self.moved_this_frame) <= 2
            local key
            if pt.from_box then key = box and key_at(box, pt.which)
            else key = party and key_at(party, pt.which) end
            if not pt.from_box and (self.key_alias or next(self.retired_alias)) then alias_departure(key) end
            if not party or not box then
                -- Both collections must DECODE before an absence can mean anything: a nil party
                -- would make Bill's WITHDRAW look like a release, a nil box would make a deposit
                -- look standalone. The signal is consumed here either way -- a later snapshot is
                -- a different instant and must never be used to re-classify this removal.
                log(string.format("[SLink-gen1] STORAGE_CLASSIFICATION_UNAVAILABLE key=%s why=%s",
                                  key or "?", (not party) and "party-undecoded" or "box-undecoded"))
            elseif pt.from_box then
                -- Bill's WITHDRAW ends in RemovePokemon(from_box) too, right after its
                -- BOX_TO_PARTY MoveMon (bills_pc.asm:282-287), so `from_box` alone is not the
                -- discriminator. At this hook (site offset 0, home/move_mon.asm:20-21 jpfar
                -- _RemovePokemon -- nothing has been shifted yet) the key is still in the BOX
                -- either way; what tells them apart is that the WITHDRAW's MoveMon has already
                -- installed it in the PARTY and a RELEASE (bills_pc.asm:310-312) has not. Owner
                -- ruling O-35 (server 8f662994): the release loses the mon, so release{key} goes on
                -- the wire; the server kills a linked partner and ignores an unlinked key. Gen 1's
                -- PC releases from the box only. Mirrored by lua/gen2/client.lua pc_release.
                if key and not holds_key(party, key) and not trading then
                    log(string.format("[SLink-gen1] RELEASE_SEEN key=%s box=%d", key, (pt.box_num or 0) % 128))
                    self.dead_keys[key] = nil -- the mon is gone: a later mon under this key is another mon
                    owe("release", { key = key }) -- INV-CLIENT-2: durable, like a trade report
                end
            else
                -- from the SNAPSHOT, like the sibling branches: _RemovePokemon has already shifted
                -- the rest of the party down by drain time, so a live read names the mon that
                -- moved INTO the slot (engine/events/in_game_trades.asm:145,
                -- engine/link/cable_club.asm:800 are the two standalone callers).
                -- A DEPOSIT's MoveMon (bills_pc.asm:230-235) has already appended the mon to the
                -- box, so its key is in the box snapshot and `move_mon` has reported it already.
                -- PARTY_TO_DAYCARE (scripts/Daycare.asm:51-56) leaves it in neither collection,
                -- which is what the MoveMon pairing still covers.
                if key and not holds_key(box, key) and not moved and not trading then
                    send("party_to_box", { key = key }) -- release
                end
            end
            -- ponytail: key membership, not a handshake. The key is DVs:OT:species only
            -- (reads.lua:185-188 -- nickname and level are not in it), so a 1/65536 identity
            -- collision misreads BOTH directions: releasing a boxed mon whose key also names a
            -- party mon logs nothing, and a standalone party removal (in_game_trades.asm:143-148,
            -- cable_club.asm:799-800) of a key that an unrelated boxed mon shares is swallowed as
            -- a completed deposit. Pair the two signals by wWhichPokemon if a receipt shows one.
            -- a live npc_trade/evolution still owes a key_change; RemovePokemon runs INSIDE the
            -- NPC trade (in_game_trades.asm:145), so a rescan here would swallow it
            local pk = self.pending_change and self.pending_change.kind
            if pk ~= "npc_trade" and pk ~= "evolution" and pk ~= "transform" and pk ~= "apex_chip" then
                self.pending_change = { kind = "rescan", frame = sig.frame }
            end
        elseif k == "evolve" then
            -- Evolution_PartyMonLoop AFTER the species is published: `ld a,[wLoadedMonSpecies] /
            -- ld [hl],a` rewrote wPartySpecies[wWhichPokemon] (evos_moves.asm:229-233 R/B,
            -- :231-235 Y) and the struct copy landed before it (:178-204). Every path runs this
            -- loop -- level-up (EndOfBattle -> predef EvolutionAfterBattle, end_of_battle.asm:42-45,
            -- which enters at 0E:6D1C and never passes TryEvolvingMon at 0E:6D0E), stone, Rare
            -- Candy and trade (TryEvolvingMon) -- and a cancelled evolution (B pressed,
            -- `jp c, CancelledEvolution` at :135) leaves before this site, so nothing pends and
            -- nothing needs a frame budget. The snapshot already holds the NEW record; the old
            -- key is the known key with the same DVs and OT (both untouched by EvolveMon) that
            -- is no longer in the party.
            -- An NPC trade can evolve its appended recipient; its completion hook
            -- owns that final identity, rather than a second overlapping migration.
            if self.pending_change and self.pending_change.kind == "npc_trade" then return end
            -- A SLINK trade evolves natively inside the apply; trade_done reports the final
            -- key of the last slot and a migration beside it would be a lie (S-5).
            if self.trade_state and self.trade_state.kind == "apply" then return end
            local party = party_from_snapshot(pt.party or {})
            local key, mon
            if party then key, mon = key_at(party, pt.which) end
            if key then
                local old, why = evolved_from(party, key, sig.frame)
                if old then
                    -- A1 alias-until-ack (PLAN 5.2): BOTH keys stay known until key_change_ack /
                    -- _rejected; the frozen record evidence and the ambiguity latch are what a
                    -- rejected change is retired by (review cx-6aacc4f1 .. cx-71b0f866)
                    self.known_keys[key] = true
                    self.key_alias = { old_key = old, new_key = key, evidence = record_evidence(mon), since = sig.frame }
                    observe_alias(self.key_alias, party)
                    -- snapshot records carry name BYTES only (party_from_snapshot), so decode here
                    self.key_alias.msg = { old_key = old, new_key = key, new_species = mon.species,
                                           reason = "evolution", new_nickname = reads.decode_name(mon.nickname_bytes) }
                    send("key_change", self.key_alias.msg)
                else
                    log("[SLink-gen1] evolution of slot " .. tostring(pt.which) .. " (" .. key .. "): " .. why)
                end
            end
            self.pending_change = { kind = "rescan", frame = sig.frame }
        elseif k == "npc_trade" and not sites.npc_trade_remove then
            -- vanilla: the site sits after the selection, immediately before RemovePokemon
            local party = party_from_snapshot(pt.party or {})
            local key = party and key_at(party, pt.which)
            if key then
                self.pending_change = { kind = "npc_trade", frame = sig.frame,
                                        count = #party, old_key = key }
            end
        elseif k == "npc_trade_remove" then
            -- pureRGB (PLAN §4 row 25): the selection is final only at the RemovePokemon call,
            -- the party is compacted and the received mon is APPENDED: identity here, readback
            -- of the last slot at npc_trade_done
            local party = party_from_snapshot(pt.party or {})
            local key = party and key_at(party, pt.which)
            if key then
                self.pending_change = { kind = "npc_trade", frame = sig.frame, slot = pt.which, old_key = key,
                                        readback_last_slot = true }
            end
        elseif k == "npc_trade_done" then
            local pc = self.pending_change
            if not pc or pc.kind ~= "npc_trade" then return end
            if pc.readback_last_slot then
                -- pureRGB: the readback settles it (settle_pending_change)
                pc.done, pc.frame = true, sig.frame
                return
            end
            local party = party_from_snapshot(pt.party or {})
            local mon = party and #party == pc.count and party[#party] or nil
            if not mon then
                log("[SLink-gen1] NPC trade completion unreadable; key migration refused")
                self.pending_change = nil
                return
            end
            -- RemovePokemon compacts; AddPartyMon appends. The final slot, not
            -- the selected slot, is the received mon (pokered in_game_trades.asm:232-239).
            local key = mon_key(mon)
            self.known_keys[key] = true
            self.key_alias = { old_key = pc.old_key, new_key = key, evidence = record_evidence(mon), since = sig.frame }
            observe_alias(self.key_alias, party)
            self.key_alias.msg = { old_key = pc.old_key, new_key = key, new_species = mon.species,
                                   reason = "npc_trade", new_nickname = reads.decode_name(mon.nickname_bytes) }
            send("key_change", self.key_alias.msg)
            self.pending_change = { kind = "rescan", frame = sig.frame }
        elseif k == "transform" then
            -- A2: recorded synchronously by on_transform; the key change settles after the
            -- routine, the HP re-zero is queued there when the mon was dead
            local t = self.transforming
            if t and t.frame == sig.frame then
                self.pending_change = { kind = "transform", frame = sig.frame, slot = t.slot,
                                        old_key = t.old_key, old_hp = t.old_hp }
            end
        elseif k == "save_witness" then
            if io.saveram then pcall(io.saveram) end
            for _, s in ipairs(self.box_settle) do s.armed = true end  -- gen1-box-durability
        elseif k == "starter_begin" or k == "starter_end" or k == "battle_loop_head" then
            -- consumed inside the hook (battle_loop_head) or informational (starter)
        end
        if k == "move_mon" then self.moved_this_frame = sig.frame end
    end

    -- A signal said the party/boxes changed; read the outcome once, when the engine is done.
    function self:settle_pending_change(acquisition_complete)
        local pc = self.pending_change
        if not pc or (self.frame <= pc.frame and not acquisition_complete) then return end
        -- AskName runs BEFORE the record exists on both paths (pokered/pokeyellow
        -- add_mon.asm:45-53; pokered item_effects.asm:2737-2743). Human input has
        -- no deadline. This is one bounded pending slot, cleared on reset or the
        -- next engine operation, not an accumulating queue needing a frame cap.
        -- only npc_trade_done owns completion; on pureRGB it marks the readback (row 25) and
        -- the KEY_CHANGE_REASON branch below settles it from the last slot
        if pc.kind == "npc_trade" and not pc.readback_last_slot then return end
        if pc.kind ~= "acquire" and self.frame - pc.frame > Client.MAX_PENDING_FRAMES then
            self.pending_change = nil return
        end
        local party = current_party()
        if not party then return end
        if pc.kind == "acquire" then
            -- The catch is the record in the witnessed slot (on_signal above), and while the
            -- player may still be on the naming screen only once those bytes have moved: a
            -- stale record that merely became "unknown" is not one. Equality does not prove
            -- the write did not happen -- a re-caught mon whose 44 bytes match the stale
            -- record exactly (same species, DVs, OT, level, zero stat exp, moves/PP, HP; the
            -- nickname is outside the struct) is real and rare -- so the veto is NOT
            -- permanent: at battle_end (acquisition_complete) the engine has returned from
            -- AddPartyMon, and the record in the slot is accepted on the readiness checks
            -- alone (Codex cross-review of da2cf11, FIX-EVO-3).
            if pc.witness and not acquisition_complete
               and hex_of(io.read_range(pc.witness_base, pc.witness_size, "System Bus")) == pc.witness then
                return -- the slot still holds what was there at the signal
            end
            local found = nil
            local list = pc.to_box and reads.read_active_box() or party
            -- A1 keeps a changed mon's OLD key known until the server acknowledges the
            -- key_change; for the acquisition witness that key is already retired (FIX-EVO-3:
            -- a re-catch into the stale slot must be reported), so it does not count as known.
            local alias_old = self.key_alias and self.key_alias.old_key
            if list then
                for _, m in ipairs(list) do
                    local mk = mon_key(m)
                    if (pc.slot == nil or m.slot == pc.slot) and (not self.known_keys[mk] or mk == alias_old) then found = m end
                end
            end
            if not found then return end -- not written yet; try next frame
            -- add_mon.asm:58-243 writes the struct AFTER the AskName prompt (:45-52), in the
            -- order species, DVs, moves, OT, exp, EVs, PP, level, stats, and a frame boundary
            -- can fall inside that run (ball_gate_new receipts 2026-09-17: OT 0000, level 0).
            -- Level and stats are the last writes: require them, and the same key on two
            -- consecutive frames, before the mon is reported.
            local key = mon_key(found)
            if found.level == 0 or (not pc.to_box and found.max_hp == 0) or (not acquisition_complete and pc.candidate ~= key) then
                pc.candidate = key
                return
            end
            self.known_keys[key] = true
            if pc.demo then
                -- Y-0: the mon Oak (or the old man) catches during a demonstration is not the
                -- player's capture and its map is not resolved by it. Known, but not reported.
                log("[SLink-gen1] demonstration battle: capture of " .. key .. " not reported")
                self.pending_change = { kind = "rescan", frame = self.frame }
                return
            end
            local gift = not pc.in_battle
            local area_id = pc.area_id
            if gift then area_id = "gift_map_" .. tostring(pc.map)
            elseif self.battle and self.battle.static then area_id = self.battle.area_id end
            send("capture", { key = key, area_id = area_id, species_id = found.species, level = found.level,
                              hp = found.hp, maxHP = found.max_hp ~= reads.NULL and found.max_hp or nil,
                              nickname = found.nickname, gift = gift, in_box = pc.to_box,
                              stats = { level = found.level, maxHP = found.max_hp ~= reads.NULL and found.max_hp or nil } })
            if self.battle and not gift then self.battle.captured = true end
            self.resolved_areas[area_id] = true
            self.pending_change = { kind = "rescan", frame = self.frame }
        elseif KEY_CHANGE_REASON[pc.kind] then
            -- M2-b: never reconcile a key while Cable Club link code rewrites party bytes
            if reads.read_battle().link_state ~= 0 then return end
            local key, mon
            if pc.readback_last_slot then
                if not pc.done then
                    if self.frame - pc.frame > 300 then self.pending_change = nil end -- declined
                    return
                end
                mon = party[#party]
                key = mon and mon_key(mon)
            else
                key, mon = key_at(party, pc.slot)
            end
            if not key then self.pending_change = nil return end
            if key ~= pc.old_key then
                -- A1 alias-until-ack: BOTH keys stay known until key_change_ack/_rejected
                self.known_keys[key] = true
                self.key_alias = { old_key = pc.old_key, new_key = key, evidence = record_evidence(mon), since = self.frame }
                observe_alias(self.key_alias, party)   -- a twin present now latches ambiguity for good
                self.key_alias.msg = { old_key = pc.old_key, new_key = key, new_species = mon.species,
                                       reason = KEY_CHANGE_REASON[pc.kind], new_nickname = mon.nickname }
                send("key_change", self.key_alias.msg)
                if pc.kind == "transform" and pc.old_hp == 0 then
                    -- A2 backstop: the in-hook zero is unproven (T2 gate); the checkpoint
                    -- re-zero through the ordinary deferred path never revives a dead mon
                    self.deferred[#self.deferred + 1] = { cmd = "force_faint", key = key }
                end
                self.pending_change = nil
            elseif self.frame - pc.frame > 300 then
                self.pending_change = nil -- trade declined / evolution cancelled
            end
        elseif pc.kind == "daycare_withdraw" then
            -- row 24 readback: the appended mon is the LAST party entry, never a box slot
            local mon = party[#party]
            local key = mon and mon_key(mon)
            if not key or (pc.expect_key and key ~= pc.expect_key) then
                if self.frame - pc.frame > 300 then self.pending_change = nil end
                return
            end
            self.known_keys[key] = true
            send("box_to_party", { key = key, area_id = pc.area_id })
            self.pending_change = { kind = "rescan", frame = self.frame }
        elseif pc.kind == "rescan" then
            self:rescan_boxes()
            for _, m in ipairs(party) do self.known_keys[mon_key(m)] = true end
            for _, e in ipairs(self.box_cache) do self.known_keys[e.key] = true end
            self.pending_change = nil
        end
    end

    -- O-30 review MAJOR-1/2 (mirror of lua/gen2/client.lua lift_deferred_deaths): a death deferred
    -- before the battle (a script window refused the checkpoint) and a quiet re-zero whose mon was
    -- revived with no checkpoint in between land at this loop head, not at battle end. A quiet
    -- entry rides as `landed` (no second banner, no explode); one still at HP 0 stays deferred.
    local function lift_deferred_deaths()
        local stay = {}
        for _, e in ipairs(self.deferred) do
            local slot, mon
            if e.cmd == "force_faint" or e.cmd == "force_explode" then slot, mon = find_party_slot(e.key) end
            if slot and not (e.quiet and mon.hp == 0) then
                e.landed = e.quiet
                self.pending_battle_writes[#self.pending_battle_writes + 1] = e
            else
                stay[#stay + 1] = e
            end
        end
        self.deferred = stay
        -- MINOR-7: a dead key revived since its death landed (a heal, then a battle) dies here too, as `landed`
        for key in pairs(self.dead_synced and self.dead_keys or {}) do
            local queued = false
            for _, w in ipairs(self.pending_battle_writes) do queued = queued or w.key == key end
            local slot, mon = find_party_slot(key)
            if slot and mon.hp > 0 and not queued then
                self.pending_battle_writes[#self.pending_battle_writes + 1] = { cmd = "force_faint", key = key, landed = true,
                                                                               arrival = self.arrivals }
            end
        end
    end

    -- Inside the MainInBattleLoop hook: apply the queued in-battle faints/explodes now (W-2).
    -- Returns true when a byte moved (the pureRGB no-move re-entry moves PC only then).
    function self:on_battle_loop_head(sig)
        if (#self.pending_battle_writes == 0 and #self.deferred == 0 and not (self.dead_synced and next(self.dead_keys)))
           or not self.writes_enabled then return false end
        local pt = sig.point
        -- an unreadable party keeps the queue: find_party_slot could not tell "gone" from
        -- "not readable yet", and a dropped in-battle write never comes back
        if not current_party() then return false end
        -- link battles never write in battle; a special battle never reaches this hook natively
        if pt.link_state ~= 4 and pt.battle_type == 0 then lift_deferred_deaths() end
        if #self.pending_battle_writes == 0 then return false end
        local keep, wrote = {}, false
        for _, w in ipairs(self.pending_battle_writes) do
            -- the same ambiguity-aware resolver the checkpoint uses: this one used to take the
            -- LAST match where find_party_slot took the first, so one duplicate made the two
            -- paths faint different mons (D-13)
            local slot, mon, _, why = find_party_slot(w.key)
            if why then
                log("[SLink-gen1] battle write dropped: " .. why .. " " .. tostring(w.key))
            elseif slot and slot == pt.active_slot then
                local battle = { in_battle = pt.in_battle, type = pt.battle_type, link_state = pt.link_state,
                                 player_mon_number = pt.active_slot, battle_species = pt.battle_species,
                                 transformed = math.floor(pt.status3 / TRANSFORMED_BIT) % 2 == 1 }
                local ok = writes.active_faint_guard(battle, slot, mon)
                if ok then
                    writes:arm("battle_loop_head")
                    -- a landed bench write that got switched in anyway is already dead: it
                    -- does not get to explode
                    if w.cmd == "force_explode" and not w.landed then writes:explode_active_battler(slot) else writes:faint_active_battler(slot) end
                    writes:disarm()
                    wrote = true
                    -- Gen 3 parity (archive/gen3-old-client:lua/clients/gen3_frlge_client.lua:760-800): same text-only banner as
                    -- the bench-mon write in run_deferred above.
                    local quiet = w.landed or self.dead_keys[w.key]
                    mark_dead(w.key, mon)
                    if not quiet then hud.show("!! " .. nick_label(w.key, w.nickname) .. " KO'd", 255, 80, 80, 360) end
                else
                    keep[#keep + 1] = w
                end
            elseif slot and pt.battle_type == 0 and pt.link_state ~= 4 then
                -- a bench mon (EXPLOSION needs the field, so explode = faint): the loop head is
                -- between turns, nothing in the engine holds its party struct. One landed on
                -- receipt and still at HP 0 needs nothing more.
                if not (w.landed and mon.hp == 0) then
                    writes:arm("battle_loop_head")
                    writes:faint_party_slot(slot)
                    writes:disarm()
                    wrote = true
                    local quiet = w.landed or self.dead_keys[w.key]
                    mark_dead(w.key, mon)
                    if not quiet then hud.show("!! " .. nick_label(w.key, w.nickname) .. " KO'd", 255, 80, 80, 360) end
                end
            elseif slot then
                -- link battle: leave the bench alone until the checkpoint (a special battle never
                -- reaches this hook natively; its bench write landed on receipt, O-30)
                defer_held({ cmd = "force_faint", key = w.key, nickname = w.nickname, arrival = w.arrival })
            end
        end
        self.pending_battle_writes = keep
        return wrote
    end

    -- pureRGB (site battle_loop_no_move): the MOVE menu's cancel path re-enters the loop below
    -- the HP check, so the loop-head write would wait a whole committed turn (and a natural KO
    -- could pre-empt it). Land the same write here and move PC back to the loop head (+6, the
    -- HP check): same stack frame, no instruction of .loopNoMoveSelected has run yet. Proven
    -- live 2026-09-18 (lua/tests/probe_gen1_loop_reentry.lua -> HandlePlayerMonFainted).
    function self:on_battle_loop_no_move(sig)
        if #self.pending_battle_writes == 0 or not self.writes_enabled or not io.set_register then return end
        local head = sites.battle_loop_head
        if not head then return end
        if self:on_battle_loop_head(sig) then
            io.set_register("PC", head.address + (head.capture_offset or 0))
            log("[SLink-gen1] battle write landed at the no-move re-entry; PC moved to the loop head")
        end
    end

    -- ── APEX CHIP (pureRGB, PLAN A1) and transformations (A2): synchronous hook handlers ──
    -- The live key set an APEX use must not collide with: party + every scanned box +
    -- server-seeded pending captures + the held alias, minus the mon's own current key.
    local function collision_set(own_key)
        local set = {}
        for k in pairs(self.known_keys) do set[k] = true end
        for k in pairs(self.pending_keys) do set[k] = true end
        if self.key_alias then set[self.key_alias.old_key], set[self.key_alias.new_key] = true, true end
        set[own_key] = nil
        return set
    end

    -- .setDVs, before the two $FF stores: HL names the slot's first DV byte, the target is
    -- wUsedItemOnWhichPokemon. Predict the FFFF:OTID:SS key and whether it is already live.
    function self:on_apex_preflight(sig)
        local pt = sig.point
        self.apex = nil
        local party = party_from_snapshot(pt.party or {})
        local key, mon = nil, nil
        if party then key, mon = key_at(party, pt.target) end
        if not key then return end
        local new_key = string.format("%04X:%04X:%02X", 0xFFFF, mon.ot_id, mon.species)
        local dvs = io.read_range(pt.hl, 2, "System Bus")
        self.apex = { slot = pt.target, dv_addr = pt.hl, dvs = { dvs[1], dvs[2] }, old_key = key,
                      new_key = new_key, species = mon.species, ot_id = mon.ot_id,
                      collide = (key ~= new_key) and (collision_set(key)[new_key] == true) or false }
    end

    -- +$11, both DV bytes written, before `call .recalculateStats`: restore on a predicted
    -- collision (the chip is still consumed, U6); otherwise the key change settles after.
    function self:on_apex_commit(sig)
        local a = self.apex
        self.apex = nil
        if not a or a.slot ~= sig.point.target then return end
        local party = party_from_snapshot(sig.point.party or {})
        local _, mon = nil, nil
        if party then _, mon = key_at(party, a.slot) end
        if not mon or mon.species ~= a.species or mon.ot_id ~= a.ot_id then return end
        if a.collide then
            if not self.writes_enabled then return end
            writes:arm("apex_commit", function(addr, n) return addr == a.dv_addr and n == 2 end)
            local ok, why = pcall(function() writes:restore_apex_dvs(a.dv_addr, a.dvs) end)
            writes:disarm()
            if ok then
                log("[SLink-gen1] APEX refused: " .. a.new_key .. " already live; DVs restored for " .. a.old_key)
                hud.show("APEX CHIP REFUSED: IDENTITY COLLISION", 255, 64, 64, 600)
            else
                log("[SLink-gen1] APEX DV restore failed: " .. tostring(why))
            end
        elseif a.old_key ~= a.new_key then
            self.pending_change = { kind = "apex_chip", frame = sig.frame, slot = a.slot, old_key = a.old_key }
        end
    end

    -- ChangePartyPokemonSpecies+0: remember the slot, its key and its HP before the rewrite.
    function self:on_transform(sig)
        local pt = sig.point
        local party = party_from_snapshot(pt.party or {})
        local key = party and key_at(party, pt.which)
        self.transforming = key and { slot = pt.which, old_key = key, old_hp = pt.old_hp, frame = sig.frame } or nil
    end

    -- +$4C, the low-byte HP store (HL names it; the high byte at HL-1 is already stored): a
    -- mon that was at 0 HP goes back to 0 right here. †UNVERIFIED that the low byte survives
    -- the store that follows the callback (T2 gate); settle queues the checkpoint re-zero too.
    function self:on_transform_hp_lo(sig)
        local t = self.transforming
        if not t or t.old_hp ~= 0 or not self.writes_enabled then return end
        local addr = sig.point.hl - 1
        writes:arm("transform", function(a, n) return a == addr and n == 2 end)
        local ok, why = pcall(function() writes:restore_transform_hp_zero(addr) end)
        writes:disarm()
        if not ok then log("[SLink-gen1] transform HP zero failed: " .. tostring(why)) end
    end

    -- ── hello / tick ─────────────────────────────────────────────────────────────────
    function self:send_hello(expected_identity)
        -- No argument = master's direct call (live gates): hello now, and the session counts it.
        if expected_identity == nil then
            local sent, why = self.hello_session:send_now(io.framecount())
            if sent then self.hello_sent = true end
            return sent, why
        end
        if hello_identity() ~= expected_identity then
            return false, "hello identity changed or unavailable"
        end
        local party, battle = snapshot_party()
        if not party then return false, "hello party snapshot unavailable" end
        local map = reads.read_map()
        local area_id, loc = area_of(map.map)
        local had_balls = self.has_pokeballs
        self.has_pokeballs = self.has_pokeballs or ball_count() > 0
        -- A hello snapshot with balls already in the bag is a resume/reconnect, not the
        -- moment of acquisition (archive/gen3-old-client:lua/clients/gen3_frlge_client.lua:1921-1926): log only, no banner.
        if not had_balls and self.has_pokeballs then
            self.nuzlocke_announced = true
            log("[SLink-gen1] nuzlocke ACTIVE (pokeballs already in bag at startup)")
        end
        self:rescan_boxes()
        for _, e in ipairs(party or {}) do self.known_keys[e.key] = true end
        for _, e in ipairs(self.box_cache) do self.known_keys[e.key] = true end
        local rom_content
        if self.rom and self.rom.rom_content then
            local ok, result = pcall(self.rom.rom_content)
            if ok then
                rom_content = result
                -- What the area-entry banner below can see: the cartridge's OWN wild-data
                -- table (rom.lua:147, keyed by map id, populated only where the wild rate is
                -- nonzero). The server's gift-area list (server/adapters/gen1_rby.py's
                -- _GIFT_AREAS) is not on the wire and is not a proxy for this: oaks_lab (map
                -- 40) is a real, non-gift-prefixed area_id with no wild table (cx-6bedd222).
                self.wild_maps = result.wild
            elseif not self.rom_content_error_logged then
                log("[SLink-gen1] rom_content unavailable: " .. tostring(result))
                self.rom_content_error_logged = true
            end
        end
        local payload = {
            rom_type = self.rom_type, foundation = self.foundation, artifact_kind = self.artifact_kind,
            instrumentation_key = SLINK_INSTRUMENTATION_KEY or os.getenv("SLINK_INSTRUMENTATION_KEY"),
            party = party or arr({}), ot_id = reads.read_player_id(),
            trainer_name = reads.read_player_name(), has_pokeballs = self.has_pokeballs,
            ball_count = ball_count(), badges = reads.read_badges(), area_id = area_id, loc_name = loc,
            pc_boxes = pc_boxes_wire(), pc_boxes_generation = box_generation(), writes_enabled = self.writes_enabled, rom_sha1 = self.rom_sha1,
            in_battle = battle and battle.in_battle ~= 0 or false, rom_content = rom_content,
            -- per CARTRIDGE, not per generation: only a patched one has the panel mailbox
            panel = self.panel and self.panel:present() or false,
            panel_abi = self.panel and self.panel:abi() or 0,
            sfx = self.panel and self.panel:sfx_present() or false,
            -- MAJOR-1 (review e9d5e136): this client answers apply_prepare before any APPLY
            trade_prepare = self.trade_enabled == true,
        }
        if hello_identity() ~= expected_identity then return false, "hello identity changed during snapshot" end
        return send("hello", payload)
    end

    self.hello_session = HelloSession.new({
        connected = function() return net.connected() end,
        identity = hello_identity,
        ready = function(identity)
            local live, why = game_is_live()
            if not live then return false, why end
            -- wIsInBattle $FF is the lost-battle/blackout state (pokered home/overworld.asm:355),
            -- a battle like 1 and 2: any nonzero value skips the checkpoint, as on master.
            local battle = reads.read_battle()
            if not battle then return false, "battle state unavailable" end
            -- MainMenu can already contain a save. A checkpoint or a running
            -- battle is still required before this game sends its first hello.
            if battle.in_battle == 0 and not safety.check(ws_profile, io) then
                return false, "waiting for Gen 1 checkpoint or battle"
            end
            local want, have = d.game_internal_version, reads.read_game_internal_version()
            if want ~= nil and have ~= want then
                if not self.version_hold_logged then
                    log("[SLink-gen1] hello held: wGameInternalVersion " .. tostring(have) .. " != " .. tostring(want))
                    self.version_hold_logged = true
                end
                return false, "save version unavailable or mismatched"
            end
            return hello_identity() == identity, "identity changed while checking readiness"
        end,
        send = function(identity) return self:send_hello(identity) end,
        retry_delay = function() return 1 end, -- existing Gen 1 next-frame retry policy
        -- master: a savestate load keeps the session (one hello, panel kept), and a throwing
        -- hello callback propagates out of frame_end (run.lua logs it) without ending it
        clock_rewind = "keep", callback_error = "raise",
        on_invalidate = function(reason)
            self.hello_sent = false
            if self.panel then self.panel:clear() end
            if reason == "identity_changed" then
                self.pending_change, self.key_alias, self.retired_alias, self.dead_keys = nil, nil, {}, {}
                self.dead_synced = false
            end
        end,
        on_error = function(stage, why) log("[SLink-gen1] hello " .. stage .. ": " .. tostring(why)) end,
    })

    self.replies = ReplyDispatch.new({
        budget = math.huge, -- existing Gen 1 policy: drain every queued line each frame
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
                log("[SLink-gen1] command " .. tostring(type(subject) == "table" and subject.cmd or nil) .. ": " .. why)
            else
                log("[SLink-gen1] unreadable reply line")
            end
        end,
    })

    function self:send_tick(event)
        local party, battle = snapshot_party()
        if not party then return end
        local map = reads.read_map()
        local area_id, loc = area_of(map.map)
        local in_battle = battle.in_battle ~= 0
        if self.last_area ~= nil and self.last_area ~= area_id then
            send("area_enter", { area_id = area_id, loc_name = loc })
            -- Gen 3 parity (archive/gen3-old-client:lua/clients/gen3_frlge_client.lua:2722-2735): flag entry into a map this
            -- cartridge's OWN wild table (self.wild_maps, from rom.rom_content() at hello) says
            -- can encounter something. "not gift_map_*" is not "not a gift area" (cx-6bedd222):
            -- the server's gift-area list is server-side only, but a gift area has no wild
            -- table either, so requiring one is the check the client can actually make.
            -- Outside battle only: a map transition mid-trainer-battle must not banner.
            if self.has_pokeballs and self.seeded and not in_battle and area_id ~= ""
               and self.wild_maps and self.wild_maps[tostring(map.map)]
               and not self.resolved_areas[area_id] then
                hud.show("** NEW ENCOUNTER **\n" .. loc, 255, 220, 60, 240)
                self:request_sfx_local(25) -- SE_SUCCESS
            end
        end
        self.last_area = area_id
        local had_balls = self.has_pokeballs
        self.has_pokeballs = self.has_pokeballs or ball_count() > 0
        if not had_balls and self.has_pokeballs then announce_nuzlocke_start() end
        send(event or "tick", {
            party = party, has_pokeballs = self.has_pokeballs, ball_count = ball_count(),
            area_id = area_id, loc_name = loc, in_battle = in_battle,
            is_trainer_battle = in_battle and battle.is_trainer or false,
            trainer_id = in_battle and battle.is_trainer and battle.cur_opponent or nil,
            enemy_party = enemy_party(battle), badges = reads.read_badges(),
            trainer_name = reads.read_player_name(), pc_boxes = pc_boxes_wire(), pc_boxes_generation = box_generation(),
            safari_type = battle.safari_type, -- pureRGB only (PLAN §3.5); nil elsewhere
            awaiting_save = awaiting_save_field(), -- BURIAL-VISIBLE: the pair board's "awaiting save"
        })
        resend_refused_change()
    end

    -- ── in-game SLINK TRADE (companion patch receptionist; T-rows) ───────────────────
    -- The overlay is a 16-byte lease the patched game hands the host at wSerialPartyMonsPatchList;
    -- writes to it (and the staged enemy party) happen inside that lease, not at the overworld
    -- checkpoint: the receptionist waits <=30/180 frames in its own loop for our bytes.
    -- the companion patch's receptionist dispatch and service entry come from profile.trade
    -- (entry.lua: the shipped vanilla patch, or an overlay pack's own block); no block = no
    -- native trade path on this cartridge and no probe of a foreign ROM
    local trade_cfg = profile.trade
    local PROMPT, APPLY = 3, 5

    -- partner name shown by the prompt/animation: A-Z, 0-9 and space through the foundation's
    -- ONE charmap (reads.charmap), terminator-padded to name_length
    local function encode_name11(text)
        local cm = reads.charmap
        local out = {}
        for ch in tostring(text or ""):upper():gmatch(".") do
            local code = ch:match("^[A-Z0-9 ]$") and cm.codes[ch] or nil
            if code and #out < d.name_length - 1 then out[#out + 1] = code end
        end
        out[#out + 1] = cm.terminator
        while #out < d.name_length do out[#out + 1] = cm.terminator end
        return out
    end
    local function ot_of(blob)
        local name = {}
        -- the incoming mon carries its OT name right after the struct
        for i = d.battle_struct_size + 1, d.battle_struct_size + d.name_length do name[#name + 1] = blob[i] end
        return name
    end
    local function new_token()
        local t = {}
        for i = 1, 4 do t[i] = math.random(1, 255) end
        return t
    end

    function self:trade_patch_present()
        if not trade_cfg or not trade_cfg.receptionist_hook or not trade_cfg.dispatch_hex then return false end
        local want = hex_bytes(trade_cfg.dispatch_hex)
        for i, b in ipairs(want) do
            if io.read_u8(trade_cfg.receptionist_hook + i - 1, "ROM") ~= b then return false end
        end
        return true
    end

    local function trade_arm(fn)
        writes:arm("trade_overlay")
        local ok, a, b = pcall(fn)
        writes:disarm()
        if not ok then error(a, 0) end
        return a, b
    end

    function self:trade_answer_query(mask)
        local st = self.trade_state
        if not st or st.kind ~= "query" then return end
        local token = new_token()
        local ok, why = trade_arm(function() return self.trade:answer_query(st.gen, mask, token) end)
        if not ok then log("[SLink-gen1] trade query answer refused: " .. tostring(why)) end
        self.trade_state = { kind = "visit", token = token }
    end

    function self:trade_answer_offer(ok)
        local st = self.trade_state
        if not st or st.kind ~= "offer" then return end
        local done, why = trade_arm(function() return self.trade:answer_offer(st.gen, ok) end)
        if not done then log("[SLink-gen1] trade offer answer refused: " .. tostring(why)) end
        self.trade_state = { kind = "visit", token = st.token }
    end

    -- Partner side: the initiator mon is staged and the game asks YES/NO natively.
    function self:trade_prompt(cmd)
        local blob = hex_bytes(cmd.blob_hex)
        local name = ot_of(blob)
        local gen, why = trade_arm(function()
            return self.trade:arm(PROMPT, cmd.slot, blob, name, new_token())
        end)
        if not gen then
            log("[SLink-gen1] trade prompt arm refused: " .. tostring(why))
            send("menu_result", { token = cmd.token, choice = 0 })
            return
        end
        self.trade_state = { kind = "prompt", gen = gen, token = cmd.token, frame = self.frame, arm = { PROMPT, cmd.slot, blob, name } }
    end

    -- Both sides: apply_trade stages the OTHER mon; the game swaps, animates, evolves, saves.
    function self:trade_apply(cmd)
        local slot = find_party_slot(cmd.old_key)
        if slot == nil then
            log("[SLink-gen1] apply_trade: old_key not in party; nothing changed")
            owe("trade_done", { token = cmd.token, slot = cmd.slot, new_key = cmd.old_key, new_species = 0 })
            return
        end
        local blob = hex_bytes(cmd.blob_hex)
        local name = cmd.partner_name and encode_name11(cmd.partner_name) or ot_of(blob)
        local gen, why = trade_arm(function()
            return self.trade:arm(APPLY, slot, blob, name, new_token())
        end)
        if not gen then
            log("[SLink-gen1] apply_trade arm refused: " .. tostring(why))
            owe("trade_done", { token = cmd.token, slot = slot, new_key = cmd.old_key, new_species = 0 })
            return
        end
        self.trade_state = { kind = "apply", gen = gen, token = cmd.token, old_key = cmd.old_key, slot = slot,
                             frame = self.frame,
                             arm = { APPLY, slot, blob, name } }
    end

    -- An apply past its commit boundary with no usable DONE (a result 2, a reset) may have mutated or
    -- saved: no release, no key claim. It is DECLARED, trade_done{token, uncertain = true}, once the
    -- hello is ready (frame_end; after a reset, the post-reset hello); the server journals it and this
    -- side's next party snapshot settles the link. Mirror of lua/gen2/client.lua trade_uncertain.
    -- after_reset (MAJOR-5): result 2 holds the lease until a reset (trade_service.asm
    -- .waitForReceipt), and its RAM party was never saved; the server then takes evidence only
    -- from the reloaded save (the post-reset hello). Mirror of lua/gen2/client.lua.
    local function trade_uncertain(st, why, after_reset)
        if st.declared then return end
        hud.show("TRADE UNCERTAIN - CHECK PARTY", 255, 64, 64, 600)
        log("[SLink-gen1] apply_trade uncertain: " .. why .. "; no release, no key claim")
        st.declared = true
        self.trade_owed[#self.trade_owed + 1] = { event = "trade_done",
            fields = { token = st.token, uncertain = true, after_reset = after_reset or nil } }
    end
    -- Mirror of Gen 2's trade_forget: before the commit boundary nothing was mutated and nothing
    -- is claimed; after it, uncertain.
    function self:trade_forget(why)
        local st = self.trade_state
        if st and st.kind == "apply" and (st.committing or (self.trade and
            (self.trade.entry_observed or self.trade.phase == "picked_up" or self.trade.phase == "done"))) then
            trade_uncertain(st, tostring(why))
        elseif st and st.kind == "apply" then
            -- review m2: before the RemovePokemon nothing was mutated or saved: a certain none, owed
            log("[SLink-gen1] apply_trade forgotten before the commit boundary (" .. tostring(why) .. "); nothing changed")
            self.trade_owed[#self.trade_owed + 1] = { event = "trade_done",
                fields = { token = st.token, slot = st.slot, new_key = st.old_key, new_species = 0 } }
        elseif st and st.kind == "prompt" and not st.declared then
            self.trade_owed[#self.trade_owed + 1] = { event = "menu_result", fields = { token = st.token, choice = 0 } }
        end
        self.trade_state = nil
    end

    -- MAJOR-4 (review e9d5e136): the service picks APPLY up on any overworld frame, so an APPLY left
    -- armed could commit after the server settled the trade. On the server's word: unpicked, give
    -- the borrowed union back and report nothing changed (certain); past the commit boundary,
    -- uncertain. Mirror of lua/gen2/client.lua trade_withdraw.
    function self:trade_withdraw(cmd)
        local st = self.trade_state
        if not (self.trade and st and st.kind == "apply" and st.token == cmd.token) then return end
        if self.trade.phase == "armed" and trade_arm(function() return self.trade:withdraw() end) then
            self.trade_state = nil
            log("[SLink-gen1] apply_trade withdrawn before pickup; nothing changed")
            owe("trade_done", { token = st.token, slot = st.slot, new_key = st.old_key, new_species = 0 })
        elseif st.committing then
            trade_uncertain(st, "the server asked for a withdrawal")
        elseif self.trade.entry_observed or self.trade.phase == "picked_up" or self.trade.phase == "done" then
            -- Native owns the request. Acknowledge conservatively with the existing wire
            -- contract; never claim nothing changed or silently drop the withdrawal.
            trade_uncertain(st, "withdrawal after committed request entry; awaiting native result")
        end
    end

    -- Per-frame: watch the lease for the receptionist questions and the native completions.
    function self:trade_tick()
        if not self.trade_enabled or not self.trade then return end
        local st = self.trade_state
        if st and st.pickup_terminal then return end
        if st and (st.kind == "prompt" or st.kind == "apply") then
            local clobbered = self.trade:clobbered()
            if self.trade.pickup_error then
                st.pickup_hold_frame = st.pickup_hold_frame or self.frame
                if self.frame - st.pickup_hold_frame >= Client.TRADE_HOLD_TERMINAL_FRAMES then
                    -- End the protocol wait conservatively; never release/restage native work.
                    local ok, party = pcall(current_party)
                    log("[SLink-gen1] pickup hold expired; party rescan=" ..
                        tostring(ok and party and #party or "unavailable"))
                    self.pending_change = {kind="rescan", frame=self.frame}
                    if st.kind == "prompt" then
                        if not st.declared then
                            owe("menu_result", {token=st.token, choice=0, uncertain=true})
                            hud.show("TRADE UNCERTAIN - CHECK PARTY", 255, 64, 64, 600)
                            st.declared = true
                            log("[SLink-gen1] prompt hold expired; outcome uncertain, native lease held until reset")
                        end
                    else
                        trade_uncertain(st, "consumption evidence missing after bounded hold")
                    end
                    -- Keep trade_state: clearing it reopens query handling and re-staging.
                    st.pickup_terminal = true
                    return
                end
                st.pickup_hold_count = (st.pickup_hold_count or 0) + 1
                local now = os.time()
                if not st.pickup_notice or now - st.pickup_notice >= Client.HOLD_REMINDER_SECONDS then
                    log("[SLink-gen1] trade hold: step=pickup count=" .. st.pickup_hold_count
                        .. " reason=" .. tostring(self.trade.pickup_error))
                    st.pickup_notice = now
                end
            end
            if self.trade.phase == "armed" and self.frame - (st.frame or self.frame) > Client.TRADE_ARMED_TRIPWIRE_FRAMES then
                local now = os.time()
                if not st.pickup_wait_notice or now - st.pickup_wait_notice >= Client.HOLD_REMINDER_SECONDS then
                    log("[SLink-gen1] trade armed too long: step=pickup frames=" .. (self.frame - st.frame)
                        .. " entry_observed=" .. tostring(self.trade.entry_observed))
                    st.pickup_wait_notice = now
                end
            end
            if clobbered then
                -- Only a request never observed at entry may be safely re-staged.
                local gen = trade_arm(function() return self.trade:arm(st.arm[1], st.arm[2], st.arm[3], st.arm[4], new_token()) end)
                if gen then st.gen = gen end
                return
            end
            local done = self.trade:poll_done()
            if not done then return end
            if st.kind == "prompt" then
                local released = trade_arm(function() return self.trade:release(st.gen) end)
                send("menu_result", { token = st.token, choice = (done.result == 0 and released) and 1 or 0 })
                self.trade_state = nil
            else
                if done.result == 2 then
                    -- native append uncertain (T-5 limit): the cartridge keeps the lease; no release, no claim
                    if not st.warned then
                        st.warned = true
                        trade_uncertain(st, "native result 2; holding until a reset", true)
                    end
                    return
                end
                trade_arm(function() return self.trade:release(st.gen) end)
                if done.result == 0 then
                    local party = current_party()
                    local received = party and party[#party]
                    if received then
                        local key = mon_key(received)
                        self.known_keys[st.old_key] = nil
                        self.known_keys[key] = true
                        owe("trade_done", { token = st.token, slot = received.slot, new_key = key, new_species = received.species })
                    else
                        owe("trade_done", { token = st.token, slot = st.slot, new_key = st.old_key, new_species = 0 })
                    end
                else
                    log("[SLink-gen1] apply_trade refused natively (result " .. tostring(done.result) .. "); nothing changed")
                    owe("trade_done", { token = st.token, slot = st.slot, new_key = st.old_key, new_species = 0 })
                end
                self.trade_state = nil
                self.pending_change = { kind = "rescan", frame = self.frame }
            end
            return
        end
        local q = self.trade:poll_query()
        if q and not (st and st.kind == "query" and st.gen == q.gen) then
            self.trade_state = { kind = "query", gen = q.gen }
            if not send("trade_query", {}) then
                trade_arm(function() return self.trade:answer_query(q.gen, 0, new_token()) end) -- offline: nothing eligible
                self.trade_state = nil
            end
            return
        end
        local o = self.trade:poll_offer()
        if o and not (st and st.kind == "offer" and st.gen == o.gen) then
            self.trade_state = { kind = "offer", gen = o.gen, slot = o.slot, token = st and st.token }
            if not send("trade_offer", { slot = o.slot }) then
                trade_arm(function() return self.trade:answer_offer(o.gen, false) end)
                self.trade_state = nil
            end
        end
    end

    -- ── per-frame driver ─────────────────────────────────────────────────────────────
    function self:start()
        -- An in-hook handler fault is logged and re-raised: the registry records it as
        -- handler_error and keeps queuing later signals (it is never a kill switch).
        local function logged(kind, fn)
            return function(sig)
                local ok, err = pcall(fn, sig)
                if not ok then
                    log("[SLink-gen1] signal handler " .. kind .. ": " .. tostring(err))
                    error(err, 0)
                end
            end
        end
        local handlers = {
            battle_loop_head = logged("battle_loop_head", function(sig) self:on_battle_loop_head(sig) end),
            battle_loop_no_move = logged("battle_loop_no_move", function(sig) self:on_battle_loop_no_move(sig) end),
            apex_preflight = logged("apex_preflight", function(sig) self:on_apex_preflight(sig) end),
            apex_commit = logged("apex_commit", function(sig) self:on_apex_commit(sig) end),
            transform = logged("transform", function(sig) self:on_transform(sig) end),
            transform_hp_lo = logged("transform_hp_lo", function(sig) self:on_transform_hp_lo(sig) end),
        }
        local all_sites = sites
        self.trade_enabled = false
        if self.trade and self:trade_patch_present() then
            local pickup_ok, pickup = pcall(self.trade.pickup_site, function(a) return io.read_u8(a, "ROM") end)
            if not pickup_ok or type(pickup) ~= "table" then
                log("[SLink-gen1] trading disabled: " .. tostring(pickup))
            else
                -- Vanilla's anchor is SlinkForeground; pure overlays name SlinkTradeService.
                -- signals.lua filters poll hits against the ROM's published trade-work gate.
                -- This companion-only site is pinned against the running ROM here.
                local svc = self.trade.service_address()
                local flat = svc.bank * 0x4000 + (svc.addr - 0x4000)
                local bytes = io.read_range(flat, 6, "ROM")
                all_sites = {}
                for k, v in pairs(sites) do all_sites[k] = v end
                local function waiting_for_pickup()
                    return self.trade.phase ~= "picked_up" and self.trade.phase ~= "done"
                end
                all_sites.trade_service = { bank = svc.bank, address = svc.addr, rom_offset = flat,
                                            capture_offset = 0, expected_hex = hex_of(bytes),
                                            lease_address = assert(profile.ram.wSerialPartyMonsPatchList, "trade lease_address required"),
                                            client_accept = waiting_for_pickup,
                                            symbol = self.foundation == "gen1_purergb"
                                                and "SlinkTradeService" or "SlinkForeground" }
                -- Polls never prove pickup. The binder requires its post-restore stack witness.
                handlers.trade_service = logged("trade_service", function()
                    self.trade:picked_up()
                end)
                all_sites.trade_request = pickup.request_site
                pickup.request_site = nil
                all_sites.trade_request.entry_observer = function() self.trade:observe_entry() end
                all_sites.trade_request.entry_accept = function()
                    return self.trade.phase == "armed" and not self.trade.entry_observed
                end
                all_sites.trade_request.client_accept = function() return false end -- observation only, no token
                all_sites.trade_consumed = pickup
                all_sites.trade_consumed.client_accept = waiting_for_pickup
                handlers.trade_consumed = logged("trade_consumed", function(sig)
                    local picked, why = self.trade:picked_up(sig.sp)
                    if picked and not self.trade.entry_observed
                       and self.trade_entry_warning_request ~= self.trade.expected then
                        self.trade_entry_warning_request = self.trade.expected
                        log("[SLink-gen1] consumption verified without request-entry observation")
                    end
                    if why then
                        log("[SLink-gen1] trade consumption unverified; holding: " .. tostring(why))
                        if self.trade_state then self.trade_state.pickup_notice = os.time() end
                    end
                end)
                self.trade_enabled = true
            end
        end
        -- Re-arming (e.g. against a newly patched ROM) releases the previous hook set first:
        -- the shared registry owns one "SLink-gen1" namespace at a time.
        if self.signals then
            assert(self.signals:close(), "previous engine signals could not be released")
            self.signals = nil
        end
        self.signals = signals_mod.new(profile, all_sites, io, handlers)
        self.filter_errors_logged = 0
    end

    function self:frame_end()
        self.frame = io.framecount()
        -- Keep the successful-frame order, but an early hold can never skip draining.
        local stage = "panel"
        local ready, connected = pcall(function()
            if self.panel then
                local pok, perr = self.panel:service()
                if not pok then error("panel: " .. tostring(perr), 0) end
            end
            stage = "net.pump"
            net.pump()
            stage = "hello"
            local online = self.hello_session:step(self.frame)
            self.hello_sent = online == true
            stage = "validate"
            if self.frame % Client.VALIDATE_EVERY == 0 then self:validate() end
            online = online and self.hello_session:status().ready
            stage = "owed"
            self.owed:step(net.connected(), online == true, send)
            return online
        end)
        for _, sig in ipairs(self.signals and self.signals:drain() or {}) do
            local ok, err = pcall(self.on_signal, self, sig)
            if not ok then log("[SLink-gen1] signal " .. tostring(sig.kind) .. ": " .. tostring(err)) end
        end
        -- A throwing hook filter dropped a hit (master: the error reached the console). Log
        -- once per NEW error, never per frame.
        if self.signals and self.signals.filter_status then
            local fs = self.signals:filter_status()
            if fs.accept_errors > (self.filter_errors_logged or 0) then
                self.filter_errors_logged = fs.accept_errors
                log("[SLink-gen1] signal filter " .. tostring(fs.accept_error) .. " (" .. fs.accept_errors .. " dropped)")
            end
        end
        -- The registry latches its first capture/queue failure and drops every later hook: say
        -- so once, loudly, instead of a run that silently stops seeing captures and battles. A
        -- synchronous hook handler (the in-battle faint/explode writes) that threw is contained
        -- as handler_error; log each new one, or a write that never lands leaves no trace.
        local st = self.signals and self.signals.status and self.signals:status()
        if st and st.handler_error and st.handler_error ~= self.handler_error_logged then
            self.handler_error_logged = st.handler_error
            log("[SLink-gen1] hook handler error: " .. tostring(st.handler_error))
        end
        if st and st.failed and not self.signal_failure_shown then
            self.signal_failure_shown = true
            log("[SLink-gen1] ENGINE SIGNALS STOPPED: " .. tostring(st.failed))
        end
        if not ready then
            local now = os.time()
            if not self.frame_hold_state then
                self.frame_hold_error = stage .. ": " .. tostring(connected)
                self.frame_hold_state = {started=now, last_report=now, count=0}
                log("[SLink-gen1] frame hold: " .. self.frame_hold_error)
            end
            local hold = self.frame_hold_state
            hold.count = hold.count + 1
            if now < hold.last_report then hold.last_report = now end
            if now - hold.last_report >= Client.HOLD_REMINDER_SECONDS then
                log("[SLink-gen1] frame hold reminder: step=" .. stage .. " count=" .. hold.count
                    .. " held_s=" .. math.max(0, now-hold.started) .. " reason=" .. tostring(connected))
                hold.last_report = now
            end
            return -- drained and diagnosed; no deferred work during the hold
        end
        self.frame_hold_error, self.frame_hold_state = nil, nil
        self:rival_window_tick()
        self:settle_pending_change()
        -- Every readable frame OBSERVES each alias (pure pass, no similarity, nothing refreshed):
        -- an edit interval in which the changed record stops matching -- however briefly, e.g.
        -- one TM taught to it and then its old move set taught to a twin, all inside the item
        -- menu -- latches `lost` before the twin could ever be the sole match (review cx-549fefdf).
        if self.key_alias or next(self.retired_alias) then
            local party = current_party()
            if party then
                if self.key_alias then observe_alias(self.key_alias, party) end
                for _, r in pairs(self.retired_alias) do observe_alias(r, party) end
            end
        end
        if connected and self.frame % Client.TICK_INTERVAL == 0 then
            -- KEY-SCOPE-5: an incomplete box scan is retried, at most once per tick
            if self.hello_sent and not self.box_complete then self:rescan_boxes() end
            self:send_tick("tick")
        end
        if self.pending_safe and connected then
            local battle = reads.read_battle()
            if battle.in_battle == 0 then
                -- the battle may have boxed a catch: a fresh census rides the safe (KEY-SCOPE-5)
                self.pending_safe = false
                self:rescan_boxes()
                send("safe", { pc_boxes = pc_boxes_wire(), pc_boxes_generation = box_generation() })
            end
        end
        if self.frame % Client.BURIAL_NAG_FRAMES == 0 and burial_waiting() then show_burial() end
        self.replies:step()
        local tok, terr = pcall(self.trade_tick, self)
        if not tok then log("[SLink-gen1] trade: " .. tostring(terr)) end
        self:run_deferred()
    end

    function self:stop()
        if self.signals then self.signals:close() end
    end

    return self
end

return Client
