-- scenario_gen3_boxsync.lua — boxsync_gen3: the linked pair's full PC round trip.
--
-- The runner links both slot-1 mons. A (battle fixture, Route 1 grass) walks to the Viridian
-- Pokemon Center PC over gen3_scripted_play.lua's BFS-verified paths and DEPOSITs party slot 1
-- into box 0 through the pinned PC stages (the viridian_pc leg's sequence); its client reports
-- party_to_box and the server mirrors box_mon to B, whose client deposits at its own overworld
-- checkpoint (B idles on the town fixture) and acknowledges with stats_cache. Released by the
-- runner (ALLOW_WITHDRAW), A WITHDRAWs box 0 slot 0; the client reports box_to_party, the server
-- mirrors party_mon, B's client withdraws and sends sync_retrieve_done. Both save on SAVE.
local function a_side(ctx, linked)
    ctx.walk_to_pc("boxsync a", {heal_before_deposit = true})
    local gone, why = ctx.pc_deposit("boxsync a deposit")
    if gone ~= linked then return false, "the deposit moved " .. tostring(gone or why) .. ", not " .. linked end
    if not ctx.observe_boxed(linked) then return false, linked .. " was never read back boxed after the deposit" end
    ctx.log("DEPOSITED " .. linked)
    if not ctx.wait_sent("party_to_box", linked, 120) then return false, "no party_to_box for " .. linked end
    if not ctx.wait_go("ALLOW_WITHDRAW", 1200) then return false, "the runner never allowed the withdraw" end
    local back, why2 = ctx.pc_withdraw("boxsync a withdraw")
    if back ~= linked then return false, "the withdraw returned " .. tostring(back or why2) .. ", not " .. linked end
    if not ctx.observe_returned(linked) then return false, linked .. " was never read back in the party after the withdraw" end
    ctx.log("WITHDRAWN " .. linked)
    if not ctx.wait_sent("box_to_party", linked, 120) then return false, "no box_to_party for " .. linked end
    return true
end

local function b_side(ctx, linked)
    if not ctx.wait_received("box_mon", linked, 1500) then return false, "no mirrored box_mon" end
    if not ctx.wait_sent("stats_cache", linked, 300) then
        return false, "the mirrored deposit was not acknowledged (stats_cache)"
    end
    -- the ACK is the client's word; the cartridge must show it (Codex C4-6b finding 2)
    if not ctx.observe_boxed(linked) then return false, "stats_cache sent but " .. linked .. " was never read back boxed" end
    ctx.log("MIRROR_DEPOSITED " .. linked)
    if not ctx.wait_received("party_mon", linked, 1500) then return false, "no mirrored party_mon" end
    if not ctx.wait_sent("sync_retrieve_done", linked, 300) then
        return false, "the mirrored withdraw was not acknowledged (sync_retrieve_done)"
    end
    if not ctx.observe_returned(linked) then return false, "sync_retrieve_done sent but " .. linked .. " was never read back in the party" end
    ctx.log("MIRROR_WITHDRAWN " .. linked)
    return true
end

return function(ctx)
    if not ctx.wait_go() then return false, "no go-file" end
    local linked = ctx.linked()
    local mon = linked and ctx.find(linked)
    if not mon or mon.slot ~= 1 then return false, "the LINKED key must be party slot 1" end
    local ok, why
    if ctx.player == "a" then ok, why = a_side(ctx, linked) else ok, why = b_side(ctx, linked) end
    if not ok then return false, why end
    if ctx.sent("box_mon_failed") > 0 or ctx.sent("sync_retrieve_failed") > 0 then
        return false, "a keyed box command was refused"
    end
    if not ctx.wait_go("SAVE", 1200) then return false, "the runner never released the SAVE" end
    ok, why = ctx.save("boxsync")
    if not ok then return false, why end
    return true, "round trip of " .. linked
end
