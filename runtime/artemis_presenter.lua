-- One bounded live frame, derived from current Majiro pages and sprites.
local M={}
function M.install(e,native,root,translate,measure)
  translate=translate or function(value)return value end
  local state={sequence=0,frames=0,last_time=-100000,signature=nil,live={}}
  state.session=assert(native('session'):match('^OK (.+)$'),'native frame session missing')
  state.unchanged=0;state.suppressed=0
  local timer=measure and function()return e:now()end or function()return 0 end
  local asynchronous=type(e.bindSurfaceAsync)=='function'and type(e.isLoadingSurface)=='function'
  local function text(c)
    local signature=(c.visible and not c.raster_only and '1' or '0')..c.text..tostring(c.configuration)..tostring(c.font_spec)..tostring(c.color)
    if signature==state.text_signature then return end
    state.text_signature=signature
    for i=1,(state.message_count or 0)do
      e:tag{'lyprop',id='~ruri_message_'..i,visible=0}
    end
    state.message_count=0
    e:tag{'lyprop',id='~ruri_message',visible=0}
    if not c.visible or c.raster_only or c.text=='' then
      return
    end
    local area=c.configuration or {0,322,590,792,114}
    local basefont=c.font_spec or {'',24,12,38}
    local entries=c.page
    if not entries or #entries==0 then entries={{text=c.text,x=area[2],y=area[3]}}end
    for i,entry in ipairs(entries)do
      local font=entry.font_spec or basefont
      local face=font[1]or''
      if face==''or face=='ＭＳ ゴシック'or face=='MS Gothic'or face=='Source Han Sans SC'then
        face='font/SourceHanSansSC-Regular.otf'
      end
      local color=entry.color or c.color or 0xffffff
      local r,g,b=color%256,math.floor(color/256)%256,math.floor(color/65536)%256
      local id='ruri_message_'..i
      e:tag{'chgmsg',id=id,layered=1};e:tag{'rp'}
      e:tag{'font',stack=0,face=face,
        -- This font's Artemis em-to-cell ratio was measured in the native
        -- host. Source line baselines remain explicit; rt adds extra leading.
        size=math.floor((font[2]or 24)*1.448+0.5),left=0,top=0,rubysize=0,
        spacetop=0,spacemiddle=0,spacebottom=0,kerning=-2,
        width=math.max(1,area[2]+area[4]-(entry.x or area[2])),height=area[5]+20,
        color=string.format('%02X%02X%02X',r,g,b),
        overflow=1,shadow=0,outline=1,outlinecolor='000000',alpha=255,entirealpha=255}
      e:tag{'print',data=translate(entry.text)};e:tag{'/chgmsg'}
      e:tag{'lyprop',id='~'..id,left=entry.x or area[2],top=entry.y or area[3],
        anchorx=0,anchory=0,visible=1,alpha=255}
      state.message_count=i
    end
  end
  local function snapshot(c)
    local page={}
    for i,entry in ipairs(c.page or {})do
      page[i]={text=entry.text,x=entry.x,y=entry.y,font_spec=entry.font_spec,color=entry.color}
    end
    return {visible=c.visible,text=c.text,configuration=c.configuration,font_spec=c.font_spec,color=c.color,
      page=page,raster_only=c.raster_only}
  end
  local function acknowledged(system,generation)
    if system.presented then system.presented(generation)end
  end
  local function commit(system,force)
    local pending=state.pending
    if not pending then return false end
    if asynchronous and not force and e:isLoadingSurface(pending.path)then return false end
    local began=timer()
    e:tag{'lyc',id='300',file=pending.path}
    e:tag{'lyprop',id='300',left=0,top=0,visible=1,anchorx=0,anchory=0}
    text(pending.console)
    if pending.cached then e:unbindSurface(pending.path)end
    state.last_bind_ms=state.last_bind_ms+timer()-began
    state.live[#state.live+1]=pending.path
    if #state.live>1 then os.remove(table.remove(state.live,1))end
    acknowledged(system,pending.generation)
    state.pending=nil;state.frames=state.frames+1
    return true
  end
  local function draw(system,now,force)
    local s=system.state
    state.last_native_ms=0;state.last_bind_ms=0
    -- Hold both queued textures and new rendering while the source builds
    -- an atomic page. An older async frame must not escape the same fence.
    if s.invalidate==false then state.suppressed=state.suppressed+1;return false end
    local bound=commit(system,force)
    if state.pending then return bound end
    if not s.frontbuffer then text(s.console);return bound end
    if not force and now-state.last_time<12 then return bound end
    system.sprites.update()
    local rows={}
    for _,id in ipairs(s.sprite_order)do
      local sprite=s.sprites[id]
      if sprite and sprite.destination==s.frontbuffer and sprite.anti<255 and
         (sprite.hidden or 0)==0 then
        rows[#rows+1]=string.format('%d %d %d %d %d %.8g %.8g %.8g %.8g %.8g %d %d',
          sprite.page,sprite.sx,sprite.sy,sprite.width,sprite.height,sprite.x,sprite.y,
          sprite.xscale,sprite.yscale,sprite.rotate,sprite.anti,sprite.extended and 1 or 0)
      end
    end
    local signature=table.concat(rows,'\n')
    if not force and not s.dirty and signature==state.signature then
      text(s.console);acknowledged(system,s.present_requested);return bound
    end
    local next_sequence=state.sequence+1
    local path=root..'/ruri_live_'..state.session..'_'..next_sequence..'.png'
    local began=timer()
    local request=system.screen and system.screen.present(s.frontbuffer,path,signature)
      or('present '..s.frontbuffer..' '..path..'\n'..signature)
    local reply=native(request)
    local composed=timer();state.last_native_ms=composed-began
    state.signature=signature;state.last_time=now;s.dirty=false
    if reply=='OK unchanged' then
      state.unchanged=state.unchanged+1;text(s.console)
      acknowledged(system,s.present_requested);return bound
    end
    state.sequence=next_sequence
    -- Replace the texture in the existing layer slot; never expose an empty
    -- layer between deletion and a disk load.
    state.pending={path=path,console=snapshot(s.console),generation=s.present_requested}
    if asynchronous and not force then
      e:bindSurfaceAsync(path);state.pending.cached=true
      return bound
    end
    return commit(system,true)or bound
  end
  return {draw=draw,state=state}
end
return M
