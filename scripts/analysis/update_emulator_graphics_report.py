"""Publish the graphics audit inventory, final hashes and scoped evidence."""
from pathlib import Path
import hashlib
import json

ROOT=Path(__file__).resolve().parents[2]
EVIDENCE=ROOT/"archive/2026-11/20261111_emulator_audit_graphics"
TESTS={
    "test_screen_semantics":"screen_semantics_tests",
    "test_text_semantics":"text_semantics_tests",
    "test_text_remaining":"text_remaining_tests",
    "test_graphics_remaining":"graphics_remaining_tests",
    "test_player_images":"player_image_tests",
    "test_player_graphics_edges":"player_graphics_edge_tests",
    "test_player_graphics_contract":"player_graphics_contract_tests",
    "test_player_pixel_transactions":"player_pixel_transaction_tests",
    "test_player_sprite_interpolation":"player_sprite_interpolation_tests",
    "test_player_tile_code_integrity":"player_tile_code_integrity_tests",
    "test_tile_actions":"tile_action_tests",
    "test_graphics_batch":"graphics_batch_tests",
    "test_sprite_argument_forms":"sprite_argument_form_tests",
    "test_sprite_variable_geometry":"sprite_variable_geometry_tests",
    "test_sprite_priority_groups":"sprite_priority_groups_tests",
    "test_sprite_alpha_terminal":"sprite_alpha_terminal",
    "test_new_sprite_sequences":"new_sprite_sequence_tests",
    "test_new_graphics_abi":"new_graphics_abi_tests",
    "test_native_pixels":"native_pixel_tests",
    "test_original_blend":"original_blend_tests",
    "test_original_effects":"original_effect_tests",
    "test_original_swap":"original_swap_tests",
    "test_drawmodes":"drawmode_tests",
    "test_remaining_pixel_calls":"remaining_pixel_call_tests",
    "test_graphics_checkpoint":"graphics_checkpoint_tests",
    "test_text_raster":"text_raster_tests",
    "test_fontout_zero_height":"fontout_zero_height_tests",
    "test_player_history_clear":"player_history_clear_tests",
    "test_player_screen_coordinates":"player_screen_coordinate_tests",
    "test_tile_routines":"tile_routine_tests",
    "test_tiles_extra":"tiles_extra_tests",
    "test_original_png_aliases":"png_alias_tests",
    "test_png_mask_resources":"png_mask_resources",
    "test_page_image_loading":"page_image_loading_tests",
    "test_image_extension_resolution":"image_extension_resolution",
    "test_runtime_queries":"runtime_query_tests",
    "test_player_capture_sequences":"player_capture_sequence_tests",
    "test_capture_cache_batch":"capture_cache_tests",
    "test_text_ui_batch":"text_ui_batch_tests",
    "test_player_extcopy_overlap":"player_extcopy_overlap_tests",
    "test_player_page_lifetimes":"player_page_lifetime_tests",
    "benchmark_player_copy":"player_copy_benchmark",
}
AUDITS={
    "player/runtime/majiro_syscalls.lua":("authorized partial audit: explicit page lifetime guard/internal disposer and extcopy storage-profile command; other core logic owned by main",
        ["test_player_page_lifetimes","test_player_extcopy_overlap"]),
    "player/core/majiro_image.cpp":("all decoder/composition functions; RC8 raw mask and buffer validation",
        ["test_player_images","test_player_graphics_contract"]),
    "player/core/majiro_image.hpp":("complete public image data/decoder contract",
        ["test_player_images","test_player_graphics_contract"]),
    "player/core/image_resource.hpp":("all resolver/missing page/transparency predicates",
        ["test_original_png_aliases","test_png_mask_resources","test_page_image_loading","test_image_extension_resolution"]),
    "player/core/image_probe_main.cpp":("archive image/base/mask resolution and CP932-safe keys",
        ["test_player_images"]),
    "player/core/screen_view.hpp":("entire modern/fixed/legacy frame composition including signed limits",
        ["test_screen_semantics"]),
    "player/core/portable_pixels.hpp":("host image/glyph/present/export interface",
        ["test_player_graphics_contract","test_player_graphics_edges"]),
    "player/core/portable_pixels_wrapper.inc":("all host adapters, glyph mask painting, PNG export, shared transaction generation",
        ["test_player_graphics_contract","test_player_graphics_edges","test_player_pixel_transactions","test_screen_semantics"]),
    "player/native/majiro_pixels.cpp":("all page algorithms and operation dispatch; Windows I/O inspected; native/portable core differentials",
        ["test_native_pixels","test_original_blend","test_original_swap","test_original_effects","test_drawmodes",
         "test_graphics_remaining","test_graphics_batch","test_remaining_pixel_calls","test_text_raster",
         "test_player_graphics_edges","test_player_pixel_transactions"]),
    "player/runtime/majiro_screen.lua":("all coordinate/presentation/zoom providers; original x87/SSE overflow and home modes",
        ["test_screen_semantics","test_player_screen_coordinates","test_text_ui_batch"]),
    "player/runtime/majiro_sprites.lua":("all creation/lifetime/order/movement/transform/animation providers",
        ["test_sprite_argument_forms","test_player_sprite_interpolation","test_sprite_alpha_terminal",
         "test_sprite_priority_groups","test_sprite_variable_geometry","test_new_sprite_sequences","test_new_graphics_abi"]),
    "player/runtime/majiro_graphics_checkpoint.lua":("entire command dependency analysis; mask preservation and alphatable dependency",
        ["test_graphics_checkpoint","test_player_graphics_edges","test_original_swap","test_graphics_remaining"]),
    "player/runtime/majiro_text.lua":("entire opcode text/proc/control flow; message/history ownership and version differences",
        ["test_text_semantics","test_player_history_clear"]),
    "player/runtime/majiro_fontout.lua":("all font stack/default/configuration/raster requests",
        ["test_text_remaining","test_fontout_zero_height","test_text_ui_batch"]),
    "player/runtime/majiro_console.lua":("all live/history/voice rectangle/replay providers; ring byte accounting",
        ["test_text_semantics","test_player_history_clear","test_text_ui_batch"]),
    "player/runtime/majiro_capture.lua":("all capture registration, polling and input transitions; seventh cancel callback",
        ["test_player_capture_sequences","test_capture_cache_batch"]),
    "player/runtime/majiro_tiles.lua":("complete two-module installation and shared state",
        ["test_tile_actions","test_tile_routines"]),
    "player/runtime/majiro_tiles_actions.lua":("all state/RNG/reservation/opponent recipe adapters",
        ["test_tile_actions"]),
    "player/runtime/majiro_tile_machine.lua":("all interpreter dispatch paths; generated instruction inventory matched to original",
        ["test_player_tile_code_integrity","test_tile_actions"]),
    "player/runtime/majiro_tiles_analysis.lua":("all operation dispatch and VM-global bindings",
        ["test_player_tile_code_integrity","test_tile_routines","test_tiles_extra"]),
    "player/runtime/majiro_tile_program.lua":("all 8316 generated instructions and 2389 constants independently matched to original",
        ["test_player_tile_code_integrity","test_tile_routines"]),
    "player/runtime/majiro_tiles_extra.lua":("entire compatibility adapter now delegates to exact operation graph",
        ["test_tiles_extra"]),
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    destination=ROOT/"reports/emulator_audit_graphics.json"
    previous=json.loads(destination.read_text(encoding="utf8"))if destination.exists()else{}
    tests=[]
    for name,report_name in TESTS.items():
        path=ROOT/"reports"/(report_name+".json")
        data=json.loads(path.read_text(encoding="utf8"))
        count=data.get("comparisons",data.get("command_comparisons",data.get("case_count",data.get("checks"))))
        if isinstance(count,list):count=len(count)
        if count is None:
            cases=data.get("cases",data.get("tests",[]))
            count=len(cases)if isinstance(cases,list)else cases
        row={"test":name,"report":str(path.relative_to(ROOT)).replace("\\","/"),
             "status":data.get("status"),"count":count,
             "mismatches":data.get("mismatches",len(data.get("failures",[])))}
        if data.get("source_changed_during_test"):
            row["status"]="source_changed";row["behavior_mismatches"]=data.get("mismatches")
            row["changed_since_test_start"]=[
                name for name,value in data.get("source_identity",{}).get("files",{}).items()
                if (ROOT/name).exists()and digest(ROOT/name)!=value]
            row["owned_sources_changed"]=[name for name in row["changed_since_test_start"]if name in AUDITS]
        logs=list(EVIDENCE.glob("phase*/"+name+".log"))
        if logs:row["latest_log"]=str(max(logs,key=lambda p:p.stat().st_mtime_ns))
        tests.append(row)
    build=json.loads((ROOT/"reports/player_native_build.json").read_text(encoding="utf8"))
    production_path=ROOT/"reports/engine_production_profiles.json"
    production=json.loads(production_path.read_text(encoding="utf8"))if production_path.exists()else{}
    report={
        "date":"2026-11-11","updated":datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
        "status":"source_frozen_pending_central_android_build",
        "production_scope":"player","legacy_runtime_is_not_evidence":True,
        "source_frozen":True,"evidence_directory":str(EVIDENCE),
        "file_audits":[{"file":name,"sha256":digest(ROOT/name),"lines":len((ROOT/name).read_text(encoding="utf8").splitlines()),
                        "review":scope,"tests":names}for name,(scope,names)in AUDITS.items()],
        "fixes":[
            "mixed-depth copy palette expansion/red-channel extraction and native palette extcopy rounding",
            "default grayscale indexed page palettes",
            "raw RC8 mask indices independent of palette",
            "base/mask/host pixel buffer validation",
            "CP932-safe image probe mask lookup",
            "PNG chunk length validation",
            "64-bit clipping and glyph position arithmetic",
            "checkpoint compaction preserves target masks and alphatable input dependencies",
            "x87 screen integer conversion",
            "sprite scale negative terminal, transform/alpha cycles and mutually exclusive tweens",
            "old-engine X_CONTROL and disabled-history last-message behavior",
            "history clear byte accounting",
            "legacy tile compatibility adapter uses exact operation graph",
            "portable PNG export materializes attached masks and indexed palettes",
            "mouse capture single/double/drag/cancel callback timing",
            "shared real Windows/portable page transactions",
        ],
        "tests":tests,
        "original_executable_fixture_samples":17,
        "native_build":{"library":build["library"],"sha256":build["sha256"],
                        "source_receipt":"reports/player_native_build.json",
                        "current":all(digest(ROOT/name)==value for name,value in build["input_sha256"].items())},
        "central_validation_commands":[
            "python -B -X utf8 scripts/build/run_emulator_audit.py --build --package",
            "python -B -X utf8 scripts/analysis/run_emulator_graphics_audit.py "+" ".join(TESTS)],
        "central_suite_additions":["test_player_pixel_transactions","test_player_capture_sequences",
                                   "test_player_history_clear","test_player_screen_coordinates",
                                   "test_player_extcopy_overlap","test_player_page_lifetimes"],
        "cross_module_requests":[
            {"owner":"main","request":"production screen_controls.integer_conversion=x87 for recovered x87 converter",
             "evidence":"reports/player_screen_coordinate_tests.json",
             "integration_report_status":production.get("status","not_run")},
            {"owner":"main","request":"production text flags x_control_event/history_suppresses_message",
             "evidence":"reports/player_text_feature_evidence.json",
             "integration_report_status":production.get("status","not_run")},
            {"owner":"host","request":"use current independent DLL for native_slot_source transaction",
             "evidence":"reports/player_pixel_transaction_tests.json"},
        ],
        "android_rebuild_required":["player/native/majiro_pixels.cpp","player/core/portable_pixels_wrapper.inc",
                                    "player/runtime/majiro_capture.lua","player/runtime/majiro_sprites.lua",
                                    "player/runtime/majiro_syscalls.lua"],
        "limits":[
            "No tablet access or complete interactive game traversal in this audit.",
            "Windows font raster and portable host alpha contracts are separately tested; Android physical glyph output is host-group scope.",
            "Text controls/grid/history state comparisons do not certify every vertical-layout/kinsoku/style-raster variant.",
            "Tile instruction/constant integrity is complete; dynamic cases cover 136 selectors plus 3/4-seat cases, not every possible hand.",
            "Any source_changed result is not a final pass and requires source-stable central validation.",
        ],
        "translation_modified":False,"game_or_save_modified":False,"game_backups_created":False,
        "new_script_category":"scripts/analysis","new_top_level_directories":[],
    }
    if previous.get("phase3"):report["phase3"]=previous["phase3"]
    destination.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf8")
    start="<!-- GENERATED PHASE2 INVENTORY -->"
    end="<!-- END GENERATED PHASE2 INVENTORY -->"
    md=ROOT/"reports/emulator_audit_graphics.md"
    text=md.read_text(encoding="utf8")
    if start in text:
        left=text.index(start);right=text.index(end,left)+len(end)
        text=text[:left]+text[right:]
    lines=[start,"","## 逐文件记录与最新验证","","JSON 保存每个文件的 SHA256。以下通过仅适用于列出的回归范围，不代表全游戏实机认证。","",
           "| 文件 | 审查范围 |","|---|---|"]
    lines += [f"| `{name}` | {scope} |"for name,(scope,_)in AUDITS.items()]
    lines += ["","### 本组完整回归清单","","| 测试 | 状态 | 比较/用例数 |","|---|---|---|"]
    lines += [f"| `{row['test']}` | {row['status']} | {row['count']} |"for row in tests]
    lines += ["","### 交接与分类","",
              "所有功能脚本放在 `scripts/analysis/`，临时编译物、原版反汇编、失败与修复证据留在日期归档目录。未产生游戏备份，未修改译文、游戏资源、平板或用户存档。",
              "下一次集中构建需包含 PNG 导出、共享页面事务和鼠标捕获修复。主流程由主对话合并；建议补入 pixel_transactions、capture_sequences、history_clear、screen_coordinates 四个专项。",end]
    md.write_text(text.rstrip()+"\n\n"+"\n".join(lines)+"\n",encoding="utf8")
    print("graphics report:",len(AUDITS),"files,",len(tests),"suites;",
          [(row["test"],row["status"])for row in tests if row["status"]!="passed"])


if __name__=="__main__":main()
