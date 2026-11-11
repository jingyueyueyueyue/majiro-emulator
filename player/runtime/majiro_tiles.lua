-- Shared named/global state adapter and unnamed tile algorithm providers.
local M={}
function M.install(define,state,options)
  local context=options.modules('runtime/majiro_tiles_actions.lua').install(define,state,options)
  options.modules('runtime/majiro_tiles_analysis.lua').install(define,state,options)
  return context
end
return M
