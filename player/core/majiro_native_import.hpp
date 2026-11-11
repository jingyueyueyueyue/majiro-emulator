#pragma once
#include "majiro_story_save.hpp"
#include <functional>
#include <map>
#include <set>

namespace majiro {
struct NativeImportScriptBinding {
    std::string raw_name, sha256;
    std::set<uint32_t> verified_idle_root_offsets;
};
struct NativeImportManifest {
    std::string game_identity, original_exe_sha256, save_sha256;
    bool verified_same_script_version = false;
    std::vector<NativeImportScriptBinding> scripts;
};
struct NativeImportPlan {
    NativeStorySave story;
    std::string game_identity, original_exe_sha256, save_sha256;
    NativeImportScriptBinding script;
    uint32_t offset = 0;
    bool identity_verified = false;
    // A verified plan is a supported root state, not universal format support.
    std::string supported_scope;
};
using NativeImportScriptLoader = std::function<std::vector<uint8_t>(const std::string& raw_name)>;
std::string native_import_sha256(const std::vector<uint8_t>& data);
NativeImportPlan plan_native_story_import(const std::vector<uint8_t>& save,
    const NativeImportManifest& manifest, const NativeImportScriptLoader& loader);
}
