-- Explicit original Windows menu events for hosts without a menu bar.
local M={}
function M.new(state,dispatch)
  local names={[1]='LOADMENU',[3]='SAVEMENU',[99]='CONFIGMENU'}
  local pending
  local function modal(vm)
    return vm.modal_dialog or vm.external_checker or
      (state.modal_stack and #state.modal_stack>0)
  end
  local function request(vm,command)
    local name=names[command]
    if not name then return 'unsupported'end
    if vm.status~='running' or state.shutdown_requested or modal(vm) or
       state.settings.mouse==false or(command~=99 and state.settings.save_menu==false)then
      return 'disabled'
    end
    local hooks=state.events[name]
    if not hooks or #hooks==0 then return 'unsupported'end
    local live=false
    for _,hook in ipairs(hooks)do
      local t=vm.threads[hook.owner or 1]
      if t and not t.finished and not t.terminate_requested then live=true end
    end
    if not live then return 'unsupported'end
    -- The original holds one latest command, not an unbounded action queue.
    pending={command=command,generation=vm.context_generation}
    return 'queued'
  end
  local function poll(vm)
    if not pending then return end
    if pending.generation~=vm.context_generation or state.shutdown_requested then
      pending=nil;return
    end
    if modal(vm) or(state.invalidate_depth or 0)~=0 then return end
    vm:save_thread()
    local name=names[pending.command]
    for _,hook in ipairs(state.events[name]or{})do
      local t=vm.threads[hook.owner or 1]
      -- A host budget slice is not a source input boundary.
      if t and not t.finished and not t.terminate_requested and not t.waiting then return end
    end
    pending=nil
    dispatch(vm,name,{},nil,false)
  end
  return{request=request,poll=poll}
end
return M
