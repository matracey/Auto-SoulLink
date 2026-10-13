-- Gen 1 pc_ops_new's native deposit/withdraw/deposit/RELEASE sequence.
-- B stays connected to prove the boxed partner's actual memorial as well.
-- core/session.lua drops force_faint for a key absent from the party; a boxed
-- record has no HP field. The keyed memorial move is the cartridge consequence.
return function(ctx)
    if not ctx.wait_go() then return false, "no go-file" end
    local key = ctx.linked()
    if not key or not ctx.find(key) or ctx.find(key).slot ~= 1 then return false, "LINKED must name party slot 1" end
    if ctx.player == "a" then
        local source
        local signals, drain = ctx.session.signals, ctx.session.signals.drain
        signals.drain = function(self)
            local out = drain(self)
            for _, signal in ipairs(out) do
                if signal.kind == "pc_release" and signal.release_key == key then
                    source = signal.release_source
                    ctx.jlog("RELEASE_PREIMAGE", {key=key, source=source})
                end
            end
            return out
        end
        -- Transit must preserve the other healthy party member for native STORE.
        local walked, walk_why = ctx.flee_incidentals("release", function()
            ctx.walk_to_pc("release", {heal_before_deposit = true})
        end)
        if not walked then return false, "release transit: " .. tostring(walk_why) end
        if ctx.pc_deposit("first deposit") ~= key or not ctx.observe_boxed(key) then return false, "first deposit readback" end
        if not ctx.wait_sent("party_to_box", key) then return false, "first deposit TX missing" end
        ctx.log("DEPOSITED " .. key)
        if not ctx.wait_go("ALLOW_WITHDRAW") then return false, "no withdraw release" end
        if ctx.pc_withdraw("withdraw") ~= key or not ctx.observe_returned(key) then return false, "withdraw readback" end
        if not ctx.wait_sent("box_to_party", key) then return false, "withdraw TX missing" end
        ctx.log("WITHDRAWN " .. key)
        if not ctx.wait_go("ALLOW_DEPOSIT") then return false, "no second deposit release" end
        ctx.G.tap("Up", 2, 13)
        if ctx.pc_deposit("second deposit") ~= key or not ctx.observe_boxed(key) then return false, "second deposit readback" end
        if not ctx.wait_until(function() return ctx.sent("party_to_box", key) == 2 end, 120, "second deposit TX") then return false, "second deposit TX missing" end
        ctx.log("SECOND_DEPOSITED " .. key)
        if not ctx.wait_go("ALLOW_RELEASE") then return false, "no PC release permission" end
        local ok, why = ctx.pc_release("release", key)
        if not ok then return false, why end
        if not source or source.where ~= "box" then return false, "native release lacks a boxed pre-removal snapshot" end
        if not ctx.wait_sent("release", key) then return false, "native release TX missing" end
        ctx.log("RELEASED " .. key)
    else
        if not ctx.wait_received("box_mon", key, 1800) or not ctx.wait_sent("stats_cache", key) or not ctx.observe_boxed(key) then return false, "first mirrored deposit" end
        ctx.log("MIRROR_DEPOSITED " .. key)
        if not ctx.wait_received("party_mon", key, 1800) or not ctx.wait_sent("sync_retrieve_done", key) or not ctx.observe_returned(key) then return false, "mirrored withdraw" end
        ctx.log("MIRROR_WITHDRAWN " .. key)
        if not ctx.wait_until(function() return ctx.received("box_mon", key) == 2 and ctx.sent("stats_cache", key) >= 2 end, 1800, "second mirrored deposit") or not ctx.observe_boxed(key) then return false, "second mirrored deposit" end
        ctx.log("MIRROR_SECOND_DEPOSITED " .. key)
        if not ctx.wait_sent("memorialize_done", key, 1800) then return false, "release did not memorialize partner" end
        if ctx.received("force_faint", key) == 0 then return false, "release has no partner force_faint command" end
        if not ctx.observe_boxed(key) then return false, "partner memorial readback missing" end
    end
    return ctx.save("release")
end
