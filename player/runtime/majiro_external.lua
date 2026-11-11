-- Legacy local license records and Windows CryptoAPI boundary.
local M={}
function M.install(define,state,options)
  local current,next_request=nil,0
  local authenticated=false
  local function checker_host(vm,action,token)
    if not options.service then vm:fail('external disc checker host unavailable')end
    local result=options.service(action,token)
    if type(result)~='string'then vm:fail('invalid external checker response')end
    return result
  end
  define(0xf430015b,function(vm,args,wants)
    assert(#args==0,'external disc checker takes no arguments')
    if authenticated then return 0 end
    if current then vm:fail('external disc checker request already active')end
    next_request=next_request+1
    local token=string.format('%d:%d',vm.thread_id,next_request)
    -- The Android host must resolve and launch an installed disc-authenticator
    -- companion. It must report missing capability if no real checker exists.
    -- A resource parser self-test or a generated 0x9004 reply is not valid.
    local result=checker_host(vm,'checker_begin',token)
    if result~='pending'then
      state.external_checker_error=result
      vm:fail('external disc authentication unavailable: '..result)
    end
    current={token=token,thread=vm.thread_id,wants=wants,generation=vm.context_generation}
    vm.external_checker=token
    vm.waiting={kind='external_checker',token=token}
    return nil,true
  end)
  local function check_license(vm,args)
    assert(#args==1 and type(args[1])=='string','license-check signature')
    local profile=options.external_controls
    if not profile then vm:fail('license engine variant has not been identified')end
    if profile.license=='native_constant_zero'then return 0 end
    assert(profile.license=='cryptoapi_user_license','unknown license-check variant')
    -- The original opens this current-user key, queries a REG_BINARY value,
    -- then decrypts it with the user's AT_KEYEXCHANGE key. Never substitute
    -- plaintext success when the original per-user key is unavailable.
    -- Android has no HKCU CryptoAPI namespace. An absent imported namespace
    -- produces the original RegOpenKey failure, not a successful validation.
    local record=options.license and options.license('read',args[1])
      or{registry_open=false}
    assert(type(record)=='table','invalid license record response')
    if record.registry_open==false then return 1 end
    assert(record.registry_open==true,'license registry-open result unavailable')
    if record.query==false then return 2 end
    assert(record.query==true,'license query result unavailable')
    if record.crypto_acquire==false then return 3 end
    assert(record.crypto_acquire==true,'license cryptographic-context result unavailable')
    if record.user_key==false then return 4 end
    assert(record.user_key==true,'license user-key result unavailable')
    if record.decrypt==false then return 5 end
    assert(record.decrypt==true,'license decryption result unavailable')
    assert(type(record.plaintext)=='string','decrypted license bytes unavailable')
    assert(#record.plaintext>=10,'decrypted license record is shorter than native prefix')
    local expected='LICENSENO:';local mismatches=0
    for i=1,#expected do
      if record.plaintext:byte(i)~=expected:byte(i)then mismatches=mismatches+1 end
    end
    return mismatches==0 and 0 or mismatches+10
  end
  define(0x87a631a6,check_license)
  return{license=check_license,
    poll=function(vm)
      if not current then return false end
      local owner=vm.threads[current.thread]
      if not owner or owner.finished or current.generation~=vm.context_generation then
        local token=current.token;current=nil;vm.external_checker=nil
        pcall(checker_host,vm,'checker_cancel',token)
        state.external_checker_error='external checker owner context no longer exists'
        vm:fail(state.external_checker_error)
      end
      local result=checker_host(vm,'checker_poll',current.token)
      if result=='pending'then return true end
      if result~='authenticated 9004'then
        state.external_checker_error=result
        vm:fail('external disc authentication failed: '..result)
      end
      local original=vm.thread_id
      vm:select_thread(current.thread)
      if not vm.waiting or vm.waiting.kind~='external_checker'or vm.waiting.token~=current.token then
        current=nil;vm.external_checker=nil
        vm:select_thread(original)
        vm:fail('external checker owner continuation changed')
      end
      vm.waiting=nil
      if current.wants then vm:push(0)end
      vm:save_thread();vm:select_thread(original)
      authenticated=true;state.external_checker_authenticated=true
      state.external_checker_error=nil;vm.external_checker=nil;current=nil
      return false
    end}
end
return M
