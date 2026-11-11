-- Compatibility entry for the two formerly handwritten tile providers.
-- Use the same original operation graph as the normal tiles installation.
local M={}
function M.install(define,state,options)
  local wanted={[0x6ddeb30e]=true,[0x850eecf4]=true}
  return options.modules('runtime/majiro_tiles_analysis.lua').install(
    function(hash,provider)if wanted[hash]then define(hash,provider)end end,state,options)
end
return M
