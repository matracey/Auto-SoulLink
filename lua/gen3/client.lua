-- lua/gen3/client.lua -- the Gen 3 driver over the shared core (P4 C4-2b).
--
-- Client.new(p) builds the game driver (docs/gen3/research/p4_gen1_contract_map.md §3.2) and
-- returns lua/core/session.lua's Session over it: the Gen 1 constructor shape. It is
-- pack-neutral: gen3_frlg (firered/leafgreen) and gen3_rr (radical_red, clean and companion)
-- differ only in profile data and capability flags (which addresses the pack pins), never in
-- a title branch. No BizHawk global is named here; every byte arrives through p.io and every
-- write goes through p.writes (the armed sink) or p.boxes (which arms the same sink).
--
-- Parts (all injected by lua/gen3/entry.lua production mode):
--   reads (instance) + R (the reads module, for record geometry), profile, sites, Signals,
--   writes, boxes (optional), policy (snapshot/check(snap, reason): "overworld" is the
--   G3-signed checkpoint; "battle_faint"/"battle_commit" are the C4-B battle predicate seam),
--   net, hud, json, io, ev, area_map, locations, player, rom_type, rom_sha1, foundation,
--   artifact_kind, log, core = { Session, Identity, Deferred }, native (optional; P5).
--
-- The optional native part (lua/gen3/native.lua, the RR companion mailbox) arrives as p.native:
-- Entry builds ONE instance and hands it to the client, Safety's native_idle and (through it)
-- the write policy, so all three see the same mailbox. Every seam below calls it when present
-- and otherwise does what the old client did without the patch. Native POSTING is gated on the
-- session (S:eligible(): writes enabled, hello'd, connected): the client wraps the injected
-- write policy so that every arm of reason "native" or "sound" is also refused while the
-- session is ineligible. native:service() still runs every frame, so ACK polling never stops;
-- a refused arm leaves the job queued (native.lua dispatch returns nil), it is not dropped.
--   native:service()               each frame before the net pump
--   native:idle() -> bool          the safety checkpoint's "native" clause
--   native:hello_fields() -> table merged into hello (panel/sfx capabilities)
--   native:replace_rival_team(cmd) / show_menu(cmd) / show_choices(cmd) / choose_mon(cmd) /
--   link_panel(cmd) / config(cmd) / play_sound(id) / transfer(step, args, done) (the trade FSM
--   trade.lua owns prepare/apply/withdraw, milestones, readback and owed completion)
--
-- Reducer model (the Gen 1 rule, lua/gen1/client.lua:14-17): events are derived from engine
-- signals, never from polling. A signal marks WHAT kind of change the engine made this frame;
-- the settle pass after the drain reads the party (and the boxes when storage moved) once and
-- diffs it against the last snapshot. The site register points are not interpreted, because
-- their meanings differ per artifact and the packs do not carry register roles yet
-- (docs/gen3/research/site_capture_points.md): a register-free diff is identical on both packs.
-- Vocabulary: tools/gen3_shadow_diff.py KINDS; folding: capture_wild + mon_given in one
-- settle -> one capture; SendMonToPC (pc_move) is acquisition to storage, not a user deposit.
local Client = {}

-- Record geometry and game constants the packs do not ship yet (requested from C4-2a), the same
-- on every admitted title: pret/pokefirered@c75f352 include/pokemon.h struct Pokemon / struct
-- BattlePokemon and the old client's production values (archive/gen3-old-client:lua/memory_gba.lua:395-416);
-- CFRU keeps the same layout, and so does pret/pokeemerald@c65e93f2 (include/pokemon.h:219-231 hp
-- at 0x56, :268 moves, :281 pp; include/constants/moves.h:157; include/battle.h:27).
local PARTY_HP_OFF = 0x56            -- struct Pokemon.hp (u16)
local MOVE_EXPLOSION = 153           -- include/constants/moves.h
local EXPLODE_PP = 5                 -- Explosion's PP, so a PP drop proves the move executed
-- CFRU action-commit values (archive/gen3-old-client:lua/memory_gba.lua:1318-1345, production-tested on RR):
local B_ACTION_USE_MOVE = 0
local TARGET_FOE_PRIMARY = 1
-- (the committed state, STATE_WAIT_ACTION_CONFIRMED_STANDBY, is per title: p.commit_guard.value)
-- The m4a SE1 poke (the old client's M.playSE, archive/gen3-old-client:lua/memory_gba.lua:2021-2066, production-tested
-- on FR/LG and RR). Every address, field offset and title SE id (se_ids) comes from the checkpoint pack's sound block
-- (p.sound: player_se1 / sound_info_ptr, player_head_off / player_next_off / tracks_off, and
-- `fields`, the exact m4a fields a sound write may touch); only the VALUES the old client
-- stores are here.
local TRK_START = 0xC0                        -- MusicPlayerTrack.flags: EXIST | START
local TRK_BEND, TRK_VOLX, TRK_LFO = 2, 64, 22 -- the old client's per-track defaults
local PC_KINDS = { pc_deposit = true, pc_withdraw = true, pc_box_place = true,
                   pc_release_begin = true, pc_release = true }
local MENU_CMDS = { "show_menu", "show_choices", "choose_mon" }

local function hex_of(bytes)
    local out = {}
    for i = 1, #bytes do out[i] = string.format("%02X", bytes[i]) end
    return table.concat(out)
end
-- json null decodes to a table sentinel: only a number is an address
local function num(v) return type(v) == "number" and v or nil end

-- A death banner names a mon for the player. Several server death paths send a blank
-- nickname (server/state.py), and a decoded record may itself have an empty or space-only
-- name. "" is truthy in Lua: it used to blank the banner and mask a valid record nickname.
-- The first non-blank candidate wins, with the command still outranking the record. A decoded
-- mon always has a string nickname, so the old raw-key fallback was unreachable on that path;
-- a nameless mon now gets generic text. No species is guessed here.
local NO_NAME = "Your Pokemon"
local function hud_name(...)
    for i = 1, select("#", ...) do
        local candidate = select(i, ...)
        if type(candidate) == "string" and candidate:find("%S") then return candidate end
    end
    return NO_NAME
end

function Client.new(p)
    local reads, R, profile = assert(p.reads, "reads"), assert(p.R, "R"), assert(p.profile, "profile")
    local writes, policy, boxes = assert(p.writes, "writes"), assert(p.policy, "policy"), p.boxes
    local json, hud, io, native = assert(p.json, "json"), assert(p.hud, "hud"), assert(p.io, "io"), p.native
    local core = assert(p.core, "core")
    local trade
    local journal = io.trade_journal
    local trade_run_id, trade_run_ot
    local pending_trade_finals = {}
    local pending_apply_trade
    local awaiting_trade_run = journal and not journal:ready() or false
    local reload_empty_frames, reload_boot_seen, reload_check_frame = 0, false, -1000
    local reload_witness_live = false
    local owed = p.owed_reports and p.owed_reports.new() or nil
    local trade_epoch, trade_connected = 0, false
    local trade_reset_epoch = 0
    local trade_report_epochs = setmetatable({}, {__mode="k"})
    local owed_hold = nil       -- the reason the last owed report is held, logged once per hold
    local a, d = profile.ram, profile.derived
    -- Expansion BattlePokemon is 140 bytes and places pp at +37. Older packs predate these
    -- geometry keys; retain their established offsets until their profiles carry the keys.
    local BATTLE_MON_MOVES_OFF = d.BATTLE_MON_MOVES_OFF or 0x0C
    local BATTLE_MON_PP_OFF = d.BATTLE_MON_PP_OFF or 0x24
    local arr = json.array
    local area_map, locations = p.area_map or {}, p.locations or {}
    local sink = p.log or function() end
    local TAG = "[SLink-gen3]"
    local function log(msg) sink(TAG .. " " .. msg) end
    -- E3-CLIENT: title facts come from the pack, never FR literals, and a missing one fails
    -- closed (logged once, here). STANDBY is this title's STATE_WAIT_ACTION_CONFIRMED_STANDBY
    -- (battle.commit_guard.value: FR/LG/RR 3, Emerald 4); without it no battle commit is written.
    local STANDBY = type(p.commit_guard) == "table" and num(p.commit_guard.value) or nil
    if not STANDBY then log("pack has no battle.commit_guard.value: battle commits refused") end
    -- Gift areas (the pack's gift_areas.ids): no NEW ENCOUNTER banner and never a no_catch there.
    -- Without the list EVERY area counts as one: an area is never dead-zoned on a guess.
    local gift_area
    do
        local ids = type(p.gift_areas) == "table" and p.gift_areas.ids
        local set = ids and json.kind(ids) == "array" and {} or nil
        for _, id in ipairs(set and ids or {}) do
            -- a non-string or empty id would build a set nothing matches (fail OPEN): the whole
            -- list is untrusted then, and takes the missing-list fallback
            if type(id) ~= "string" or id == "" then set = nil; break end
            set[id] = true
        end
        if set then
            gift_area = function(id) return set[id] == true end
        else
            log("pack has no valid gift_areas.ids: every area treated as a gift area (no banner, no no_catch)")
            gift_area = function() return true end
        end
    end
    local key = reads.key
    local session
    local function send(event, fields) return session.send(event, fields) end
    local function owe(event, fields)
        if io.report_outbox and io.report_outbox.events[event] then return send(event,fields) end
        -- INV-CLIENT-2: native removals use the same reply-bound outbox as trade.
        if not owed then error("durable reports unavailable for " .. event) end
        for _, report in ipairs(owed.list) do
            if report.event == event and report.fields.key == fields.key then return end
        end
        owed.list[#owed.list + 1] = {event = event, fields = fields}
    end
    local function eligible() return session ~= nil and session:eligible() end
    local function recovery_hidden()
        return journal and (journal:hidden() or (awaiting_trade_run and journal:has_entries())) or false
    end
    -- The BLOCKER gate (C4-2d): native and sound arms need an eligible session. Wrapping the
    -- injected policy covers every arm made through the one sink, including a job native.lua
    -- queued while eligible and tries to post after a pause.
    local base_check = policy.check
    function policy:check(snapshot, reason, args)
        if (reason == "native" or reason == "sound") and not eligible() then
            return false, "session not eligible for " .. reason .. " writes (paused, not hello'd or disconnected)"
        end
        return base_check(self, snapshot, reason, args)       -- args reach the clause sets
    end

    -- battle request identity (docs/protocol.md, card C5-10): a client-SESSION NONCE minted once
    -- per client process, plus a per-battle counter bumped on every battle-begin signal. Both
    -- ride on trainer_battle_start; the server stores and echoes both on replace_rival_team, and
    -- the command seam below refuses anything that is not this session's current battle.
    --
    -- The nonce is what makes a restart safe: a bare counter would collide across sessions
    -- (session A's battle 1 and a restarted session B's battle 1 are both 1, so a command the
    -- server queued for A and delivered late would pass in B). A reconnect inside one process
    -- keeps both; only a real restart re-mints the nonce.
    --
    -- The seed arrives from the bootstrap (lua/gen3/run.lua, which owns the BizHawk-facing
    -- entropy) through Entry, or from $SLINK_GEN3_BATTLE_NONCE for a harness that needs
    -- determinism. Anything else -- absent, non-hex, longer than the wire's 16 chars -- means NO
    -- identity: there is no local draw (C5-11c MAJOR 5), because a guessable session is exactly
    -- what the per-install counter exists to prevent.
    local session_nonce
    do
        local seed = p.battle_nonce_seed
        -- NO fallback (Codex C5-10 review, restated C5-11c): without a usable seed this client
        -- mints NO identity, declares no capability, and refuses every rival command -- fail
        -- closed, never a guessable session.
        if type(seed) == "string" and seed:match("^%x+$") and #seed <= 16 then
            session_nonce = seed
        else
            session_nonce = nil
        end
    end
    if native and (p.artifact_kind == "companion" or p.artifact_kind == "rand_companion") and native.bind_match_call_session and session_nonce then
        -- The production nonce's first word is the persisted session counter.
        -- Short explicit harness seeds are also u32s; never invent an epoch.
        local epoch_seed = #session_nonce == 16 and session_nonce:sub(1,8)
                           or (#session_nonce <= 8 and session_nonce or nil)
        local epoch = epoch_seed and tonumber(epoch_seed, 16)
        if epoch and epoch > 0 then native:bind_match_call_session(epoch) end
    end
    local battle_seq = 0
    -- RIVAL AUTHORITY (C5-11c BLOCKER 2). The battle that may accept a rival swap, as the immutable
    -- {session, battle_id, trainer_id} triple. It is opened when this client ANNOUNCES a battle
    -- (note_battle) and closed SYNCHRONOUSLY when a lifecycle boundary signal is CAPTURED -- the
    -- signals layer runs the on_fire handlers at the fire (lua/gen3/signals.lua:121-122), i.e.
    -- before the frame's drain and before pre_pump services native. That is deliberately separate
    -- from st.battle: a same-frame battle_end + battle_begin (same trainer) never touches the
    -- reducer's lifecycle, so a job queued behind a menu cannot ride it into the next battle, and
    -- the boundary sweep that used to clear st.battle (and made finish_battle skip the encounter
    -- result) is gone.
    --
    -- It is only as good as the signals that close it (C5-11d): a reset (drv.on_reset) revokes it,
    -- and so does a signal source that stopped delivering -- see live_authority below.
    local rival_authority = nil
    local nature_preimage = nil
    local nature_completed = nil
    local borrowed_party = nil
    local sig_src = nil          -- the signal source drv.start armed; its health gates the authority
    local st = {
        known = {},        -- already acquired non-egg keys (party + boxes)
        eggs = {},         -- observed eggs stay pending until the native hatch signal
        alive = {},        -- keys last seen with hp > 0: a faint is alive -> hp 0 at a faint site
        commanded = {},    -- keys WE zeroed: their faint is not reported (old client force_fainted_keys)
        party_prev = {},   -- key -> { slot, level, max_hp } at the last settle (PC diff baseline)
        carried = {},      -- keys that left the party into the PC cursor, not yet placed
        box_cache = {}, boxes_ok = false, box_generation = 0,
        battle = nil,      -- battle_begin .. battle_end lifecycle
        frozen = false,    -- a borrowed party is in RAM
        flags = {},        -- what the signals of this frame said changed
        has_pokeballs = false, last_area = nil, trade = nil, sound_frame = nil, sound_logged = {},
        baselined = false, seen_count = nil, observe_at = nil,
        trade_apply = nil, trade_settle_until = 0,
        trade_limits = { settle = 30 },
        bframe = nil, bcache = nil,
        held_acq = nil,        -- LOST-CAPTURE: a gift / PC-move acquisition held while the journal is unreadable (acquisition only)
        acq_hold_limit = 1800, -- frames (30 s) a held acquisition may wait before its loud, player-visible expiry
    }
    -- the first actual native-trade POST and its settle window own the party change (settle's `trading`)
    function st.trading_now()
        return (st.trade_apply ~= nil and (st.trade_apply.posted == true or st.trade_apply.possibly_posted == true))
               or io.framecount() < st.trade_settle_until
    end

    -- ── reads (tolerant: a read the pack cannot make yet degrades the field, not the client)
    local function call(name, ...)
        local f = reads[name]
        if not f then return nil, "reads." .. name .. " is not built" end
        return f(...)
    end
    local function party_read(occupied) return call("read_party", occupied) end
    local function trainer() local t = call("read_trainer") return type(t) == "table" and t or nil end
    local function area_now()
        local l = call("read_location")
        if type(l) ~= "table" then return "", "" end
        local k = tostring(l.map_group) .. ":" .. tostring(l.map_num)
        return area_map[k] or "", locations[k] or ""
    end
    local function badges()
        local b = call("read_badges")
        if type(b) == "table" then b = b.mask or b.badges end
        return num(b)
    end
    local function ball_count()
        local b = call("read_balls")
        if type(b) ~= "table" then return nil end
        return num(b.ball_count or b.count)
    end
    -- read_battle decodes the enemy party: once per frame
    local function battle_now()
        local f = io.framecount()
        if st.bframe ~= f then
            st.bframe = f
            local b = call("read_battle")
            st.bcache = type(b) == "table" and b or false
        end
        return st.bcache or nil
    end
    local function in_battle()
        local b = battle_now()
        if b and b.in_battle ~= nil then return b.in_battle == true end
        return st.battle ~= nil
    end
    local function party_hp_addr(slot)
        local base = reads.party_base and reads.party_base()
        return base and base + slot * R.PARTY_MON_SIZE + PARTY_HP_OFF or nil
    end
    -- player-side battlers are positions 0 and (doubles) 2 (reads.lua read_battle)
    local function battler_of(b, slot)
        local idx = b and b.battler_party_indexes or {}
        if idx[1] == slot then return 0 end
        if (b and b.battlers_count or 0) >= 4 and idx[3] == slot then return 2 end
        return nil
    end

    -- ── wire shapes ────────────────────────────────────────────────────────────────
    -- `active` and stat_stages ride a battler's entry (docs/protocol.md §4.1, old client parity)
    -- only while gBattleMons[battler] holds that very mon (reads.battler_holds: in a switch the
    -- party index moves before the mon is copied in, so the index alone would pin the outgoing
    -- mon's stages on the incoming one). No stages at all in a link battle, nor while the pack
    -- cannot say it is not one: there battler ids are not positions (pret
    -- battle_controllers.c:151-168). Returns active, stages.
    local function stages_of(b, battler, m)
        if not battler or call("battler_holds", battler, m) ~= true then return false, nil end
        local s = b and b.is_link == false and call("read_stat_stages", battler)
        return true, type(s) == "table" and arr(s) or nil
    end
    local function party_entry(m, active, b)
        local base = reads.party_base and reads.party_base()
        local raw = base and io.read_bytes(base + m.slot * R.PARTY_MON_SIZE, R.PARTY_MON_SIZE)
        local held, stages = stages_of(b, active[m.slot], m)
        return { key = key(m), slot = m.slot, species_id = m.species, nickname = m.nickname,
                 level = m.level, hp = m.hp, maxHP = m.max_hp, status_cond = m.status,
                 moves = arr(m.moves), pp = arr(m.pp), pp_bonuses = m.pp_bonuses,
                 held_item_id = m.held_item, active = held,
                 stat_stages = stages, blob_hex = raw and hex_of(raw) or nil }
    end
    local function party_wire(party)
        local active, b = {}, nil
        if in_battle() then
            b = battle_now()
            for i, s in ipairs(b and b.active_player_battler_slots or {}) do active[s] = 2 * (i - 1) end
        end
        local out = arr({})
        for i, m in ipairs(party) do out[i] = party_entry(m, active, b) end
        return out
    end
    local function enemy_wire(b)
        local out = arr({})
        if not b then return out end
        local idx, active = b.battler_party_indexes or {}, {}
        if idx[2] then active[idx[2]] = 1 end
        if (b.battlers_count or 0) >= 4 and idx[4] then active[idx[4]] = 3 end
        for _, m in ipairs(b.enemy_party or {}) do
            if m.species and m.species ~= 0 then
                local held, stages = stages_of(b, active[m.slot], m)
                out[#out + 1] = { species_id = m.species, level = m.level, hp = m.hp, maxHP = m.max_hp,
                                  active = held, held_item_id = m.held_item,
                                  stat_stages = stages,
                                  status_cond = m.status, moves = arr(m.moves), pp = arr(m.pp),
                                  pp_bonuses = m.pp_bonuses }
            end
        end
        return out
    end
    local function rescan_boxes()
        local cache, ok = {}, true
        for box = 0, (num(d.BOXES_PER_STORE) or 0) - 1 do
            local mons, why = call("read_box", box)
            if not mons then ok = false; log("box scan stopped at box " .. box .. ": " .. tostring(why)); break end
            for _, m in ipairs(mons) do
                if m.has_species == 1 then
                    cache[#cache + 1] = { box = box, slot = m.slot, key = key(m), species_id = m.species,
                                          nickname = m.nickname, held_item_id = m.held_item,
                                          moves = m.moves, is_egg = m.is_egg }
                end
            end
        end
        st.box_cache, st.boxes_ok = cache, ok
        if ok then st.box_generation = st.box_generation + 1 end
        return cache
    end
    -- the ONE box-generation accessor: (raw generation, whether the last scan was complete) for
    -- the core (lua/core/session.lua); a wire field takes `ok and gen or nil` of it, so the two
    -- can never disagree
    local function box_generation() return st.box_generation, st.boxes_ok end
    local function pc_boxes_wire()
        if not st.boxes_ok then return nil end
        local out = arr({})
        for _, e in ipairs(st.box_cache) do
            out[#out + 1] = { box = e.box, slot = e.slot, key = e.key, species_id = e.species_id,
                              nickname = e.nickname, held_item_id = e.held_item_id, moves = arr(e.moves) }
        end
        return out
    end

    -- ── baselines ──────────────────────────────────────────────────────────────────
    local function observe_hp(party)
        for _, m in ipairs(party) do
            local k = key(m)
            if m.hp == 0 then st.commanded[k] = nil          -- our zero has landed
            elseif not st.commanded[k] then st.alive[k] = true end
        end
    end
    -- The stats shape server/state.py caches (stats_cache, party_to_box, boxed capture) and
    -- boxes.lua needs to rebuild a CFRU record on withdraw (level, maxHP, five stats, PP):
    -- the old client's snapshot, archive/gen3-old-client:lua/clients/gen3_frlge_client.lua:1625-1639.
    local function stats_of(m)
        local s = { level = m.level, maxHP = m.max_hp, attack = m.attack, defense = m.defense,
                    speed = m.speed, spAtk = m.sp_attack, spDef = m.sp_defense }
        for i = 1, 4 do s["pp" .. i] = m.pp and m.pp[i] or 0 end
        return s
    end
    local function rebaseline(party)
        st.party_prev = {}
        for _, m in ipairs(party) do st.party_prev[key(m)] = { slot = m.slot, stats = stats_of(m) } end
        observe_hp(party)
    end
    -- ACQ diagnostics (log only; no decision reads them): one line per call that marked keys known, naming the path (`via`; no argument = the quiet-count observe path)
    local function seed_known(party, via)
        if st.held_acq then       -- a held acquisition is pending: seeding now would absorb its key as known
            log("ACQ seed skipped via=" .. (via or "observe") .. " (a held acquisition is pending)")
            return
        end
        local fresh = {}
        for _, m in ipairs(party) do
            local k = key(m)
            if m.is_egg == 1 then st.eggs[k] = true
            elseif not st.eggs[k] then
                if not st.known[k] then fresh[#fresh + 1] = k end
                st.known[k] = true
            end
        end
        for _, e in ipairs(st.box_cache) do
            if e.is_egg == 1 then st.eggs[e.key] = true
            elseif not st.eggs[e.key] then
                if not st.known[e.key] then fresh[#fresh + 1] = e.key end
                st.known[e.key] = true
            end
        end
        if #fresh > 0 then
            log(string.format("ACQ known via=seed:%s new=%d keys=%s%s", via or "observe", #fresh,
                              table.concat(fresh, ",", 1, math.min(#fresh, 6)), #fresh > 6 and ",..." or ""))
        end
    end
    local function mark_commanded(k)
        st.commanded[k], st.alive[k] = true, nil
    end
    local function latch_balls(announce)
        local n = ball_count()
        if n and n > 0 and not st.has_pokeballs then
            st.has_pokeballs = true
            if announce then hud.nuzlocke_start("Nuzlocke Start!") end
        end
        return n
    end

    -- A borrowed party (RR multi/tag battles swap a partner's party in) shares no key with the
    -- pre-battle party. While it is in RAM nothing is diffed and tick omits `party`
    -- (docs/protocol.md §3.2 tick row). Held battle writes are KEPT, not dropped (the old client
    -- dropped them on restore, :2976): a server force_faint is never resent, so dropping one
    -- loses a required write; the core holds them until the real party returns or the battle
    -- ends, then the checkpoint queue lands them.
    local function update_frozen(party)
        local was = st.frozen
        if borrowed_party then
            local authority = borrowed_party
            local restored = authority.ended and not authority.invalid and not in_battle()
                and #party == authority.count
            if restored then
                local seen = {}
                for _, mon in ipairs(party) do
                    local k = key(mon)
                    if not authority.keys[k] or seen[k] or mon.has_species ~= 1
                       or mon.is_bad_egg ~= 0 or mon.checksum_ok == false then restored = false; break end
                    seen[k] = true
                end
            end
            if restored then borrowed_party = nil
            else
                st.frozen = true
                if authority.ended and not authority.reported then
                    log("borrowed party restore held: own keys/count or field state not proved")
                    authority.reported = true
                end
                return
            end
        end
        local base = st.battle and st.battle.base_keys
        if base and next(base) and #party > 0 then
            local overlap = false
            for _, m in ipairs(party) do if base[key(m)] then overlap = true; break end end
            st.frozen = not overlap
        else
            st.frozen = false
        end
        if st.frozen ~= was then log(st.frozen and "borrowed party in RAM: party frozen" or "own party restored") end
    end

    -- ── settles (one party read per frame that had signals) ────────────────────────
    local function settle_faints(party, area_id)
        for _, m in ipairs(party) do
            local k = key(m)
            if m.hp == 0 and st.alive[k] then
                st.alive[k] = nil
                send("faint", { key = k, area_id = area_id, level=m.level,
                    battle_evidence={in_battle=st.battle~=nil or in_battle(),
                        is_trainer=st.battle and st.battle.is_trainer==true or false} })
            end
        end
    end

    local function unknown_acquisition_count(party)
        local fresh, seen = 0, {}
        for _, m in ipairs(party) do
            local k = key(m)
            if not st.known[k] and not st.eggs[k] and m.is_egg == 0 and m.is_bad_egg == 0 then
                fresh, seen[k] = fresh + 1, true
            end
        end
        rescan_boxes()
        for _, e in ipairs(st.box_cache) do
            if not seen[e.key] and not st.known[e.key] and not st.eggs[e.key] and e.is_egg == 0 then
                fresh, seen[e.key] = fresh + 1, true
            end
        end
        return fresh
    end

    local function settle_acquisitions(party, area_id, caught)
        local gift = not caught
        local function resolve_area()
            if area_id ~= "" and not gift_area(area_id) and not gift then session.resolved_areas[area_id] = true end
        end
        local found = false
        for _, m in ipairs(party) do
            local k = key(m)
            if m.is_egg == 1 then st.eggs[k] = true end
            -- O-15: eggs are acquired at native hatch, never at GiveEgg.
            if not st.known[k] and not st.eggs[k] and m.is_egg == 0 and m.is_bad_egg == 0 then
                st.known[k], found = true, true
                resolve_area()
                local sent = send("capture", { key = k, area_id = area_id, species_id = m.species, level = m.level,
                                  hp = m.hp, maxHP = m.max_hp, nickname = m.nickname,
                                  held_item_id = m.held_item, is_egg = m.is_egg == 1,
                                  stats = stats_of(m), gift = gift or nil })
                log(string.format("ACQ capture via=settle:party key=%s species=%s gift=%s sent=%s", k, tostring(m.species),
                                  tostring(gift), tostring(sent)))
            end
        end
        if found then return end
        -- party full: the mon went to the PC (SendMonToPC)
        local before_n, known_boxed = #st.box_cache, 0
        for _, e in ipairs(st.box_cache) do if st.known[e.key] then known_boxed = known_boxed + 1 end end
        rescan_boxes()
        log(string.format("acquisition boxes: cache before=%d (known %d) now=%d ok=%s gen=%d",
                          before_n, known_boxed, #st.box_cache, tostring(st.boxes_ok), st.box_generation))
        local fresh = {}
        for _, e in ipairs(st.box_cache) do
            if e.is_egg == 1 then st.eggs[e.key] = true end
            if not st.known[e.key] and not st.eggs[e.key] and e.is_egg == 0 then fresh[#fresh + 1] = e end
        end
        for _, e in ipairs(fresh) do st.known[e.key] = true end
        if #fresh == 1 then
            local e = fresh[1]
            resolve_area()
            -- a boxed record has no party tail: the caught mon's stats and PP are still in the
            -- enemy party record it was copied from (old client :3950-3985 read the same foe)
            local src
            local b = battle_now()
            for _, m in ipairs(b and b.enemy_party or {}) do if key(m) == e.key then src = m end end
            local stats = src and stats_of(src) or nil
            local sent = send("capture", { key = e.key, area_id = area_id, in_box = true, species_id = e.species_id,
                              nickname = e.nickname, held_item_id = e.held_item_id,
                              level = stats and stats.level or nil, maxHP = stats and stats.maxHP or nil,
                              stats = stats, is_egg = e.is_egg == 1, gift = gift or nil })
            log(string.format("ACQ capture via=settle:box key=%s species=%s gift=%s sent=%s", e.key, tostring(e.species_id),
                              tostring(gift), tostring(sent)))
        elseif #fresh == 0 then
            -- the signal named an acquisition but every party and boxed key was already known: nothing is reported
            log(string.format("ACQ none via=settle caught=%s party=%d known_already=all (no capture sent)", tostring(not gift), #party))
        elseif #fresh > 1 then
            -- more than one unknown boxed key cannot be attributed to this acquisition
            log("acquisition: " .. #fresh .. " new boxed keys, none reported (ambiguous)")
        end
    end

    local function settle_hatches(hatches)
        for _, m in ipairs(hatches or {}) do
            local k = key(m)
            st.eggs[k] = nil
            if not st.known[k] then
                st.known[k] = true
                local sent = send("capture", { key = k, area_id = "gift_daycare", species_id = m.species,
                                  level = m.level, hp = m.hp, maxHP = m.max_hp,
                                  nickname = m.nickname, held_item_id = m.held_item,
                                  is_egg = false, stats = stats_of(m), gift = true })
                log(string.format("ACQ capture via=settle:hatch key=%s species=%s sent=%s", k, tostring(m.species), tostring(sent)))
            end
        end
    end

    local function settle_pc(party, area_id, released)
        local now = {}
        for _, m in ipairs(party) do now[key(m)] = m end
        rescan_boxes()
        local boxed = {}
        for _, e in ipairs(st.box_cache) do boxed[e.key] = true end
        for k, prev in pairs(st.party_prev) do
            if not now[k] and not st.carried[k] then
                session.identity:departure(k)
                st.carried[k] = prev
            end
        end
        for k, prev in pairs(st.carried) do
            if boxed[k] then
                st.carried[k] = nil
                send("party_to_box", { key = k, stats = prev.stats })
            elseif now[k] then
                st.carried[k] = nil                            -- a party shuffle, not a move
            elseif released and released[k] then
                st.carried[k] = nil
            end
        end
        for k in pairs(released or {}) do
            st.known[k], st.alive[k] = nil, nil
            owe("release", {key = k})
        end
        for k in pairs(now) do
            if not st.party_prev[k] then
                if st.known[k] then send("box_to_party", { key = k, area_id = area_id })
                elseif now[k].is_egg == 1 then st.eggs[k] = true
                elseif not st.eggs[k] then
                    st.known[k] = true -- eggs still await native hatch
                    log(string.format("ACQ known via=settle:pc key=%s species=%s (no capture sent)", k, tostring(now[k].species)))
                end
            end
        end
    end

    -- OPEN kind (not PHYSICAL; G3_request_draft.md:72-74): an in-game NPC trade replaces the
    -- record at the traded slot. Reported as key_change reason npc_trade (the Gen 1 contract);
    -- never for a link trade (docs/protocol.md §3.2), so skipped while a native trade runs.
    local function emit_identity_change(old, k, m, party, reason)
        st.known[k] = true
        log(string.format("ACQ known via=%s key=%s old=%s species=%s (key_change, no capture)", reason, k, tostring(old), tostring(m.species)))
        if m.hp and m.hp > 0 then st.alive[k] = true end
        session.identity:begin_alias(old, k, m, party, io.framecount())
        -- Keep the exact message on the alias for the existing retryable box-census refusal.
        session.identity.pending.msg = { old_key = old, new_key = k, reason = reason,
                                         new_species = m.species, new_nickname = m.nickname }
        send("key_change", session.identity.pending.msg)
    end
    local function settle_trade(party)
        local before = st.trade
        st.trade = nil
        if not before or (native and native.trade_active and native:trade_active()) then return end
        for _, m in ipairs(party) do
            local old = before[m.slot]
            local k = key(m)
            if old and old ~= k then
                emit_identity_change(old, k, m, party, "npc_trade")
            end
        end
    end
    local function settle_nature(party)
        local pair = nature_completed
        nature_completed = nil
        if not pair or pair.epoch ~= trade_reset_epoch then return end
        local mon = party[pair.after.slot + 1]
        if mon and key(mon) == pair.after.key and pair.before.key ~= pair.after.key
           and mon.is_egg ~= 1 and mon.is_egg_flag ~= 1 then
            emit_identity_change(pair.before.key, pair.after.key, mon, party, "nature_change")
        end
    end

    local function check_area()
        local area_id, loc = area_now()
        local here = area_id .. "|" .. loc
        if st.last_area ~= nil and st.last_area ~= here then
            send("area_enter", { area_id = area_id, loc_name = loc })
            if st.has_pokeballs and session.seeded and area_id ~= "" and not gift_area(area_id)
               and not session.resolved_areas[area_id] and not in_battle() then
                hud.show("** NEW ENCOUNTER **  " .. loc, 255, 220, 60, 240)
            end
        end
        st.last_area = here
        return area_id, loc
    end

    -- per-battle facts the end of the battle needs: the foe and whether it was a trainer
    local function note_battle(b, announce)
        local bt = st.battle
        if not (bt and b) then return end
        if b.is_trainer ~= nil then bt.is_trainer = b.is_trainer end
        if num(a.BATTLE_TYPE_ADDR) then bt.type_flags = io.read_u32(a.BATTLE_TYPE_ADDR) end
        if not bt.foe then
            for _, m in ipairs(b.enemy_party or {}) do
                if m.species and m.species ~= 0 then bt.foe = { species = m.species, level = m.level }; break end
            end
        end
        -- one announcement per battle id (OMP F5): a battle a pre-battle announcement already named
        -- is never announced again, even when its battle_begin refused the carry
        if announce ~= false and b.is_trainer
           and bt.battle_id and bt.battle_id == st.pre_announced_id then
            bt.trainer_sent = true
        end
        if announce ~= false and b.is_trainer
           and num(b.trainer_id) and b.trainer_id ~= 0 and not bt.trainer_sent then
            bt.trainer_sent = true
            bt.trainer_id = b.trainer_id
            -- battle_id rides with the announcement: it is what this battle's
            -- replace_rival_team must echo back (docs/protocol.md).
            if session_nonce then
                send("trainer_battle_start", { trainer_id = b.trainer_id,
                                               battle_id = bt.battle_id, session = session_nonce })
                rival_authority = {session = session_nonce, battle_id = bt.battle_id,
                                   trainer_id = b.trainer_id,
                                   rejected = sig_src and sig_src.rejected}
            else
                -- no identity to announce: the server will treat this client as legacy
                send("trainer_battle_start", { trainer_id = b.trainer_id })
            end
        end
    end

    local function finish_battle(area_id)
        local bt = st.battle
        st.battle = nil
        session.pending_safe = true
        if not bt then return end
        local b = battle_now()
        local caught = bt.caught or (b and num(d.OUTCOME_CAUGHT) and b.outcome == d.OUTCOME_CAUGHT)
        -- is_trainer must be KNOWN false: a pack without the trainer mask never dead-zones an
        -- area on a guess (an unknown battle type is not evidence of a failed encounter).
        -- †Limit: FRLG ghost/old-man-tutorial battles need a pack BATTLE_TYPE_NO_CATCH_MASK.
        local exempt = num(d.BATTLE_TYPE_NO_CATCH_MASK) and bt.type_flags
                       and (bt.type_flags & d.BATTLE_TYPE_NO_CATCH_MASK) ~= 0
        if not caught and bt.is_trainer == false and not exempt and bt.foe and st.has_pokeballs
           and area_id ~= "" and not gift_area(area_id) and not session.resolved_areas[area_id] then
            session.resolved_areas[area_id] = true
            send("no_catch", { area_id = area_id, species_id = bt.foe.species, level = bt.foe.level })
        end
    end

    local function settle()
        local f = st.flags
        local h = st.held_acq
        if h then
            local hidden = recovery_hidden()                     -- re-reads the journal: refreshes journal.busy
            local now = io.framecount()
            if st.trading_now() or st.trade ~= nil then h.tainted = true end
            if journal.failure or (hidden and not journal.busy) then
                st.held_acq = nil
                log(string.format("ACQ dropped reason=journal_owns held=%d frames caught=false", now - h.since))
            elseif hidden then
                if now - h.since > st.acq_hold_limit then       -- bounded: then drop, LOUDLY and player-visibly, never silently
                    st.held_acq = nil
                    log(string.format("ACQ expired after=%d frames reason=recovery_hidden retained=false DROPPED area=%s", now - h.since, tostring(h.area)))
                    hud.show("Capture not recorded - check your party / tell the host", 255, 60, 60, 300)
                end
            else
                st.held_acq = nil
                if h.tainted then
                    log(string.format("ACQ dropped reason=trade_during_hold held=%d frames", now - h.since))
                else
                    f.acquire, f.acq_area = true, h.area
                    log(string.format("ACQ resumed after=%d frames (journal visible again) area=%s", now - h.since, tostring(h.area)))
                end
            end
        end
        if not next(f) and not nature_completed then return end
        if f.save and io.saveram then pcall(io.saveram) end
        f.save = nil -- a host flush is one-shot even while a journal read is temporarily hidden
        if recovery_hidden() then
            -- LOST-CAPTURE (docs/protocol.md 6.2 item 5). recovery_hidden() is true whenever the journal cannot be READ, and a busy lock is
            -- unreadable: any other process holding ROOT/slink_gen3_trade.guard makes read() answer LOCK_BUSY (a sync client, an indexer,
            -- a backup, a second BizHawk; run.lua counts a CreateNew that sees an existing guard as busy too), so journal:hidden() is true
            -- although the journal holds no record. An unreadable journal is not a trade-owned party change. Only the ACQUISITION is
            -- treated specially; every other flag keeps the rule it always had here (cleared):
            --   * a wild catch (capture_wild: the battle engine's Cmd_givecaughtmon, which no trade can produce) is reported NOW. Its
            --     key needs no journal (party_read reads RAM, the key is personality:ot_id), so nothing is held;
            --   * a gift / PC move (mon_given / pc_move, whose trade ambiguity is real) is held as a bounded, acquisition-only
            --     record (st.held_acq) that carries the area and the trade-ownership verdict of the SIGNAL frame.
            -- A journal that shows entries or a failure, or a posted trade, still owns the change: the signal is dropped, by name.
            local posted = st.trade_apply and (st.trade_apply.posted or st.trade_apply.possibly_posted)
            local busy_only = journal and journal.busy and not journal.failure and not posted
            local owned = f.acq_trade or f.trade or st.trade ~= nil or st.trading_now()
            if f.acquire and busy_only and not owned and f.caught then
                st.flags = {acquire = true, caught = true, acq_area = f.acq_area, acq_busy = true}
                f = st.flags
                log(string.format("ACQ journal_busy reported_now=true caught=true area=%s (unreadable journal; a wild catch is not a trade)",
                                  tostring(f.acq_area)))
            else
                if f.acquire then
                    local h = st.held_acq
                    if busy_only and not owned and h then
                        if h.area ~= f.acq_area then
                            log(string.format("ACQ held area_conflict first=%s second=%s (the first is kept)", tostring(h.area), tostring(f.acq_area)))
                        end
                    elseif busy_only and not owned and st.baselined then
                        st.held_acq = {since = io.framecount(), caught = false, area = f.acq_area}
                        log(string.format("ACQ held reason=recovery_hidden retained=true caught=false area=%s", tostring(f.acq_area)))
                    else
                        log(string.format("ACQ held reason=recovery_hidden retained=false caught=%s why=%s", tostring(f.caught),
                                          not busy_only and "journal_owns" or owned and "trade_owned" or "no_baseline"))
                    end
                end
                st.flags = {}
                st.trade, f.trade = nil, nil
                return
            end
        end
        st.flags = {}
        local party = party_read(f.pc)                          -- a PC settle reads occupancy
        if not party then
            f.save = nil
            st.flags = f                                        -- try again next frame
            return
        end
        update_frozen(party)
        local area_id = area_now()
        if st.battle and in_battle() then note_battle(battle_now()) end
        if f.acquire and not st.baselined and f.acq_busy then
            -- LOST-CAPTURE F4: a wild catch while the journal has been unreadable since startup. The foe being caught is the new mon:
            -- learn every OTHER key as the baseline (its personality is the foe's) and let settle report the foe.
            if not st.frozen then
                local b, foe = battle_now(), {}
                for _, m in ipairs(b and b.enemy_party or {}) do if m.personality then foe[m.personality] = true end end
                rescan_boxes(); seed_known(party, "baseline")
                local excluded = 0
                local function unknow(k)
                    local pid = tonumber(tostring(k):match("^(%x+):"), 16)
                    if pid and foe[pid] and st.known[k] then st.known[k] = nil; excluded = excluded + 1 end
                end
                for _, m in ipairs(party) do unknow(key(m)) end
                for _, e in ipairs(st.box_cache) do unknow(e.key) end
                st.baselined = true
                log(string.format("ACQ baseline taken with the caught foe excluded (%d key(s)) before reporting it", excluded))
            end
        elseif f.acquire and not st.baselined then
            -- nothing tells a pre-existing key from a new one yet: report none, learn them all
            log("acquisition signal before a party baseline exists: not reported")
            f.acquire = nil
            if not st.frozen then rescan_boxes(); seed_known(party, "baseline"); st.baselined = true end
        end
        -- a PC trade in flight (and its settle window) swaps a party slot: nothing about it is a
        -- capture, a deposit or a key_change (docs/protocol.md §6.2 item 5, §6.5); the TradeMons
        -- site fires during the native scene and is ignored here
        -- from the FIRST ACTUAL POST (an owned op's own dispatch receipt published its opcode),
        -- not from the queued phase: a post that is refused or held writes nothing, and until
        -- something is written the party can only change the ordinary way, so ordinary reduction
        -- keeps going
        local trading = st.trading_now()
        if trading then st.trade = nil end
        if f.battle_end and not f.acquire and st.battle and st.battle.is_trainer == false
           and not st.frozen and not trading then
            local fresh = unknown_acquisition_count(party)
            if fresh == 1 then
                f.acquire, f.caught = true, true
                f.acq_area = st.battle.area_id or area_id
                st.battle.caught = true
                log(string.format("ACQ fallback via=battle_end area=%s unknown=1",
                                  tostring(f.acq_area)))
            elseif fresh > 1 then
                log(string.format("ACQ fallback ambiguous via=battle_end area=%s unknown=%d",
                                  tostring(st.battle.area_id or area_id), fresh))
            end
        end
        if f.acquire and (st.frozen or trading) then
            -- the flags were already cleared above: this acquisition signal is dropped, not retried
            log(string.format("ACQ skipped reason=%s dropped=true caught=%s", st.frozen and "frozen" or "trading", tostring(f.caught)))
        end
        if not st.frozen and not trading then
            if f.faint or f.battle_end or f.whiteout then settle_faints(party, area_id) end
            settle_hatches(f.hatches)
            if f.acquire then settle_acquisitions(party, f.acq_area or area_id, f.caught or (st.battle and st.battle.caught)) end
            if f.pc then settle_pc(party, area_id, f.release) end
            if f.trade then settle_trade(party) end
            if nature_completed then settle_nature(party) end
        end
        if f.whiteout and not st.frozen then
            send("whiteout", {battle_evidence={in_battle=st.battle~=nil or in_battle(),
                is_trainer=st.battle and st.battle.is_trainer==true or false}})
            hud.show("WHITED OUT", 255, 60, 60, 300)
        end
        if f.battle_end then finish_battle(area_id) end
        if f.map then check_area() end
        if not st.frozen then rebaseline(party) end
    end

    -- ── in-battle writes (owner ruling 2026-09-23: parity with RR on vanilla) ──────
    local explode_capable = num(a.BATTLE_MONS_ADDR) and num(a.CHOSEN_ACTION_ADDR)
                            and num(a.CHOSEN_MOVE_ADDR) and num(a.BATTLE_COMM_ADDR) and true or false
    -- Mechanism P has its own required fields, independent of the Explosion binding.
    local expansion_perish = num(a.BATTLE_MONS_ADDR) and num(a.CHOSEN_ACTION_ADDR)
                             and num(a.BATTLE_COMM_ADDR) and num(d.BATTLE_MON_PERISH_FLAG_OFF)
                             and num(d.BATTLE_MON_PERISH_FLAG_MASK) and num(d.BATTLE_MON_PERISH_TIMER_OFF)
                             and num(d.BATTLE_MON_PERISH_TIMER_KEEP) and num(d.B_ACTION_NOTHING_FAINTED)
                             and policy.handoff_entry and policy:handoff_entry(0) and true or false
    local vanilla_perish = num(a.BATTLE_MONS_ADDR) and num(a.STATUS3_ADDR)
                                 and num(a.DISABLE_STRUCTS_ADDR) and num(a.CHOSEN_ACTION_ADDR)
                                 and num(a.BATTLE_COMM_ADDR) and num(d.STATUS3_PERISH_SONG)
                                 and num(d.DISABLE_STRUCT_SIZE) and num(d.DISABLE_STRUCT_PERISH_TIMER_OFF)
                                 and num(d.B_ACTION_NOTHING_FAINTED) and true or false
    local active_faint_capable = expansion_perish or vanilla_perish

    -- one armed window, one reason, one allow set; returns true or nil, why
    -- args: what the reason's clause set needs (battle_commit: {battler}; sound: {player, track})
    --
    -- C5-11d addendum (the LeafGreen sound stall): a plan is applied WHOLE or not at all. Every
    -- entry's address, width and value is checked against writes.lua's own uint rules BEFORE the
    -- window is armed, so a bad entry anywhere (LG's garbage SE header made the sound plan's LAST
    -- value, status, overflow after 11 of 12 entries had landed) is refused with zero bytes
    -- written. What can still stop a plan mid-way is a live safety refusal between entries;
    -- writes.attempted makes that one loud.
    local UINT_MAX = { [1] = 255, [2] = 65535, [4] = 4294967295 }
    local function uint_ok(v, max)
        return type(v) == "number" and v % 1 == 0 and v >= 0 and v <= max
    end
    local function armed_write(reason, plan, args)
        if recovery_hidden() then return nil, "trade recovery withholds party writes" end
        if args then args.plan = plan end                     -- G4-PH: the policy judges the plan itself
        for i, w in ipairs(plan) do
            local max = UINT_MAX[w[2]]
            if not (max and uint_ok(w[1], 4294967296 - w[2]) and uint_ok(w[3], max)) then
                return nil, string.format("%s plan entry %d/%d is not writable (addr=%s width=%s "
                                          .. "value=%s); nothing written", reason, i, #plan,
                                          tostring(w[1]), tostring(w[2]), tostring(w[3]))
            end
        end
        local snap, why = policy:snapshot()
        if not snap then return nil, why end
        local ok, cwhy = policy:check(snap, reason, args)
        if not ok then return nil, cwhy end
        local allow = {}
        for _, w in ipairs(plan) do allow[w[1] .. ":" .. w[2]] = true end
        local before = writes.attempted
        local wok, err = pcall(function()
            writes:arm(reason, function(x, n) return allow[x .. ":" .. n] == true end, args)
            -- G4-PH §3.3: a plan ending in the controller hand-off carries two self-invalidating
            -- writes (comm, then the slot); it is validated once and written whole
            if plan.handoff then return writes:write_plan(plan) end
            for _, w in ipairs(plan) do
                if w[2] == 4 then writes:write_u32(w[1], w[3])
                elseif w[2] == 2 then writes:write_u16(w[1], w[3])
                else writes:write_bytes(w[1], { w[3] }) end
            end
        end)
        writes:disarm()
        if not wok then
            local attempted = (writes.attempted or 0) - (before or 0)
            if attempted > 0 then
                err = string.format("PARTIAL %s write: %d byte(s) attempted, partial mutation possible: %s",
                                    reason, attempted, tostring(err))
                log(err)
            end
            return nil, tostring(err)
        end
        return true
    end

    local function faint_plan(slot, battler)
        local hp = party_hp_addr(slot)
        if not hp then return nil end
        local plan = { { hp, 2, 0 } }
        if battler and num(a.BATTLE_MONS_ADDR) then
            plan[2] = { a.BATTLE_MONS_ADDR + battler * reads.BATTLE_MON_SIZE + reads.BATTLE_MON_HP_OFF, 2, 0 }
        end
        return plan
    end

    -- The Variant-3 menu skip (archive/gen3-old-client:lua/memory_gba.lua:1303-1345): every move slot of the battler
    -- reads Explosion, and the action-commit state says "already chosen", so the action menu is
    -- skipped. Addresses come from each title's pack; a pack without those facts stays held.
    local function commit_plan(battler, with_moves)
        local plan = {}
        if with_moves then
            local base = a.BATTLE_MONS_ADDR + battler * reads.BATTLE_MON_SIZE
            for i = 0, 3 do
                plan[#plan + 1] = { base + BATTLE_MON_MOVES_OFF + i * 2, 2, MOVE_EXPLOSION }
                plan[#plan + 1] = { base + BATTLE_MON_PP_OFF + i, 1, EXPLODE_PP }
            end
        end
        plan[#plan + 1] = { a.CHOSEN_ACTION_ADDR + battler, 1, B_ACTION_USE_MOVE }
        plan[#plan + 1] = { a.CHOSEN_MOVE_ADDR + battler * 2, 2, MOVE_EXPLOSION }
        local bs = num(a.BATTLE_STRUCT_PTR_ADDR) and io.read_u32(a.BATTLE_STRUCT_PTR_ADDR) or 0
        if bs ~= 0 then
            if num(d.BATTLE_STRUCT_CHOSEN_MOVE_POS_OFF) then
                plan[#plan + 1] = { bs + d.BATTLE_STRUCT_CHOSEN_MOVE_POS_OFF + battler, 1, 0 }
            end
            if num(d.BATTLE_STRUCT_MOVE_TARGET_OFF) then
                plan[#plan + 1] = { bs + d.BATTLE_STRUCT_MOVE_TARGET_OFF + battler, 1, TARGET_FOE_PRIMARY }
            end
        end
        -- the committing state is itself the battle_commit guard (gBattleCommunication[battler]
        -- < STANDBY, the pack's commit_guard.value) and writes.lua re-validates before every write, so only the hand-off may follow it
        plan[#plan + 1] = { a.BATTLE_COMM_ADDR + battler, 1, STANDBY }
        -- The pinned Explode+H shape ends the menu without a press on every bound title.
        local h = policy.handoff_entry and policy:handoff_entry(battler, "explode")
        if h then plan[#plan + 1] = { h[1], h[2], h[3] }; plan.handoff = true end
        return plan
    end

    -- force_explode on the active battler. The old client settled on HP 0 / switch-out /
    -- battle end, and otherwise after a 600-frame wall-clock timer wrote HP 0 into the ACTIVE
    -- battler (the write the force_faint path deliberately avoids, :2555-2596). Here the
    -- timer is replaced by an engine witness: Explosion's PP dropped (the move executed) and a
    -- new action selection began (gBattleCommunication < standby) with HP still > 0, i.e. the
    -- user survived (Damp). The entry then degrades to the force_faint rule for an active
    -- battler: held until switch-out or battle end. UNVERIFIED on hardware (no emulator lane).
    local function explode_step(e, slot, mon, battler)
        -- The proved window is controller 0. Never strand a menu with a tail-less commit.
        if not (policy.handoff_entry and policy:handoff_entry(battler, "explode")) then
            return "hold", "active battler"
        end
        local base = a.BATTLE_MONS_ADDR + battler * reads.BATTLE_MON_SIZE
        local bhp = io.read_u16(base + reads.BATTLE_MON_HP_OFF)
        local pp0 = io.read_u8(base + BATTLE_MON_PP_OFF)
        local comm = io.read_u8(a.BATTLE_COMM_ADDR + battler)
        local ex = e.explode
        if ex and ex.failed then return "hold", ex.why end
        if not STANDBY then return "hold", "pack has no battle.commit_guard.value" end
        -- A pre-existing multi-turn lock (Thrash/Outrage/Rollout: gLockedMoves[battler] ~= 0)
        -- owns the next action; committing Explosion over it would fight the engine. Held as
        -- the active battler instead, and nothing is written. Sleep and flinch need no case:
        -- the move never executes, PP never drops, and the commit is re-armed each turn until
        -- it does or the battler leaves. Liveness of every branch here is NOT PHYSICAL.
        if not ex and num(a.LOCKED_MOVES_ADDR) and io.read_u16(a.LOCKED_MOVES_ADDR + battler * 2) ~= 0 then
            e.explode = { battler = battler, failed = true, why = "move locked; held as the active battler" }
            log("force_explode: battler " .. battler .. " is move-locked; held as active " .. e.key)
            return "hold", "move locked; held as the active battler"
        end
        if ex and bhp == 0 then
            e.explode = nil
            hud.show("!! " .. hud_name(mon.nickname) .. " BOOM!", 255, 80, 80, 360)
            return "done"
        end
        if ex and pp0 < EXPLODE_PP then
            if comm < STANDBY then
                ex.failed, ex.why = true, "explosion failed; held as the active battler"
                log("force_explode: Explosion executed and the battler survived; held as active " .. e.key)
                return "hold", "explosion failed; held as the active battler"
            end
            return "hold", "explosion executing"
        end
        if ex and comm >= STANDBY then return "hold", "explosion committed" end
        -- first commit, or the engine reset the commit state at turn start: (re)write it
        local plan = commit_plan(battler, not ex)
        local ok, why = armed_write("battle_commit", plan, { battler = battler })
        if not ok then return "hold", why end
        if not ex then
            e.explode = { battler = battler }
            mark_commanded(key(mon))
            log("force_explode: menu skip committed slot=" .. slot .. " battler=" .. battler
                .. " handoff=" .. (plan.handoff and 1 or 0))
        end
        return "hold", "explosion committed"
    end

    -- Mechanism P (docs/gen3/research/active_faint_in_battle_scope_2026-09-23.md §2d; owner
    -- ruling PLAN §0 "In-battle faint"): at the parked action menu, the battler gets the Perish
    -- flag with its counter at 0 and a committed no-op action. The turn runs, and the end-of-turn
    -- Perish check (FR/LG HandleWishPerishSongOnTurnEnd, RR CFRU end-turn state 34) runs the
    -- engine's own BattleScript_PerishSongTakesLife: HP drain, datahpupdate (gBattleMons AND the
    -- party), tryfaintmon ("X fainted!"), then the vanilla send-out / whiteout. No HP byte is
    -- written here. The no-op commit is what stops the mon acting (or Baton Passing the Perish
    -- flag on). gStatuses3 and the timer byte are read on the same parked frame.
    -- G4-PH (P+H, owner rulings 15-18; rr_active_faint_parity_scope_2026-09-23.md §3.2): when the
    -- pack proves battle.handoff, the plan ends by handing the controller slot to
    -- PlayerBufferExecCompleted, AFTER comm = 3 (with comm 1 the stale buffer-B action replays).
    -- The engine then ends the menu itself: no A press on any title, and CFRU's parked menu (the
    -- RR L-throw) is gone. Without the block the plan is P alone and the player presses A once.
    local function perish_plan(battler)
        local plan
        if expansion_perish then
            local base = a.BATTLE_MONS_ADDR + battler * reads.BATTLE_MON_SIZE
            local status = base + d.BATTLE_MON_PERISH_FLAG_OFF
            local timer = base + d.BATTLE_MON_PERISH_TIMER_OFF
            plan = {
                { status, 1, io.read_u8(status) | d.BATTLE_MON_PERISH_FLAG_MASK },
                { timer, 1, io.read_u8(timer) & d.BATTLE_MON_PERISH_TIMER_KEEP },
            }
        else
            local s3 = a.STATUS3_ADDR + battler * 4
            local timer = a.DISABLE_STRUCTS_ADDR + battler * d.DISABLE_STRUCT_SIZE + d.DISABLE_STRUCT_PERISH_TIMER_OFF
            plan = {
                { s3, 4, io.read_u32(s3) | d.STATUS3_PERISH_SONG },
                -- timer 0 (low nibble); the high nibble is kept, as the engine's own decrement does
                { timer, 1, io.read_u8(timer) & 0xF0 },
            }
        end
        plan[#plan + 1] = { a.CHOSEN_ACTION_ADDR + battler, 1, d.B_ACTION_NOTHING_FAINTED }
        -- comm is the battle_commit guard: only the hand-off may follow it
        plan[#plan + 1] = { a.BATTLE_COMM_ADDR + battler, 1, STANDBY }
        local h = policy.handoff_entry and policy:handoff_entry(battler)
        if h then plan[#plan + 1] = { h[1], h[2], h[3] }; plan.handoff = true end
        return plan
    end

    -- commit -> held until gBattleMons[b].hp == 0 (done: the engine's faint, or a foe KO first).
    -- The battler leaving (Roar) takes battle_write's bench path; the battle ending first (flee,
    -- catch) takes its overworld path. A reset commit (comm < STANDBY) with the mon alive is re-armed;
    -- mid-turn the permit refuses that, so it can only land at a parked menu.
    -- Review follow-up 2: the commit's comm[b] = STANDBY fails every later battle_faint (battle_comm_0)
    -- until the next parked menu, which comes AFTER the Perish KO's party screen, where a bench
    -- mon still alive could be sent in. So the commit waits while another pending entry resolves
    -- to a bench slot: that entry lands later in this same flush (same permit, minus the guard)
    -- and the commit follows next frame. Only this step is delayed; the core's arrival order of
    -- battle_pending and each entry's arrival stamp (684bbb7a) are untouched.
    local function bench_write_pending(e, b)
        local party = party_read()
        if not party then return false end
        for _, o in ipairs(session.battle_pending) do
            if o ~= e then
                local slot = session.identity:find_party_slot(o.key, party)
                if slot and not battler_of(b, slot) then return true end
            end
        end
        return false
    end

    local function active_faint_step(e, mon, battler, b)
        if not STANDBY then return "hold", "pack has no battle.commit_guard.value" end
        local bhp = io.read_u16(a.BATTLE_MONS_ADDR + battler * reads.BATTLE_MON_SIZE + reads.BATTLE_MON_HP_OFF)
        if e.perish and bhp == 0 then
            hud.show("!! " .. hud_name(e.nickname, mon.nickname) .. " fainted", 255, 80, 80, 360)
            return "done"
        end
        -- the carrier's contract (W2, G4-PH): e.why is exactly "active faint committed" after a
        -- hand-off; a pack without battle.handoff keeps the press hint
        if e.perish and io.read_u8(a.BATTLE_COMM_ADDR + battler) >= STANDBY then
            return "hold", e.handoff and "active faint committed" or "active faint committed (press A)"
        end
        if bench_write_pending(e, b) then return "hold", "bench write first" end
        local plan = perish_plan(battler)
        local ok, why = armed_write("battle_commit", plan, { battler = battler })
        if not ok then return "hold", why end
        e.handoff = plan.handoff == true
        if not e.perish then
            e.perish = true
            mark_commanded(key(mon))                          -- the engine's faint is not an echo
            log("force_faint: Perish commit battler=" .. battler .. " handoff=" .. (e.handoff and 1 or 0)
                .. " " .. e.key)
        end
        return "hold", e.handoff and "active faint committed" or "active faint committed (press A)"
    end

    local function battle_write(e, slot, mon, ending)
        local k = key(mon)
        if ending then
            -- the battle is over: the overworld checkpoint rarely holds on the exit frame, so this
            -- normally declines and the core hands the entry to the checkpoint queue
            local plan = faint_plan(slot, nil)
            if plan and armed_write("overworld", plan) then mark_commanded(k); return "done" end
            return nil
        end
        local b = battle_now()
        local battler = battler_of(b, slot)
        if battler then
            if e.cmd == "force_explode" and explode_capable and battler == 0
               and b.battlers_count == 2 and b.is_doubles ~= true then
                return explode_step(e, slot, mon, battler)
            end
            -- P is the linked-faint path ONLY (owner 2026-09-23: Explode Mode is untouched, so a
            -- non-capable force_explode keeps its hold below). Singles only: in doubles a partner
            -- B-cancel resets battler 0's commit while the Perish flag stays (scope doc §2.1), and
            -- D1-D5 are signed limits.
            if e.cmd == "force_faint" and active_faint_capable and battler == 0
               and b.battlers_count == 2 and b.is_doubles ~= true then
                return active_faint_step(e, mon, battler, b)
            end
            -- Held until it switches out or the battle ends: a party-only HP write is undone by the
            -- next datahpupdate, and a both-words write is a silent faint (scope doc §1.4, §2a).
            return "hold", "active battler"
        end
        local plan = faint_plan(slot, nil)
        if not plan then return "hold", "party base unreadable" end
        local ok, why = armed_write("battle_faint", plan)
        if not ok then return "hold", why end                  -- C4-B: refusal = hold, not defer
        mark_commanded(k)
        hud.show("!! " .. hud_name(e.nickname, mon.nickname) .. " KO'd", 255, 80, 80, 360)
        return "done"
    end

    -- ── the checkpoint executors (the deferred queue) ──────────────────────────────
    local exec = {
        arm = function() end,                                  -- each executor arms its own window
        disarm = function() writes:disarm() end,
        faint_slot = function(slot)
            local plan = assert(faint_plan(slot, nil), "party base unreadable")
            local ok, why = armed_write("overworld", plan)
            assert(ok, why)
            local party = party_read() or {}
            for _, m in ipairs(party) do if m.slot == slot then mark_commanded(key(m)) end end
        end,
        deposit = function(k, hint)
            if not boxes then return nil, "no box module" end
            return boxes:deposit(k, hint)
        end,
        withdraw = function(k, stats, nickname)
            if not boxes then return nil, "no box module" end
            return boxes:withdraw(k, stats, nickname)
        end,
        memorialize = function(k, hint)
            if not boxes then return nil, "no box module" end
            return boxes:memorialize(k, hint)
        end,
        stats_of = stats_of,
        command_state=function(cmd)
            local party=party_read(true)
            if not party or st.frozen or recovery_hidden() then return nil end
            rescan_boxes()
            if not st.boxes_ok then return nil end
            local np,nb=0,0
            for _,m in ipairs(party) do if key(m)==cmd.key then np=np+1 end end
            for _,m in ipairs(st.box_cache) do if m.key==cmd.key then nb=nb+1 end end
            if np+nb~=1 then return nil end
            return cmd.cmd=="box_mon" and nb==1 or cmd.cmd=="party_mon" and np==1
        end,
        -- every byte moves through the one sink; its ATTEMPT counter moves before each external
        -- write, so an error after the first attempted byte reads as a (possible) mutation
        write_count = function() return writes.attempted end,
        -- our own moves must not read as the player's: re-baseline after every one
        rescan = function()
            rescan_boxes()
            local party = party_read()
            if party then seed_known(party, "rescan"); rebaseline(party) end
        end,
    }
    local memorial_box = boxes and boxes.memorial_box or ((num(d.BOXES_PER_STORE) or 1) - 1)

    -- ── the driver ─────────────────────────────────────────────────────────────────
    local drv = { commands = {} }
    drv.frame = function() return io.framecount() end
    drv.read_party = party_read
    function drv.report_ot()
        local t=trainer()
        return t and type(t.ot_id)=="number" and string.format("%08X",t.ot_id) or nil
    end
    drv.in_battle = in_battle
    drv.battle_write = battle_write
    -- KEY-SCOPE-5: the core's key_change retry hook. st.box_generation only bumps on a complete
    -- rescan, so it IS the raw generation counter core/session.lua needs. This is the same
    -- accessor the hello census field reads, not a second one that could drift away from it.
    drv.box_generation = box_generation
    drv.rescan_boxes = rescan_boxes
    function drv.party_borrowed()
        local party = party_read()
        if party then update_frozen(party) end
        return st.frozen or recovery_hidden()
    end

    function drv.game_is_live()
        local party, why = party_read()
        if not party then return false, why or "party unreadable" end
        if reads.read_sb2 and not reads.read_sb2() then return false, "save blocks not set" end
        local t = trainer()
        if t and t.ot_id == 0 and #party == 0 then return false, "pre-game (title/new game)" end
        reload_witness_live = true
        return true
    end
    function drv.save_cleared()
        if reads.read_sb2 and not reads.read_sb2() then return true end
        local t = trainer()
        return t ~= nil and t.ot_id == 0
    end
    local function overworld_ok()
        local snap, why = policy:snapshot()
        if not snap then return false, why end
        return policy:check(snap, "overworld")
    end
    -- the deferred queue's gate: box moves wait while a PC trade swaps a party slot
    -- (docs/protocol.md §6.2 item 6)
    function drv.checkpoint_ok()
        if recovery_hidden() then return false, "trade recovery withholds party" end
        if st.frozen then return false, "borrowed party withholds writes" end
        if st.trade_apply then return false, "PC trade in flight" end
        return overworld_ok()
    end
    function drv.hello_ready()
        local live, why = drv.game_is_live()
        if not live then return false, why end
        if in_battle() then return true end
        return overworld_ok()
    end
    function drv.on_reset()
        st.release_snapshot = nil
        nature_preimage = nil
        nature_completed = nil
        borrowed_party = nil
        st.eggs = {}
        trade_reset_epoch = trade_reset_epoch + 1
        -- Pre-CONTINUE validation can see OT=0 after a real cleared boot interval.
        -- That is still the same boot episode. A save clear after live play revokes
        -- its earlier witness; a subsequent cleared interval may establish a new one.
        if reload_witness_live then reload_boot_seen = false end
        if trade then trade:reset() end
        st.known, st.alive, st.commanded, st.party_prev, st.carried = {}, {}, {}, {}, {}
        st.box_cache, st.boxes_ok, st.battle, st.frozen, st.flags = {}, false, nil, false, {}
        st.last_area, st.trade = nil, nil
        st.opp_seen, st.pre_announced_id, st.held_acq = nil, nil, nil
        st.baselined, st.seen_count, st.observe_at = false, nil, nil
        st.trade_apply, st.trade_settle_until = nil, 0
        -- C5-11d MAJOR 3: the battle the authority named is gone with the save, whatever the
        -- battle RAM still says; a queued job must not ride it (reducer lifecycle or not)
        rival_authority = nil
    end
    function drv.on_disconnect()
        trade_connected = false
        if trade_run_id then awaiting_trade_run = true end
    end

    -- One read-only cartridge snapshot per session, including cached failure. The Gen 3
    -- reader owns bounds/pointer walking; this hook only binds table metadata and transport.
    local content_cache = {attempted=false}
    local function rom_content()
        if p.artifact_kind ~= "rand" and p.artifact_kind ~= "rand_companion" then return nil end
        if not content_cache.attempted then
            content_cache.attempted = true
            local ok, payload, why = pcall(function()
                local source = assert(profile.rom_tables, "profile has no rom_tables")
                local tables = {rom_size=p.rom_size} -- reader's documented 32 MiB window default if absent
                for _, name in ipairs({"gTrainers", "gWildMonHeaders", "gEvolutionTable", "gSpeciesInfo", "gTrainerClassNames"}) do
                    local row = assert(source[name], "missing ROM table " .. name)
                    tables[name] = {address=tonumber(row.address), count=tonumber(row.count), size=tonumber(row.size)}
                end
                local reader = assert(p.rom_content_new, "ROM content reader unavailable")(tables, {read_u8=io.read_u8})
                return reader:payload()
            end)
            if ok and type(payload) == "table" and type(payload.tables) == "table" and type(payload.fingerprint) == "string" then
                content_cache.payload = payload
            else
                log("rom_content unavailable: " .. tostring(ok and (why or "invalid reader result") or payload))
            end
        end
        return content_cache.payload
    end
    -- No wire side effect here (no area_enter, no banner): hello is the connection's first line.
    function drv.hello_fields()
        if not trade_connected and p.net.connected() then
            trade_epoch, trade_connected = trade_epoch + 1, true
        end
        local party = party_read(true)
        if not party then
            log("hello held: party census unavailable")
            return nil
        end
        update_frozen(party)
        rescan_boxes()
        -- a borrowed party is never published nor learned as ours (docs/protocol.md §9 item 11):
        -- hello.party stays present and empty, exactly like tick_fields' guard
        local hidden = st.frozen or (trade and trade:hide_party()) or recovery_hidden()
        session.hello_visible = not hidden
        if not hidden then st.trade_hello_pending = nil end
        local own = hidden and {} or party
        -- Once a baseline exists hello REPORTS and never learns: the session builds hello before
        -- this frame's signals drain, so a mon added since the last quiet frame (its acquisition
        -- hook firing this frame or the next) must stay unknown for settle to report it as a
        -- capture right after the hello. Only the very first baseline is taken here.
        if not st.baselined and not hidden then
            seed_known(own, "hello")                           -- box-key seeding at connect
            log(string.format("baseline(hello): boxes ok=%s n=%d gen=%d party=%d",
                              tostring(st.boxes_ok), #st.box_cache, st.box_generation, #own))
            rebaseline(party)
            st.baselined = true
            -- the quiet interval starts from THIS count: a mon added after hello is a change
            st.seen_count = num(a.PARTY_COUNT_ADDR) and io.read_u8(a.PARTY_COUNT_ADDR) or -1
        end
        latch_balls(false)                                     -- a resume, not an acquisition
        local area_id, loc = area_now()
        st.last_area = area_id .. "|" .. loc
        local gen, gen_ok = box_generation()                -- advertised only when the scan was complete
        -- Companion is a local native capability; randomized pairing stays the
        -- existing wire kind and still carries its cartridge's ROM content.
        local f = { rom_type = p.rom_type, foundation = p.foundation, artifact_kind = p.artifact_kind == "rand_companion" and "rand" or p.artifact_kind,
                    rom_sha1 = p.rom_sha1, party = party_wire(own), pc_boxes = pc_boxes_wire(),
                    pc_boxes_generation = gen_ok and gen or nil,
                    area_id = area_id, loc_name = loc, has_pokeballs = st.has_pokeballs,
                    in_battle = in_battle(), badges = badges(), ball_count = ball_count() }
        if io.report_outbox then
            f.report_delivery=1
            f.report_backlog=io.report_outbox:pending_count()
        end
        if session_nonce then
            -- capability declaration (card C5-10b): this client mints and enforces the battle
            -- request identity, so the server may refuse an identity-less manual rival inject for
            -- it. Declared ONLY when the identity really exists (fail closed).
            f.battle_identity = true
        end
        local t = trainer()
        if t then f.ot_id, f.trainer_name, f.player_gender = t.ot_id, t.name, t.player_gender end
        if native and native.hello_fields then
            for k, v in pairs(native:hello_fields() or {}) do f[k] = v end
        end
        f.companion_abi = p.companion_abi and p.companion_abi() or nil  -- the cartridge's own mailbox, never the launcher's claim
        if hidden then
            f.party_hidden = true
            f.pc_boxes, f.pc_boxes_generation = nil, nil
        end
        local outstanding = journal and not awaiting_trade_run and journal:outstanding()
        if outstanding and #outstanding > 0 then f.trade_outstanding = outstanding end
        f.trade_prepare = not awaiting_trade_run and trade ~= nil and trade:capable() == true
        f.rom_content = rom_content()
        return f
    end

    function drv.tick_fields()
        local party = party_read(true)
        if not party then return nil end
        update_frozen(party)
        if not st.frozen and not recovery_hidden() then observe_hp(party) end
        local area_id, loc = check_area()
        local b = in_battle() and battle_now() or nil
        if b then note_battle(b) end
        local n = latch_balls(true)
        local f = { area_id = area_id, loc_name = loc, in_battle = b ~= nil, has_pokeballs = st.has_pokeballs,
                    is_trainer_battle = b and b.is_trainer or false, is_doubles = b and b.is_doubles or false,
                    trainer_id = b and b.is_trainer and num(b.trainer_id) or nil,
                    enemy_party = enemy_wire(b), ball_count = n, badges = badges() }
        if io.report_outbox then f.report_backlog=io.report_outbox:pending_count() end
        local hidden = st.frozen or (trade and trade:hide_party()) or recovery_hidden()
        if hidden then f.party_hidden = true else f.party = party_wire(party) end
        -- the ONE box-generation accessor, the same one hello_fields reads (review F4): the tick
        -- path can never publish a different verdict than the hello did
        local gen, gen_ok = box_generation()
        if gen_ok and not hidden then
            f.pc_boxes, f.pc_boxes_generation = pc_boxes_wire(), gen
        end
        local t = trainer()
        if t then f.trainer_name, f.player_gender = t.name, t.player_gender end
        return f
    end

    function drv.start()
        -- on_fire runs AT the fire, before the queue is drained: the only place a same-frame
        -- end+begin can be seen before native is serviced (see rival_authority above).
        local function close_authority(sig)
            local had = rival_authority
            rival_authority = nil                 -- first: a throwing log must not keep it open
            -- G5-RR-RIVAL: a PRE-battle announcement names the battle about to begin (battle_seq
            -- + 1). Its own battle_begin -- the first one, for the same trainer -- CARRIES it
            -- into that battle instead of closing it; any later boundary closes it as before.
            -- a WILD battle never carries it (review F2): the trainer type bit is set by
            -- BattleSetup_StartTrainerBattle before CB2_InitBattle, i.e. at this fire
            if had and had.pre and not had.begun and sig and sig.kind == "battle_begin"
               and had.battle_id == battle_seq + 1
               and io.read_u16(a.TRAINER_OPPONENT_ADDR) == had.trainer_id
               and num(d.BATTLE_TYPE_TRAINER_MASK)
               and (io.read_u32(a.BATTLE_TYPE_ADDR) & d.BATTLE_TYPE_TRAINER_MASK) ~= 0 then
                rival_authority = {session = had.session, battle_id = had.battle_id,
                                   trainer_id = had.trainer_id, rejected = had.rejected,
                                   pre = true, opened = had.opened, begun = io.framecount()}
                log("rival authority carried into battle " .. tostring(had.battle_id))
                return
            end
            if had then log("rival authority closed by " .. tostring(sig and sig.kind)) end
        end
        local function capture_trade_before(sig)
            sig.trade_reset_epoch = trade_reset_epoch
            local party, why = party_read()
            if not party then
                log("NPC trade preimage unavailable: " .. tostring(why))
                return
            end
            -- TradeMons entry still holds the outgoing identities. By signal
            -- drain time the swap is complete, and the quiet/PC cache may be empty.
            sig.trade_before = {}
            for _, mon in ipairs(party) do sig.trade_before[mon.slot] = key(mon) end
        end
        local function release_census()
            -- Read through this title's profile at the native hooks. The party
            -- count can still include the purged slot until compaction follows.
            local party = party_read()
            if not party then return nil end
            local census = {}
            for _, mon in ipairs(party) do
                if mon.has_species == 1 and mon.species ~= 0 then
                    local k = key(mon)
                    if census[k] then return nil end
                    census[k] = {where = "party", slot = mon.slot}
                end
            end
            for box = 0, (num(d.BOXES_PER_STORE) or 0) - 1 do
                local mons = call("read_box", box)
                if not mons then return nil end
                for _, mon in ipairs(mons) do
                    if mon.has_species == 1 and mon.species ~= 0 then
                        local k = key(mon)
                        if census[k] then return nil end
                        census[k] = {where = "box", box = box, slot = mon.slot}
                    end
                end
            end
            return census
        end
        local function release_begin(sig)
            st.release_snapshot = {keys = release_census(), epoch = trade_reset_epoch, sp = sig.sp, frame = sig.frame}
        end
        local function release_done(sig)
            local before = st.release_snapshot
            st.release_snapshot = nil
            if not before or not before.keys or before.epoch ~= trade_reset_epoch then return end
            -- A pack whose two sites sit mid-body in one task invocation (no push/pop between,
            -- SP delta 0) declares pairing = frame_window: same-invocation by framecount.
            -- Otherwise (every vanilla pack: no `pairing` key) ReleaseMon entry SP and its
            -- pre-pop return SP differ by the saved LR.
            local site = p.sites and p.sites.pc_release
            local pairing = site and site.pairing
            if pairing and pairing.mode == "frame_window" then
                local dt = sig.frame and before.frame and sig.frame - before.frame
                if not dt or dt < 0 or dt > (tonumber(pairing.window) or 0) then return end
            elseif pairing then return        -- declared but unknown mode: refuse, never the SP rule
            elseif before.sp ~= sig.sp + 4 then return end
            local after, gone = release_census(), nil
            if not after then return end
            for k in pairs(after) do if not before.keys[k] then return end end
            for k, location in pairs(before.keys) do
                if not after[k] then
                    if gone then return end -- ambiguous removal: no guessed key
                    gone = {key = k, location = location}
                end
            end
            if gone then
                sig.release_key, sig.release_source = gone.key, gone.location
                sig.release_epoch = trade_reset_epoch
            end
        end
        local function capture_hatch(sig)
            -- Pinned AddHatchedMonToParty returns leave the mon pointer in R5 (FR/LG/RR/Emerald);
            -- the expansion build keeps it in R4 (R5 is the species/save pointer): R5 first.
            -- Read AT the completed mutation, before later callbacks can move the party.
            local base, ptr = call("party_base"), sig.point and (sig.point.R5 or sig.point.R4)
            local party = party_read()
            if not base or not ptr or not party then return end
            for _, mon in ipairs(party) do
                if ptr == base + mon.slot * R.PARTY_MON_SIZE and mon.species ~= 0 and mon.is_egg == 0
                   and mon.is_bad_egg == 0 and mon.checksum_ok ~= false then
                    sig.hatch_mon, sig.hatch_epoch = mon, trade_reset_epoch
                    return
                end
            end
        end
        local function nature_record(sig)
            local ptr, base = sig.point and sig.point.R4, call("party_base")
            local count = io.read_u8(a.PARTY_COUNT_ADDR)
            if not ptr or not base or not count or count < 1 or count > 6
               or ptr < base or (ptr - base) % R.PARTY_MON_SIZE ~= 0 then return end
            local slot = (ptr - base) // R.PARTY_MON_SIZE
            if slot >= count then return end
            local party = party_read()
            local mon = party and party[slot + 1]
            if not mon or mon.slot ~= slot or mon.checksum_ok == false
               or mon.has_species ~= 1 or mon.is_bad_egg ~= 0
               or mon.is_egg == 1 or mon.is_egg_flag == 1 then return end
            local pid, ot = io.read_u32(ptr), io.read_u32(ptr + 4)
            if key(mon) ~= string.format("%08X:%08X", pid, ot) then return end
            return {ptr=ptr, slot=slot, pid=pid, ot=ot, species=mon.species,
                    key=key(mon), epoch=trade_reset_epoch}
        end
        local function nature_begin(sig)
            nature_preimage = nature_record(sig)
        end
        local function nature_done(sig)
            local before = nature_preimage
            nature_preimage = nil
            if not before or before.epoch ~= trade_reset_epoch
               or not sig.point or sig.point.R4 ~= before.ptr then return end
            local after = nature_record(sig)
            if not after or after.slot ~= before.slot or after.ot ~= before.ot
               or after.pid == before.pid or after.species ~= before.species then return end
            sig.nature_before, sig.nature_after, sig.nature_epoch = before, after, trade_reset_epoch
        end
        local function borrowed_begin(sig)
            if borrowed_party then return end -- a nested begin never replaces the own-party base
            local party = party_read()
            local keys, valid = {}, party and #party >= 1 and #party <= 6
            if valid then
                for _, mon in ipairs(party) do
                    local k = key(mon)
                    if not k or keys[k] or mon.has_species ~= 1 or mon.is_bad_egg ~= 0
                       or mon.checksum_ok == false then valid = false; break end
                    keys[k] = true
                end
            end
            borrowed_party = {keys=keys, count=party and #party or 0,
                              epoch=trade_reset_epoch, invalid=not valid}
            st.frozen = true -- before queued command handling or signal drain
            if not valid then log("borrowed party begin held: own-party preimage unavailable") end
        end
        local function borrowed_end(sig)
            if borrowed_party and borrowed_party.epoch == trade_reset_epoch then
                borrowed_party.ended = true
            end
        end
        sig_src = p.Signals.new(profile, p.sites, io, p.ev,
                                {battle_begin = close_authority, battle_end = close_authority,
                                 whiteout = function(sig)
                                     close_authority(sig)
                                     sig.borrowed_party = borrowed_party ~= nil
                                 end,
                                 borrowed_party_begin = borrowed_begin, borrowed_party_opponent_begin = borrowed_begin, borrowed_party_end = borrowed_end,
                                 trade_begin = capture_trade_before,
                                 pc_release_begin = release_begin, pc_release = release_done,
                                 hatch = capture_hatch,
                                 nature_change_begin = nature_begin, nature_change = nature_done})
        return sig_src
    end

    function drv.on_signal(sig)
        local k, f = sig.kind, st.flags
        if k == "battle_begin" then
            -- the pre-battle party is the borrowed-party reference
            local base = {}
            for pk in pairs(st.party_prev) do base[pk] = true end
            battle_seq = battle_seq + 1
            -- both halves of the request identity live on the epoch record: the guard below and
            -- the harness read them from here, never from a second copy
            st.battle = { caught = false, base_keys = base, battle_id = battle_seq,
                          area_id = area_now(),
                          session = session_nonce }
            note_battle(battle_now(), false)
            -- the battle a pre-battle announcement already named: never announce it twice (the
            -- server queues one swap per trainer_battle_start)
            local ra = rival_authority
            if ra and ra.pre and ra.begun and ra.battle_id == battle_seq then
                st.battle.trainer_sent, st.battle.trainer_id = true, ra.trainer_id
            end
        elseif k == "battle_end" then f.battle_end = true
        elseif k == "faint" then f.faint = true
        elseif k == "poison_faint" then f.faint = true          -- OPEN kind (FR only), not PHYSICAL
        elseif k == "whiteout" and not sig.borrowed_party then f.whiteout = true
        elseif k == "capture_wild" then
            f.acquire, f.caught = true, true
            f.acq_area = f.acq_area or (area_now())             -- the area of the SIGNAL, whatever settle reads later
            if st.trading_now() or st.trade ~= nil then f.acq_trade = true end
            if st.battle then st.battle.caught = true end
            log(string.format("ACQ signal kind=capture_wild frame=%d in_battle=%s", io.framecount(), tostring(st.battle ~= nil)))
        elseif k == "mon_given" or k == "pc_move" then
            f.acquire = true
            f.acq_area = f.acq_area or (area_now())
            if st.trading_now() or st.trade ~= nil then f.acq_trade = true end
            log(string.format("ACQ signal kind=%s frame=%d in_battle=%s", k, io.framecount(), tostring(st.battle ~= nil)))
        elseif k == "hatch" and sig.hatch_mon and sig.hatch_epoch == trade_reset_epoch then
            f.hatches = f.hatches or {}
            f.hatches[#f.hatches + 1] = sig.hatch_mon
        elseif PC_KINDS[k] then
            f.pc = true
            if k == "pc_release" and sig.release_key and sig.release_epoch == trade_reset_epoch then
                f.release = f.release or {}
                f.release[sig.release_key] = true
            end
        elseif k == "map_load" then f.map = true
        elseif k == "save" then f.save = true
        elseif k == "trade_begin" then                          -- OPEN kind, not PHYSICAL
            st.trade = sig.trade_reset_epoch == trade_reset_epoch and sig.trade_before or nil
        elseif k == "trade_done" then f.trade = true            -- OPEN kind, not PHYSICAL
        elseif k == "nature_change" and sig.nature_before and sig.nature_epoch == trade_reset_epoch then
            local pending = nature_completed
            if pending then
                if (pending.epoch == trade_reset_epoch and pending.after.key == sig.nature_before.key
                    and pending.after.ptr == sig.nature_before.ptr
                    and pending.after.slot == sig.nature_before.slot
                    and pending.after.ot == sig.nature_before.ot) then
                    nature_completed = {before=pending.before, after=sig.nature_after, epoch=trade_reset_epoch}
                else
                    nature_completed = nil  -- a different record cannot inherit the first old key
                end
            else
                nature_completed = {before=sig.nature_before, after=sig.nature_after, epoch=trade_reset_epoch}
            end
            f.nature = true
        end
        -- evolve_species_store / trade_evolve_species_store (OPEN, not PHYSICAL): a Gen 3
        -- evolution keeps PID:OTID, so the key is unchanged and the next tick carries the
        -- species. poison_hp_before / pc_release_begin bookkeeping and frame_control: nothing.
    end
    -- The pre-existing baseline (R4), independent of hello: what is in the party/boxes on a
    -- QUIET frame (no engine change signalled) is not an acquisition. The first live quiet
    -- frame baselines at once (a loaded save, or the script starting mid-game). After that a
    -- party-count change is learned only after it has stayed quiet across one frame boundary,
    -- so a gift whose GiveMonToPlayer straddles a frame end (party written this frame, return
    -- hook next frame) is still settled as an acquisition, not absorbed. A signalled frame
    -- belongs to settle, which runs after this hook.
    local function observe_known()
        if next(st.flags) or st.held_acq then st.observe_at = nil; return end   -- a held acquisition's key is never absorbed as known
        local f = io.framecount()
        local count = num(a.PARTY_COUNT_ADDR) and io.read_u8(a.PARTY_COUNT_ADDR) or -1
        -- the quiet interval belongs to one candidate count: a change inside it restarts it
        if st.baselined and st.observe_at == nil then
            if count ~= st.seen_count then st.observe_at, st.observe_count = f + 1, count end
            return
        end
        if st.baselined and count ~= st.observe_count then
            st.observe_at, st.observe_count = f + 1, count
            return
        end
        if st.baselined and f < st.observe_at then return end
        st.observe_at = nil
        if not drv.game_is_live() then return end
        if st.battle or in_battle() then return end
        local party = party_read()
        if not party then return end
        update_frozen(party)
        if st.frozen or recovery_hidden() then return end      -- withheld RAM is never a baseline
        -- A settled quiet count-change re-baselines the boxes with the party. A cold boot
        -- baselines at party=0 / boxes n=0 before CONTINUE loads the save; without this rescan
        -- the pre-existing boxed keys stay unknown and the next boxed gift reads as ambiguous.
        -- Quiet frames only (st.flags empty above): a new boxed mon signals on its own frame.
        -- ONE scan per settle: the first baseline logs after it (it used to scan twice).
        local first = not st.baselined
        rescan_boxes()
        if first then
            st.baselined = true
            log(string.format("baseline(quiet): boxes ok=%s n=%d gen=%d party=%d",
                              tostring(st.boxes_ok), #st.box_cache, st.box_generation, #party))
        end
        seed_known(party)
        st.seen_count = count
    end
    -- G5-RR-RIVAL: the PRE-BATTLE announcement. The patch's rival-swap window (W1,
    -- gBattleMainFunc == BeginBattleIntroDummy) is ~5 frames long and opens in the battle_begin
    -- frame itself, while the in_battle read (gBattleMons[0].maxHP) first holds ~30 frames AFTER
    -- it closes (live receipt rr_rival_swap_real_gen3_gen3_rr_as_a_4202b5d8: window 1012-1016,
    -- announcement 1043). The trainerbattle script sets gTrainerBattleOpponent_A ~120 frames
    -- before the battle (891 there), so a trainer battle is announced on that value's EDGE on the
    -- field, with the id of the battle about to begin (battle_seq + 1); its battle_begin carries
    -- the authority in (drv.start) and the staged swap posts in the window (native.lua).
    -- RR companion only (native.rival_window_open); every other artifact keeps note_battle's
    -- in-battle announcement. The value stays set after a battle, so only a CHANGE counts: a
    -- rematch against the SAME id is not pre-announced and falls back to note_battle (the value
    -- is tracked every frame, so a stale post-battle id is never an edge).
    local function pre_announce()
        if not (native and native.rival_window_open and session_nonce and num(a.TRAINER_OPPONENT_ADDR)) then
            return
        end
        local opp = io.read_u16(a.TRAINER_OPPONENT_ADDR)
        local seen = st.opp_seen
        st.opp_seen = opp
        if opp == 0 or seen == nil or opp == seen then return end   -- the first read only latches
        if st.battle or in_battle() then return end
        -- a NOT-YET-BEGUN pre-authority is replaced, never a wall (review F1): the opponent id is
        -- loaded before the script's defeated-trainer check (pret FR ScrCmd_trainerbattle ->
        -- TrainerBattleLoadArgs, then the flag test), so talking to a beaten trainer announces a
        -- battle that never begins; the next trainer's edge must still be announced. A job staged
        -- for the replaced authority fails its guard (trainer mismatch) and is refused.
        local old = rival_authority
        if old and not (old.pre and not old.begun) then return end
        local id = battle_seq + 1
        -- only an announcement that left (or is held for hello) opens the authority (review F4):
        -- a dropped one would stage nothing the server ever answers
        if not send("trainer_battle_start", { trainer_id = opp, battle_id = id, session = session_nonce }) then
            log("trainer " .. opp .. " pre-battle announcement dropped (not connected)")
            return
        end
        rival_authority = {session = session_nonce, battle_id = id, trainer_id = opp,
                           rejected = sig_src and sig_src.rejected, pre = true,
                           opened = io.framecount()}
        st.pre_announced_id = id
        log("trainer " .. opp .. " announced before battle " .. id
            .. (old and (" (replaces trainer " .. tostring(old.trainer_id) .. ")") or ""))
    end
    drv.frame_hooks = { observe_known, settle, pre_announce }

    -- ── command seams ──────────────────────────────────────────────────────────────
    local C = drv.commands
    -- §4.3 selectable-team pre-filter (C5-11a). The engine's own rule, getter semantics:
    -- GetMonData(MON_DATA_SPECIES_OR_EGG) collapses an empty slot, an egg and a BAD EGG to
    -- SPECIES_EGG (pret src/pokemon.c:3245-3249), plus HP != 0, so the predicate is
    -- hp != 0 and species != 0 and not is_egg_flag and not is_bad_egg. Doubles needs two
    -- distinct such mons. Decode-only, from the blobs the command carries. The patch remains
    -- the authority for the WINDOW; this is a pre-filter that keeps an unselectable team from
    -- ever being staged.
    local function selectable_team(cmd)
        -- doubles from the battle's own type flags (C5-11c MAJOR 6): gBattlersCount is not
        -- initialised yet in W1, so the count was the wrong witness.
        local double_mask = d.BATTLE_TYPE_DOUBLE_MASK
        local need = (num(a.BATTLE_TYPE_ADDR) and num(double_mask)
                      and (io.read_u32(a.BATTLE_TYPE_ADDR) & double_mask) ~= 0) and 2 or 1
        local viable = 0
        for _, hex in ipairs(cmd.blobs_hex or {}) do
            local raw = {}
            for i = 1, 200, 2 do raw[#raw + 1] = tonumber(hex:sub(i, i + 1), 16) end
            local mon = reads.decode_party_mon(raw)
            -- getter semantics (pret src/pokemon.c:3182-3183, 3245-3249): MON_DATA_SPECIES_OR_EGG
            -- returns SPECIES_EGG when species != 0 && (SECURE Misc.isEgg || boxMon.isBadEgg) --
            -- the decoded `is_egg` is that secure bit; the header `is_egg_flag` is not enough.
            if mon and mon.hp ~= 0 and mon.species ~= 0 and mon.is_egg == 0
               and mon.is_bad_egg == 0 then
                viable = viable + 1
            end
        end
        if viable >= need then return true end
        return false, "slots_unviable"
    end

    -- The dispatch-time revalidation (C5-11a BLOCKER). The job carries an IMMUTABLE epoch snapshot
    -- and this runs immediately before the post, so a job queued behind a menu cannot act after
    -- the battle it was minted for has ended -- the case Codex reproduced (battle1/sessionS/id1,
    -- battle2 same trainer/id2, the old request posting into battle2). `st.battle` alone is not
    -- enough: lua/core/session.lua services native BEFORE this frame's signal drain, which is why
    -- pre_pump sweeps the boundary first (below) and why the live reads are checked here too.
    -- C5-11d MAJOR 4: the authority is closed BY signals, so it cannot outlive their delivery. A
    -- source that failed (signals.lua:126 -- a fire can fail before on_fire runs), was closed
    -- (hooks uninstalled), was never armed, or has rejected a callback since the authority opened
    -- (a boundary fire counted and dropped, signals.lua:97-100) may have missed the boundary that
    -- should have closed it. Read here, at the ONE consumer, from the source's own fields, so no
    -- path that stops delivery -- however its flag got set -- leaves the authority usable.
    local function live_authority()
        local s, a = sig_src, rival_authority
        if a and not (s and not s.failure and not s.closed and s.rejected == a.rejected) then
            rival_authority = nil
            log("rival authority revoked: engine signals are not being delivered")
        end
        return rival_authority
    end
    local function rival_epoch_guard(epoch)
        -- the AUTHORITY, not the reducer's st.battle: it is closed at signal capture, so a job
        -- minted for an earlier battle can never post after a same-frame boundary (BLOCKER 2).
        local now = live_authority()
        if now == nil then return false, "stale_battle_id" end
        if now.session ~= epoch.session or now.battle_id ~= epoch.battle_id
           or now.trainer_id ~= epoch.trainer_id then
            return false, "stale_battle_id"
        end
        -- a pre-announced battle (G5-RR-RIVAL) posts in the W1 window, before the in_battle read
        -- holds: its own battle_begin must have carried the authority (`begun`); the patch's
        -- five-part check stays the authority for the window itself
        if not in_battle() and not (now.pre and now.begun) then return false, "not_in_battle" end
        local live = battle_now()
        if not (live and live.is_trainer and num(live.trainer_id)
                and live.trainer_id == epoch.trainer_id) then
            return false, "stale_battle_id"
        end
        return true
    end
    -- G5-RR-RIVAL: how long a pre-announced swap may wait in native.lua's queue for the W1 window
    -- (native job `ready`): true = keep holding. Only a pre-battle authority holds; the battle must
    -- begin within RIVAL_HOLD frames of the announcement and the window open within RIVAL_WINDOW
    -- frames of battle_begin. When the hold ends the job is dispatched anyway and its guard (or the
    -- patch's REASON_WINDOW_CLOSED) refuses it cleanly -- a hold never becomes a silent drop.
    local RIVAL_HOLD, RIVAL_WINDOW = 3600, 30
    local function rival_hold(epoch)
        local now = live_authority()
        if not (now and now.pre and now.session == epoch.session and now.battle_id == epoch.battle_id
                and now.trainer_id == epoch.trainer_id) then
            return false
        end
        if not now.begun then return io.framecount() - now.opened < RIVAL_HOLD end
        return io.framecount() - now.begun < RIVAL_WINDOW
    end

    -- rival swap is a companion-mailbox feature (OP_RIVAL_SWAP 28, C5-11a; 16 stays the trade's).
    -- Without the patch: the old client's refusal (archive/gen3-old-client:lua/clients/gen3_frlge_client.lua:824-837).
    C.replace_rival_team = function(cmd)
        local function refuse(why)
            send("rival_team_replaced", { trainer_id = cmd.trainer_id or 0, species_ids = arr({}),
                                          error = why })
        end
        local trainer = cmd.trainer_id
        local current = st.battle
        local ra = live_authority()
        if ra and ra.pre and session_nonce ~= nil and type(cmd.session) == "string"
           and cmd.session == ra.session and cmd.battle_id == ra.battle_id
           and type(trainer) == "number" and trainer == ra.trainer_id then
            -- G5-RR-RIVAL: the swap for a PRE-announced battle (usually still on the field). It is
            -- STAGED in native.lua and posted in the W1 window. Selectability is checked ONLY at
            -- dispatch (OMP F6): on the field gBattleTypeFlags still holds the LAST battle's bits,
            -- so a stale doubles bit would refuse a singles swap here.
            if not eligible() then
                log("replace_rival_team: session not eligible (writes paused); nothing staged")
                refuse("writes_paused")
            elseif native and native.replace_rival_team then
                log("replace_rival_team staged for the window (battle " .. tostring(ra.battle_id) .. ")")
                native:replace_rival_team(cmd, function(epoch)
                    local gok, gwhy = rival_epoch_guard(epoch)
                    if not gok then return gok, gwhy end
                    return selectable_team(cmd)
                end, rival_hold)
            else
                refuse("patch_required")
            end
        elseif not in_battle() then
            refuse("not_in_battle")
        -- MAJOR 1 (Codex C5-10 review): the no-epoch case FIRST -- a client attached mid-battle
        -- has no identity to compare against and must refuse by name, never index a nil record.
        elseif session_nonce == nil then
            -- fail closed: no identity was minted, so nothing can be validated
            log("replace_rival_team refused: stale_battle_id (no session nonce)")
            refuse("stale_battle_id")
        elseif current == nil then
            log("replace_rival_team refused: stale_battle_id (no epoch)")
            refuse("stale_battle_id")
        elseif type(cmd.battle_id) ~= "number" or cmd.battle_id % 1 ~= 0
            or type(cmd.session) ~= "string" or cmd.session ~= current.session
            or cmd.battle_id ~= current.battle_id then
            log("replace_rival_team refused: stale_battle_id (counter=" .. tostring(cmd.battle_id)
                .. " session=" .. tostring(cmd.session) .. " current="
                .. tostring(current.battle_id) .. "/" .. tostring(current.session) .. ")")
            refuse("stale_battle_id")
        -- trainer matching is mandatory and has no shortcuts: a missing, non-numeric, zero,
        -- out-of-range or non-matching id is refused, and so is an epoch whose trainer is unknown.
        elseif type(trainer) ~= "number" or trainer % 1 ~= 0 or trainer <= 0 or trainer > 65535
            or type(current.trainer_id) ~= "number" or current.trainer_id ~= trainer then
            log("replace_rival_team refused: stale_battle_id (trainer=" .. tostring(trainer)
                .. " current=" .. tostring(current.trainer_id) .. ")")
            refuse("stale_battle_id")
        else
            local ok, why = selectable_team(cmd)
            if not ok then
                log("replace_rival_team refused: " .. why)
                refuse(why)
            elseif not eligible() then
                log("replace_rival_team: session not eligible (writes paused); nothing staged")
                refuse("writes_paused")
            elseif native and native.replace_rival_team then
                native:replace_rival_team(cmd, rival_epoch_guard)
            else
                refuse("patch_required")
            end
        end
        return true
    end

    -- Durable trade is a separate protocol owner. It never writes; native owns every stage.
    -- V1 companions have no save witness and therefore cannot implement this capability.
    local function sync_trade()
        if not trade then return end
        local active, prepared = trade:state()
        st.trade_apply = active or prepared
    end
    if p.Trade and owed and (p.artifact_kind == "companion" or p.artifact_kind == "rand_companion") and (native or journal) then
        local trade_policy = assert(p.trade_policy, "pack trade policy required")
        trade = p.Trade.new({native=native or {trade_capable=function() return false end}, journal=journal,
            frame=io.framecount, eligible=function() return not awaiting_trade_run and eligible() end,
            intent_committed=function()
                reload_empty_frames, reload_boot_seen, reload_check_frame = 0, false, -1000
            end,
            saveram=function()
                assert(io.saveram, "SaveRAM host API unavailable")
                return io.saveram()
            end,
            log=log, hud=hud,
            epoch=function() return trade_epoch end,
            prepare_frames=trade_policy.prepare_frames, apply_frames=trade_policy.apply_frames,
            ready_to_send=function()
                return not awaiting_trade_run and (not journal or journal:ready())
                    and session and session.hello_sent and p.net.connected()
            end,
            publish_visible_snapshot=function()
                if not session or not session:eligible() then return false end
                local fields = drv.tick_fields()
                if type(fields) ~= "table" or fields.party_hidden == true
                   or type(fields.party) ~= "table" or #fields.party == 0 then return false end
                -- The same TCP stream orders this fresh census before apply_ready. A pre-save
                -- hidden tick must not be the server's last view when it commits the pair.
                return session:send("tick", fields) == true
            end,
            clear=function() return not in_battle() and overworld_ok() end,
            party=party_read, key=key, capacity=d.PARTY_CAPACITY or 6, mon_size=R.PARTY_MON_SIZE,
            decode_blob=function(hex)
                local bytes = {}
                for i=1,#hex,2 do bytes[#bytes+1] = tonumber(hex:sub(i,i+1),16) end
                return reads.decode_party_mon(bytes)
            end,
            report_pending=function(epoch, value)
                for _, row in ipairs(owed.list) do
                    if row.event == "trade_done" and row.fields.token == value
                       and trade_report_epochs[row.fields] == epoch then return true end
                end
                return false
            end,
            send=function(event, fields, epoch)
                if event == "trade_done" then
                    trade_report_epochs[fields] = epoch
                    owed.list[#owed.list+1] = {event=event, fields=fields}
                    return true
                end
                return send(event, fields)
            end,
            completed=function(t, changed, party, mon)
                if changed then
                    local new_key = key(mon)
                    st.known[t.old_key], st.alive[t.old_key], st.commanded[t.old_key] = nil, nil, nil
                    st.known[new_key] = true
                    log(string.format("ACQ known via=trade:completed key=%s old=%s (link trade, no capture)", new_key, tostring(t.old_key)))
                    local items = session.deferred.items
                    for i=#items,1,-1 do
                        local q=items[i]
                        if (q.key == t.old_key or q.key == new_key)
                           and (q.cmd == "box_mon" or q.cmd == "party_mon" or q.cmd == "memorialize") then
                            table.remove(items,i)
                        end
                    end
                    seed_known(party, "trade"); rebaseline(party)
                    st.trade_settle_until = io.framecount() + st.trade_limits.settle
                end
                -- The write-ahead interval withheld our party from the server.
                -- Publish the now-durable (or proved unchanged) party before
                -- releasing trade_done. Core gates this HELLO on its field
                -- checkpoint and sends it before owed reports. Another hidden
                -- party reason must not turn that ordering into a false proof.
                if t.journaled and journal and journal:ready() and not journal:hidden() then
                    st.trade_hello_pending, st.trade_hello_check = true, nil
                    session.hello_visible = false
                    session.hello_sent = false
                end
            end})
    end
    C.apply_prepare = function(cmd)
        if trade then trade:prepare(cmd); sync_trade()
        else send("apply_ready", {token=cmd.token, ok=false}) end
        return true
    end
    local function hold_busy_apply(cmd)
        if not (trade and journal and journal.busy and not awaiting_trade_run) then return false end
        local _, prepared = trade:state()
        if not prepared or prepared.token ~= cmd.token or prepared.old_key ~= cmd.old_key then return false end
        if not pending_apply_trade then
            pending_apply_trade = {cmd=cmd, run_id=trade_run_id, ot_id=trade_run_ot,
                                   prepared=prepared, epoch=prepared.epoch,
                                   deadline=prepared.prepare_deadline}
            log("apply_trade held: journal lock busy")
        end
        return true
    end
    C.apply_trade = function(cmd)
        if trade and trade:capable() then trade:apply(cmd); sync_trade()
        elseif hold_busy_apply(cmd) then return true
        else
            log("apply_trade refused: durable native trade unavailable")
            -- A replay during journal recovery cannot truthfully report
            -- "unchanged"; the earlier intent may already have committed.
            -- Its qualified uncertainty declaration owns the next report.
            if journal and journal:hidden() then return true end
            if type(cmd.token) == "string" and cmd.token ~= "" and type(cmd.old_key) == "string" and cmd.old_key ~= "" then
                local fields = {token=cmd.token, slot=cmd.slot, new_key=cmd.old_key, new_species=0}
                local cancel = {token=cmd.token, choice=0, withdraw=true}
                if owed then
                    -- This branch owes a NON-uncertain trade_done, and the owed gate
                    -- (drv.after_receive) refuses one while the hello is hidden.
                    -- session.hello_visible only refreshes when a NEW hello is BUILT, and
                    -- core/session.lua sends one only while hello_sent is false -- so without
                    -- an armer the report (and the menu_result cancel behind it) stalls
                    -- until a reconnect. trade.lua's completed() is the other armer; arm the
                    -- same recovery here. The report stays NON-uncertain on purpose: this
                    -- refusal can only justify "unchanged", which is server semantics.
                    if not session.hello_visible then
                        st.trade_hello_pending, st.trade_hello_check = true, nil
                    end
                    owed.list[#owed.list+1] = {event="trade_done", fields=fields}
                    owed.list[#owed.list+1] = {event="menu_result", fields=cancel}
                else send("trade_done", fields); send("menu_result", cancel) end
            end
        end
        return true
    end
    C.withdraw_trade = function(cmd)
        if trade then trade:withdraw(cmd); sync_trade() end
        return true
    end
    C.trade_final = function(cmd)
        if journal and (awaiting_trade_run or (not journal:ready() and not journal.busy)) then return true end
        local ok, why
        if trade then ok, why = trade:server_final(cmd)
        elseif journal then ok, why = journal:final(cmd.token,cmd.epoch,cmd.verdict) end
        if why == "trade journal lock busy" then
            pending_trade_finals[#pending_trade_finals+1] = {
                token=cmd.token, epoch=cmd.epoch, verdict=cmd.verdict,
                run_id=trade_run_id, ot_id=trade_run_ot,
            }
        end
        return true
    end
    drv.after_receive = function()
        if trade then trade:tick(); sync_trade() end
        if owed then
            local refresh=false
            owed:step(p.net.connected(), session.hello_sent == true, function(event, fields)
                -- Uncertainty declarations deliberately precede the later
                -- visible recovery HELLO; gating those would deadlock recovery.
                if event == "trade_done" and not fields.uncertain
                   and (not session.hello_visible or st.frozen
                        or (trade and trade:hide_party()) or recovery_hidden()) then return false end
                local sent=send(event,fields)
                if sent and event == "trade_done" and fields.uncertain and fields.after_reset
                   and trade and trade:declaration_sent(fields.token) then
                    refresh=true
                end
                return sent
            end, function(event)
                if event ~= "trade_done" and event ~= "menu_result" then owed_hold = nil return true end
                local why = nil
                if awaiting_trade_run then why = "trade run not bound"
                elseif journal and not journal:ready() then why = "trade journal not ready" end
                if why then
                    -- owed:step runs on EVERY after_receive, and a held head blocks every
                    -- later report (lua/owed_reports.lua:62). Name the reason once per
                    -- hold; a second line would only be per-frame noise.
                    if owed_hold ~= why then
                        owed_hold = why
                        log(string.format("owed %s held: %s", event, why))
                    end
                    return false
                end
                owed_hold = nil
                return true
            end)
            -- Server only consumes a hello AFTER the uncertainty declaration. This second
            -- hello is allowed only after the real reset boundary, never from unsaved RAM.
            if refresh then session.hello_sent=false end
        end
    end
    -- native pickers/menus (RR PC trade NPC); otherwise the core answers with the cancel sentinels
    -- Once native takes a prompt it owns the answer (its done callback replies, including the
    -- cancel sentinel on refusal), so the command is CONSUMED: no second, core-made ACK.
    for _, name in ipairs(MENU_CMDS) do
        C[name] = function(cmd)
            if native and native[name] and eligible() then
                native[name](native, cmd)
                return true
            end
            return false
        end
    end
    C.link_panel = function(cmd)                               -- the RR info panel (native)
        if native and native.link_panel then native:link_panel(cmd) end
        return true
    end
    C.config = function(cmd)                                   -- the session stores it too
        if journal then
            local t = trainer()
            local ot = t and type(t.ot_id) == "number" and string.format("%08X",t.ot_id) or nil
            if type(cmd.run_id) ~= "string" or cmd.run_id == "" or not ot then
                journal:unbind()
                trade_run_id, trade_run_ot = nil, nil
                awaiting_trade_run = true
                log("trade journal binding refused: server run identity or trainer unavailable")
            elseif awaiting_trade_run or trade_run_id ~= cmd.run_id or trade_run_ot ~= ot then
                local ok, why = journal:bind(cmd.run_id,ot)
                if ok then
                    trade_run_id, trade_run_ot = cmd.run_id, ot
                    awaiting_trade_run = false
                    session.hello_sent = false
                else
                    journal:unbind()
                    trade_run_id, trade_run_ot, awaiting_trade_run = nil, nil, true
                    log("trade journal binding refused: " .. tostring(why))
                end
            end
        end
        if native and native.config then native:config(cmd) end
        return false
    end
    C.ghost_pos = function() return true end                   -- peer ghost: post-RC (PLAN §0)
    -- Sound: the native SE when the companion is present, else the old client's m4a SE1 poke
    -- (archive/gen3-old-client:lua/memory_gba.lua M.playSE) as a GATED write, writes:arm("sound", allow, {player, track})
    -- over exactly the pack block's fields. WHETHER it may land (driver initialised, addresses in
    -- IWRAM) is safety's sound clause set, re-checked before every byte; the client only
    -- resolves addresses. Any refusal means no sound, logged once per reason.
    local function sound_refused(why)
        if not st.sound_logged[why] then
            st.sound_logged[why] = true
            log("sound refused: " .. why)
        end
    end
    -- SE1: the pack's static gMPlayInfo_SE1 (FR/LG), else resolved at runtime from the pack's
    -- gSoundInfo pointer (MPlayOpen prepends, so the list is gMPlayTable reversed and SE1 is its
    -- second-to-last node). A pack naming neither (RR today) is refused by name.
    local function se1_player(snd)
        if type(snd.player_se1) == "table" and num(snd.player_se1.address) then
            return snd.player_se1.address
        end
        local ptr = type(snd.sound_info_ptr) == "table" and num(snd.sound_info_ptr.address) or nil
        if not ptr then return nil, "the pack's sound block names no SE1 player and no gSoundInfo pointer" end
        local lo, hi = snd.iwram_min, snd.iwram_max
        local function iw(v) return v >= lo and v < hi end
        local info = io.read_u32(ptr)
        if not iw(info) then return nil, "gSoundInfo not initialised" end
        local nodes, cur = {}, io.read_u32(info + snd.player_head_off)
        while iw(cur) and #nodes < 16 do nodes[#nodes + 1] = cur; cur = io.read_u32(cur + snd.player_next_off) end
        if #nodes < 2 then return nil, "m4a player list not initialised" end
        return nodes[#nodes - 1]
    end
    -- id: the TITLE's song id (drv.play_sound translates the wire id once, for both paths)
    local function m4a_plan(id)
        local snd = p.sound
        if type(snd) ~= "table" or type(snd.fields) ~= "table" then
            return nil, "no sound block in the checkpoint pack"
        end
        local headers = profile.rom and profile.rom.SE_SONG_HEADERS
        local hdr = type(headers) == "table" and num(headers[tostring(id)]) or nil
        if not hdr then return nil, "pack has no song header for SE " .. tostring(id) end
        local player, why = se1_player(snd)
        if not player then return nil, why end
        local track = io.read_u32(player + snd.tracks_off) + (snd.track0_off or 0)
        local F = {}
        for _, f in ipairs(snd.fields) do F[f.on .. "." .. f.name] = f end
        local function at(on, name)
            local f = assert(F[on .. "." .. name], "sound block has no field " .. on .. "." .. name)
            return (on == "player" and player or track) + f.offset, f.size
        end
        -- the header and the track-0 script it points at must be in ROM, and the track count a
        -- real m4a one (pret include/gba/m4a_internal.h MAX_MUSICPLAYER_TRACKS = 16): a garbage
        -- header (LG shipped FR's SE addresses) must be refused here, before any plan exists
        local function in_rom(v, len) return v >= 0x08000000 and v + (len or 1) <= 0x0A000000 end
        local se = "SE " .. tostring(id) .. " song header " .. string.format("0x%X", hdr)
        if not in_rom(hdr, 12) then return nil, se .. " is not in ROM" end
        local h = io.rom_read(hdr - 0x08000000, 12)
        local count, priority = h[1], h[3]
        local cmd_ptr = h[9] + h[10] * 256 + h[11] * 65536 + h[12] * 16777216
        -- ponytail: 16 is m4a's hard cap; SE1's own allocated tracks (gMPlayTable) are tighter,
        -- use them if a pack ever names them
        if not (count >= 1 and count <= 16) then
            return nil, se .. " has trackCount " .. count .. " (m4a allows 1..16)"
        end
        if not in_rom(cmd_ptr) then
            return nil, se .. string.format(" has a track pointer 0x%X outside ROM", cmd_ptr)
        end
        local plan = {}
        local function put(on, name, value, width)
            local addr, size = at(on, name)
            plan[#plan + 1] = { addr, width or size, value }
        end
        -- No ident lock/unlock: the frame-end callback runs between emulated instructions, so the
        -- ISR cannot see a half-set player, and the sound clause set (re-checked before every
        -- byte) requires ident == magic -- a lock value would refuse the rest of the plan.
        -- status goes LAST: it is what makes the next VBlank start the song.
        put("player", "songHeader", hdr)
        put("player", "trackCount", count)
        put("player", "priority", priority)
        put("player", "clock", 0)
        put("track", "flags", 0)
        put("track", "flags", TRK_START, 1)
        put("track", "bendRange", TRK_BEND)
        put("track", "volX", TRK_VOLX)
        put("track", "lfoSpeed", TRK_LFO)
        put("track", "chan", 0)
        put("track", "cmdPtr", cmd_ptr)
        put("player", "status", (1 << count) - 1)
        return plan, { player = player, track = track }
    end
    drv.play_sound = function(id)
        if not eligible() then return sound_refused("session not eligible") end
        local f = io.framecount()
        if st.sound_frame == f then return end              -- one cue per frame
        st.sound_frame = f
        -- the wire id keeps FR numbering (docs/protocol.md "play_sound ids"); the pack's se_ids
        -- maps it to this title's song id, and an id it does not map is refused. Translated ONCE,
        -- before either path: the companion's OP_PLAY_SE plays a song number of the running ROM
        -- (native.lua play_sound), exactly what the m4a poke needs, so both take the title id.
        -- (RR's map is identity today; asserting identity would only refuse a future native title.)
        local snd = type(p.sound) == "table" and p.sound or {}
        local sid = type(snd.se_ids) == "table" and num(snd.se_ids[tostring(id)]) or nil
        if not sid then return sound_refused("pack maps no title SE for wire id " .. tostring(id)) end
        if native and native.play_sound and native:play_sound(sid) then return end
        local plan, args = m4a_plan(sid)
        if not plan then return sound_refused(args) end
        local ok, awhy = armed_write("sound", plan, args)
        if not ok then sound_refused(tostring(awhy)) end
    end
    local trade_frame
    drv.pre_pump = function()
        if trade_frame and io.framecount() < trade_frame then
            if trade then trade:reset(); sync_trade() end
            reload_empty_frames, reload_boot_seen = 0, false
            session.hello_sent = false
        end
        trade_frame = io.framecount()
        local held_apply = pending_apply_trade
        if held_apply and p.net.connected() and not awaiting_trade_run then
            local _, current_prepared = trade:state()
            if held_apply.run_id ~= trade_run_id or held_apply.ot_id ~= trade_run_ot then
                pending_apply_trade = nil
                log("pending apply_trade discarded: server run/trainer binding changed")
            elseif current_prepared ~= held_apply.prepared or current_prepared.epoch ~= held_apply.epoch then
                pending_apply_trade = nil
                log("pending apply_trade discarded: preparation binding changed")
            elseif (held_apply.deadline and io.framecount() > held_apply.deadline)
                   or trade:capable() then
                pending_apply_trade = nil
                trade:apply(held_apply.cmd) -- checks the original prepare deadline and epoch
                sync_trade()
            elseif not journal.busy then
                pending_apply_trade = nil
                C.apply_trade(held_apply.cmd) -- named non-busy refusal, with no scene write
            end
        end
        local pending = pending_trade_finals[1]
        if pending and p.net.connected() and not awaiting_trade_run then
            if pending.run_id ~= trade_run_id or pending.ot_id ~= trade_run_ot then
                table.remove(pending_trade_finals,1) -- a different run cannot inherit this final
                log("pending trade_final discarded: server run/trainer binding changed")
            elseif journal and (journal:ready() or journal.busy) then
                local _, why
                if trade then _, why = trade:server_final(pending)
                else _, why = journal:final(pending.token,pending.epoch,pending.verdict) end
                if why ~= "trade journal lock busy" then table.remove(pending_trade_finals,1) end
            end
        end
        if owed then owed:step(p.net.connected(), false, send) end
        if journal then
            local empty = reads.read_sb1 and not reads.read_sb1() and reads.read_sb2 and not reads.read_sb2()
                and num(a.PARTY_COUNT_ADDR) and io.read_u8(a.PARTY_COUNT_ADDR) == 0
            if empty then reload_empty_frames = reload_empty_frames + 1
            elseif reload_empty_frames >= 2 then
                reload_boot_seen, reload_empty_frames = true, 0
                reload_witness_live = false
            else reload_empty_frames = 0 end
            if reload_boot_seen and trade and journal:ready() and journal:hidden()
               and not st.frozen and not in_battle() and io.trade_reload_proof
               and io.framecount() - reload_check_frame >= 30 and overworld_ok() then
                reload_check_frame = io.framecount()
                local ok, proof, why = pcall(io.trade_reload_proof, p.rom_type, profile, reload_boot_seen)
                if ok and proof and trade:qualify_reload(proof) then session.hello_sent = false
                elseif not ok or why then log("trade reload held: " .. tostring(ok and why or proof)) end
            end
        end
        -- the hidden-HELLO recovery runs with or without a native part: a clean cartridge's
        -- refused apply_trade owes reports behind it too (OMP cx-2e644e72 F1)
        if st.trade_hello_pending and session.hello_sent and not session.hello_visible
           and (not st.trade_hello_check or io.framecount()-st.trade_hello_check >= 30)
           and not in_battle() and overworld_ok() then
            st.trade_hello_check = io.framecount()
            update_frozen(party_read() or {})
            if not st.frozen and not (trade and trade:hide_party()) and not recovery_hidden() then
                session.hello_sent = false -- one refresh when visibility returns, no hidden-HELLO spin
            end
        end
        if not (native and native.service) then return end
        -- C5-11c MAJOR 3: this used to clear st.battle on a live boundary, which made
        -- finish_battle skip the encounter result (RR companion then sent no no_catch where RR
        -- clean did). The rival authority above replaces it and never touches the lifecycle.
        native:service()
        sync_trade() -- each native job's own publication receipt, never inferred from sink bytes
    end

    local Id = core.Identity.new({ key = key })
    local Q = core.Deferred.new({ exec = exec, memorial_box = memorial_box })
    Q.outbox=io.report_outbox
    local transport = setmetatable({
            send=function(line)
                -- safe has no snapshot-building seam in the shared core. Mark only
                -- this Gen 3 transport's withheld safe; Gen 1/2 bytes stay unchanged.
                local ok, fields = pcall(json.decode,line)
                if ok and type(fields) == "table" and fields.event == "safe"
                   and (st.frozen or (trade and trade:hide_party()) or recovery_hidden()) then
                    fields.party_hidden = true
                    line = json.encode(fields)
                end
                local result=p.net.send(line)
                if owed then owed:line_sent() end
                return result
            end,
            receive=function()
                local line=p.net.receive()
                if line ~= nil and owed then
                    owed:line_received()
                    local ok, reply=pcall(json.decode,line)
                    if ok and type(reply)=="table" and type(reply.commands)=="table" then owed:answer(reply.commands) end
                end
                return line
            end}, {__index=p.net})
    session = core.Session.new({ net = transport, json = json, hud = hud, log = sink, tag = TAG,
                                 player = p.player, game = drv, identity = Id, deferred = Q,
                                 outbox=io.report_outbox })
    session.driver, session.state = drv, st
    if native and (p.artifact_kind == "companion" or p.artifact_kind == "rand_companion") and native.request_match_call then
        local base_command = session.handle_command
        function session:handle_command(cmd)
            -- Same optional tag seam as gen2/client.lua: the original command
            -- still runs, and unsupported cartridges silently ignore the tag.
            if cmd.cmd ~= "noop" and cmd.phone ~= nil then
                local ok, why = pcall(native.request_match_call, native, cmd.phone, cmd.phone_data)
                if not ok then log("match_call request failed: " .. tostring(why)) end
            end
            return base_command(self, cmd)
        end
    end
    local base_send = session.send
    session.send = function(a, b, c)
        local event = (a == session) and b or a
        if event == "trade_request" and st.trade_apply then
            log("trade_request dropped: a trade is in flight")
            return false
        end
        return base_send(a, b, c)
    end
    return session
end

return Client
