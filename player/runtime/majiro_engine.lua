-- Format and engine-generation selection. EXE fingerprints never gate startup.
local M={}
local function same_behavior(a,b)
  if type(a)~=type(b)then return false end
  if type(a)~='table'then return a==b end
  for key,value in pairs(a)do
    if key~='generation'and key~='recognition'and not same_behavior(value,b[key])then return false end
  end
  for key,value in pairs(b)do
    if key~='generation'and key~='recognition'and not same_behavior(value,a[key])then return false end
  end
  return true
end
function M.detect(requirements)
  assert(type(requirements)=='table'and requirements.family=='Majiro','not a Majiro engine')
  local archive=tonumber(requirements.archive_version)
  assert(archive and archive>=1 and archive<=3,'unsupported Majiro archive version')
  assert(requirements.script_signature=='MajiroObjX1.000'or
    requirements.script_signature=='MajiroObjV1.000'or
    requirements.script_signature=='MjPlainBytecode','unsupported Majiro script format')
  -- Keep optional executables separate. A launcher and its interpreter may
  -- provide different amounts of evidence for the same behavior; unrelated
  -- engines must never be combined into a synthetic profile.
  local groups,current,plain={},nil,{}
  local complete=false
  for _,feature in ipairs(requirements.features or{})do
    assert(not feature:match('^engine:ambiguous%-dispatch'),
      'ambiguous Majiro syscall dispatch '..(feature:match('^engine:ambiguous%-dispatch:(.+)$')or''))
    if feature=='engine-source:begin'then
      assert(not current,'nested engine feature source');current={}
    elseif feature=='engine-source:end'then
      assert(current,'engine feature source is not open');groups[#groups+1]=current;current=nil
    elseif current then
      current[#current+1]=feature
      if feature=='engine:api-dispatch'then complete=true end
    else plain[#plain+1]=feature end
  end
  assert(not current,'unterminated engine feature source')
  if #groups>0 then
    assert(#plain==0,'mixed grouped and ungrouped engine features')
    local selected
    for _,group in ipairs(groups)do
      local recognized=false
      for _,feature in ipairs(group)do if feature=='engine:api-dispatch'then recognized=true end end
      -- Unrecognized helper EXEs cannot override a recognized interpreter.
      if recognized or not complete then
        local profile=M.detect{family=requirements.family,archive_version=archive,
          script_signature=requirements.script_signature,features=group}
        assert(not selected or same_behavior(selected,profile),
          'conflicting Majiro engine executables: keep one engine implementation for this game')
        selected=selected or profile
      end
    end
    assert(selected,'engine feature sources contain no profile')
    return selected
  end
  local features={}
  local save
  for _,feature in ipairs(requirements.features or{})do
    features[feature]=true
    local version=tonumber(feature:match('^save:(%d+)$'))
    if version then save=math.max(save or 0,version)end
  end
  -- The on-disk save marker describes an engine generation, not a game.
  -- Without an EXE, the independently validated ARC/MJO still runs.
  save=save or(archive==3 and 105 or 103)
  local modern=save>=104
  local wide=modern or archive>=3
  local fixed=not modern and not not(features['api:f63ed9ae']or
    features['api:2bd1709d']or features['api:7e0b8320'])
  local coordinate=modern and'double'or fixed and'fixed1000'or'identity'
  local string_case=features['string:cp932']and'cp932'or
    features['string:latin_fullwidth']and'latin_fullwidth'or
    save>=105 and'latin_fullwidth'or'cp932'
  local marker=features['voice:marker_prefix']and'prefix'or
    features['voice:marker_exact']and'exact'or
    features['voice:marker_none']and'none'or
    (wide or fixed)and'exact'or'none'
  local profile={
    generation=save,recognition='Majiro ARC/MJO with optional engine version/API hints',
    numeric=save>=105 and'sse32'or'x87',
    minmax_arguments=wide and 0 or 2,string_case=string_case,
    array_3d_legacy_stride=features['array:3d_legacy_stride']==true,
    string_search_case_sensitive=features['string:search_case_sensitive']==true,
    integer_parse=save>=105 and'saturate32'or'wrap32',empty_search_absent=save<105,
    case_string_relations=wide,ultra_disable_returns_previous=wide or fixed,
    event_controls={release_registers_hook=features['event:release_registers_hook']==true},
    text_controls={legacy_font_defaults=not modern,overlay_saves_style=wide,
      overlay_suppresses_message=wide or features['api:15d07df8']==true,
      message_first_line_only=not modern,uppercase_pause_event=modern,
      x_control_event=not features['text:x_no_event'],
      history_suppresses_message=not features['text:history_retains_message']},
    voice_controls={marker=marker,force_overrides_skip=wide},
    audio_controls={extensions=features['audio:extensions:ogg-wav']and{'.ogg','.wav'}or{'.wav','.ogg'}},
    screen_controls={coordinate_mode=coordinate,home_direction=modern and 1 or -1,
      home_modulo=coordinate=='identity',home_affects_input=coordinate~='identity',
      integer_conversion=features['screen:integer_conversion:x87']and'x87'or'sse32',
      cursor_reset_sentinel=not features['screen:cursor_literal_coordinates']},
    graphics_controls={optional_mulcopy_coordinates=wide},
    dialog_controls={reset_playback_modes=wide,
      reset_error_playback_modes=wide or features['api:15d07df8']==true,
      warning_retrycancel_flags=features['dialog:warning_retrycancel:53']and 53 or 37},
    platform_controls={containment=modern and'modern_margin_inclusive'or
      fixed and'fixed_offset_inclusive'or'legacy_exclusive',
      mouse_move=modern and'modern'or fixed and(archive==2 and'fixed_plus'or'fixed_minus')or'legacy_identity',
      keyboard_enable_clears_caps=fixed and archive==2 or false},
    external_controls={license=(features['external:license:constant_zero']or save>=105)
      and'native_constant_zero'or'cryptoapi_user_license'},
  }
  if features['engine:api-dispatch']then
    profile.ultra_fast_mode_available=features['api:c102bcc3']==true
    if not features['api:a361d9f7']then profile.graphics_controls={mulcopy_available=false}end
    if not features['api:3670b283']then profile.platform_controls.containment='unavailable'end
  end
  return profile
end
return M
