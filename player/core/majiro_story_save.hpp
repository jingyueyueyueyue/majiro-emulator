#pragma once
#include "majiro_save.hpp"
#include <array>
#include <cstdint>
#include <string>
#include <vector>

namespace majiro {
struct NativeStoryFrame {
    std::array<uint8_t, 0xa8> serialized{};
    std::string script_bytes;
    std::array<uint32_t, 10> fields{};
};
struct NativeStoryValues {
    std::vector<uint8_t> serialized_slots;
    std::vector<NativeSaveVariable> values;
};
struct NativeStorySave {
    std::string signature, label_bytes;
    uint32_t count_fields_offset = 0, frame_depth = 0;
    uint32_t thumbnail_width = 0, thumbnail_height = 0, thumbnail_pixel_bytes = 0;
    uint32_t opaque_header_offset = 0, opaque_header_bytes = 0;
    uint32_t picture_hash_offset = 0;
    std::vector<uint8_t> serialized_header, thumbnail, serialized_object_pool;
    std::vector<NativeStoryFrame> frames;
    std::array<NativeStoryValues, 3> groups;
};
// These are requests for a caller to establish exact script identity and code
// boundaries. They are not verified mappings and cannot authorize VM restore.
struct NativeStoryScriptRequirement {
    std::string script_bytes;
    uint32_t line_id = 0;
    std::vector<uint32_t> code_offsets;
};
NativeStorySave read_native_story_save(const std::vector<uint8_t>& data);
NativeStorySave read_native_story_save(const ReadAt& read, uint64_t bytes);
std::vector<NativeStoryScriptRequirement> native_story_script_requirements(const NativeStorySave& save);
std::string native_story_save_json(const NativeStorySave& save);
// Deliberately no VM import/partial restore API. Scene/media semantics and
// script-version identities must be validated before that separate API exists.
}
