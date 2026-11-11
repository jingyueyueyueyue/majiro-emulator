#pragma once
#include "majiro_core.hpp"
#include <array>
#include <cstdint>
#include <memory>
#include <string>
#include <vector>

namespace majiro {
// Native pointers are never retained. Offset IDs preserve serialized sharing.
struct NativeSaveObject {
    uint32_t offset = 0, type = 0;
    std::string bytes;
    std::array<uint32_t, 3> dimensions{};
    std::vector<uint32_t> values;
    // Null entries retain the native -1 string reference, meaning empty string.
    std::vector<std::shared_ptr<NativeSaveObject>> strings;
};
struct NativeSaveVariable {
    uint32_t hash = 0, type = 0, bits = 0;
    std::shared_ptr<NativeSaveObject> object;
};
struct NativeSystemSave {
    uint32_t object_pool_bytes = 0;
    std::vector<NativeSaveVariable> variables;
    std::vector<uint32_t> picture_basename_hashes;
};
struct NativeReadmark {
    std::string script_bytes;
    uint32_t line_count = 0;
    uint64_t source_offset = 0;
    std::vector<uint8_t> bits;
    bool marked(uint32_t line) const;
};
struct NativeReadmarks {
    std::vector<NativeReadmark> records;
};
enum class NativeSaveKind { unknown, system, story_103, story_105 };
NativeSaveKind identify_native_save(const std::vector<uint8_t>& data);
NativeSystemSave read_native_system_save(const std::vector<uint8_t>& data);
NativeSystemSave read_native_system_save(const ReadAt& read, uint64_t bytes);
// The signatureless readmark format requires an explicit choice by the caller.
// Script names remain CP932/raw bytes; decode only at the platform boundary.
NativeReadmarks read_native_readmarks(const std::vector<uint8_t>& data);
NativeReadmarks read_native_readmarks(const ReadAt& read, uint64_t bytes);
std::string native_system_save_json(const NativeSystemSave& save);
std::string native_readmarks_json(const NativeReadmarks& marks);
}
