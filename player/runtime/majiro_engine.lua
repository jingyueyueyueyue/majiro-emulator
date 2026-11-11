-- Format and engine-generation selection. EXE fingerprints never gate startup.
local M={}
function M.detect(requirements)
  assert(type(requirements)=='table'and requirements.family=='Majiro','not a Majiro engine')
  local archive=tonumber(requirements.archive_version)
  assert(archive and archive>=1 and archive<=3,'unsupported Majiro archive version')
  assert(requirements.script_signature=='MajiroObjX1.000'or
    requirements.script_signature=='MajiroObjV1.000'or
    requirements.script_signature=='MjPlainBytecode','unsupported Majiro script format')
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
  local fixed=not modern and(features['api:2bd1709d']or features['api:7e0b8320'])
  local coordinate=modern and'double'or fixed and'fixed1000'or'identity'
  local profile={
    generation=save,recognition='Majiro ARC/MJO with optional engine version/API hints',
    numeric=save>=105 and'sse32'or'x87',
    minmax_arguments=wide and 0 or 2,string_case=save>=105 and'latin_fullwidth'or'cp932',
    integer_parse=save>=105 and'saturate32'or'wrap32',empty_search_absent=save<105,
    case_string_relations=wide,ultra_disable_returns_previous=wide or fixed,
    text_controls={legacy_font_defaults=not modern,overlay_saves_style=wide,
      overlay_suppresses_message=wide,message_first_line_only=not modern,uppercase_pause_event=modern},
    voice_controls={marker=(wide or fixed)and'exact'or'none',force_overrides_skip=wide},
    screen_controls={coordinate_mode=coordinate,home_direction=modern and 1 or -1,
      home_modulo=coordinate=='identity',home_affects_input=coordinate~='identity'},
    graphics_controls={optional_mulcopy_coordinates=wide},
    dialog_controls={reset_playback_modes=wide,reset_error_playback_modes=wide},
    platform_controls={containment=modern and'modern_margin_inclusive'or
      fixed and'fixed_offset_inclusive'or'legacy_exclusive',
      mouse_move=modern and'modern'or fixed and(archive==2 and'fixed_plus'or'fixed_minus')or'legacy_identity',
      keyboard_enable_clears_caps=fixed and archive==2 or false},
    external_controls={license=save>=105 and'native_constant_zero'or'cryptoapi_user_license'},
  }
  return profile
end
return M
