-- Shared probe-origin mutation guard. Native game writes are not Lua API calls.
local G = {}
function G.readonly(raw_memory, raw_emu, denied)
    local function block(kind, api)
        return function()
            denied(kind, api)
            error("read-only probe denied " .. api, 0)
        end
    end
    local memory = setmetatable({}, {__index=function(_, name)
        if type(name) == "string" and name:match("^write") then
            return block("guest_write", "memory." .. name)
        end
        return raw_memory[name]
    end})
    local emu = setmetatable({}, {__index=function(_, name)
        if name == "setregister" then return block("cpu_change", "emu.setregister") end
        return raw_emu[name]
    end})
    return memory, emu
end
function G.observe_memory(raw_memory, record)
    return setmetatable({}, {__index=function(_, name)
        local fn = raw_memory[name]
        if type(name) == "string" and name:match("^write")
            and (type(fn) == "function" or type(fn) == "userdata") then
            return function(...)
                record(name, ...)
                return fn(...)
            end
        end
        return fn
    end})
end
return G
