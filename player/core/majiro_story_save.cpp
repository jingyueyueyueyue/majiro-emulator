#include "majiro_story_save.hpp"
#include <algorithm>
#include <cstring>
#include <locale>
#include <ostream>
#include <stdexcept>
#include <streambuf>
#include <string_view>

namespace majiro {
namespace {
constexpr uint64_t kMaxBytes = 64 * 1024 * 1024, kMaxValues = 1000000;
constexpr size_t kSystemHeader = 0x9c58;
void require(bool good, const char* message) {
    if (!good) throw std::runtime_error(message);
}
uint32_t u32(const std::vector<uint8_t>& data, size_t offset) {
    require(offset <= data.size() && data.size() - offset >= 4, "truncated story save field");
    return uint32_t(data[offset]) | (uint32_t(data[offset + 1]) << 8) |
           (uint32_t(data[offset + 2]) << 16) | (uint32_t(data[offset + 3]) << 24);
}
void put32(std::vector<uint8_t>& data, size_t offset, uint32_t value) {
    for (size_t i = 0; i < 4; ++i) data[offset + i] = static_cast<uint8_t>(value >> (8 * i));
}
std::string hexbytes(const uint8_t* data, size_t size) {
    static constexpr char digits[] = "0123456789abcdef";
    std::string result;
    result.reserve(size * 2);
    for (size_t i = 0; i < size; ++i) {
        result.push_back(digits[data[i] >> 4]);
        result.push_back(digits[data[i] & 15]);
    }
    return result;
}
std::string hexbytes(const std::vector<uint8_t>& data) { return hexbytes(data.data(), data.size()); }
class JsonBuffer final : public std::streambuf {
public:
    std::string data;
protected:
    std::streamsize xsputn(const char* value, std::streamsize bytes) override {
        require(bytes >= 0 && static_cast<uint64_t>(bytes) <= kMaxBytes * 2 - data.size(),
                "native story JSON exceeds size bound");
        data.append(value, static_cast<size_t>(bytes));
        return bytes;
    }
    int_type overflow(int_type value) override {
        if (traits_type::eq_int_type(value, traits_type::eof())) return traits_type::not_eof(value);
        require(data.size() < kMaxBytes * 2, "native story JSON exceeds size bound");
        data.push_back(traits_type::to_char_type(value));
        return value;
    }
};
std::vector<uint8_t> portion(const std::vector<uint8_t>& data, size_t at, size_t bytes) {
    require(at <= data.size() && bytes <= data.size() - at, "story section outside input");
    return {data.begin() + at, data.begin() + at + bytes};
}
}

NativeStorySave read_native_story_save(const std::vector<uint8_t>& data) {
    require(data.size() <= kMaxBytes, "native story save exceeds size bound");
    NativeStorySave result;
    auto kind = identify_native_save(data);
    size_t header_size = 0;
    if (kind == NativeSaveKind::story_103) {
        result.signature = "MajiroSavV1.103";
        header_size = 0xa3e0;
        result.count_fields_offset = 0x90;
        result.thumbnail_pixel_bytes = 3;
    } else if (kind == NativeSaveKind::story_105) {
        result.signature = "MajiroSavV1.105";
        header_size = 0xa560;
        result.count_fields_offset = 0x210;
        result.thumbnail_pixel_bytes = 4;
    } else throw std::runtime_error("unsupported native story save signature");
    require(data.size() >= header_size, "truncated native story header");
    size_t c = result.count_fields_offset;
    result.frame_depth = u32(data, c);
    result.thumbnail_width = u32(data, c + 4);
    result.thumbnail_height = u32(data, c + 8);
    require(result.frame_depth <= 1000, "story frame count exceeds bound");
    std::array<uint32_t, 3> counts{u32(data, c + 12), u32(data, c + 16), u32(data, c + 20)};
    uint64_t total_values = uint64_t(counts[0]) + counts[1] + counts[2];
    require(total_values <= kMaxValues, "story combined value count exceeds bound");
    uint32_t pool_bytes = u32(data, c + 24);
    uint64_t pixels = uint64_t(result.thumbnail_width) * result.thumbnail_height;
    require(pixels <= kMaxBytes / result.thumbnail_pixel_bytes, "story thumbnail exceeds size bound");
    uint64_t thumbnail_bytes = pixels * result.thumbnail_pixel_bytes;
    uint64_t frame_bytes = (uint64_t(result.frame_depth) + 1) * 0xa8;
    uint64_t values_bytes = total_values * 16;
    require(header_size + thumbnail_bytes + frame_bytes + values_bytes + pool_bytes == data.size(),
            "native story size mismatch or trailing data");
    result.serialized_header = portion(data, 0, header_size);
    const auto label_begin = data.begin() + 16, label_end = data.begin() + c;
    auto label_zero = std::find(label_begin, label_end, 0);
    require(label_zero != label_end, "unterminated native story label");
    result.label_bytes.assign(reinterpret_cast<const char*>(data.data() + 16),
                              static_cast<size_t>(label_zero - label_begin));
    result.picture_hash_offset = static_cast<uint32_t>(header_size - 40000);
    result.opaque_header_offset = static_cast<uint32_t>(c + 28);
    result.opaque_header_bytes = result.picture_hash_offset - result.opaque_header_offset;
    size_t cursor = header_size;
    result.thumbnail = portion(data, cursor, static_cast<size_t>(thumbnail_bytes));
    cursor += static_cast<size_t>(thumbnail_bytes);
    result.frames.reserve(size_t(result.frame_depth) + 1);
    for (size_t index = 0; index <= result.frame_depth; ++index) {
        NativeStoryFrame frame;
        std::copy_n(data.begin() + cursor, 0xa8, frame.serialized.begin());
        auto zero = std::find(frame.serialized.begin(), frame.serialized.begin() + 128, 0);
        require(zero != frame.serialized.begin() && zero != frame.serialized.begin() + 128,
                "unterminated or empty native story frame script");
        frame.script_bytes.assign(reinterpret_cast<const char*>(frame.serialized.data()),
                                  static_cast<size_t>(zero - frame.serialized.begin()));
        for (size_t j = 0; j < 10; ++j) frame.fields[j] = u32(data, cursor + 128 + j * 4);
        result.frames.push_back(std::move(frame));
        cursor += 0xa8;
    }
    const size_t value_start = cursor, pool_start = cursor + static_cast<size_t>(values_bytes);
    result.serialized_object_pool = portion(data, pool_start, pool_bytes);
    // Reuse the exact validated common object decoder without altering its API.
    // Temporary unique hashes allow stack slots and cross-scope names to repeat;
    // restore each original slot hash after decoding. No guest pointer is used.
    const uint64_t shared_size = kSystemHeader + values_bytes + pool_bytes;
    require(shared_size <= kMaxBytes, "story shared value view exceeds size bound");
    std::vector<uint8_t> shared(static_cast<size_t>(shared_size), 0);
    const std::string_view system_signature("MajiroSysV1.100\0", 16);
    std::copy(system_signature.begin(), system_signature.end(), shared.begin());
    put32(shared, 16, static_cast<uint32_t>(total_values));
    put32(shared, 20, pool_bytes);
    std::copy(data.begin() + value_start, data.end(), shared.begin() + kSystemHeader);
    for (size_t i = 0; i < total_values; ++i) put32(shared, kSystemHeader + i * 16, static_cast<uint32_t>(i + 1));
    auto common = read_native_system_save(shared);
    size_t decoded = 0;
    for (size_t group = 0; group < 3; ++group) {
        result.groups[group].serialized_slots = portion(data, cursor, size_t(counts[group]) * 16);
        result.groups[group].values.reserve(counts[group]);
        for (size_t i = 0; i < counts[group]; ++i) {
            auto value = std::move(common.variables[decoded++]);
            value.hash = u32(data, cursor + i * 16);
            result.groups[group].values.push_back(std::move(value));
        }
        cursor += size_t(counts[group]) * 16;
    }
    return result;
}
NativeStorySave read_native_story_save(const ReadAt& read, uint64_t bytes) {
    require(bytes <= kMaxBytes, "native story save exceeds size bound");
    std::vector<uint8_t> data(static_cast<size_t>(bytes));
    if (bytes) read(0, data.data(), data.size());
    return read_native_story_save(data);
}
std::vector<NativeStoryScriptRequirement> native_story_script_requirements(const NativeStorySave& save) {
    std::vector<NativeStoryScriptRequirement> result;
    for (const auto& frame : save.frames) {
        NativeStoryScriptRequirement requirement;
        requirement.script_bytes = frame.script_bytes;
        requirement.line_id = frame.fields[4];
        requirement.code_offsets.push_back(frame.fields[0]);
        for (size_t i = 5; i < frame.fields.size(); ++i)
            if (frame.fields[i]) requirement.code_offsets.push_back(frame.fields[i]);
        result.push_back(std::move(requirement));
    }
    return result;
}
std::string native_story_save_json(const NativeStorySave& save) {
    JsonBuffer buffer;
    std::ostream out(&buffer);
    out.exceptions(std::ios::badbit | std::ios::failbit);
    out.imbue(std::locale::classic());
    out << "{\"schema\":1,\"format\":\"" << save.signature << "\","
           "\"native_story_restore_supported\":false,\"script_identity_verified\":false,"
           "\"resources_verified\":false,\"header_bytes_hex\":\"" << hexbytes(save.serialized_header)
        << "\",\"label_bytes_hex\":\""
        << hexbytes(reinterpret_cast<const uint8_t*>(save.label_bytes.data()), save.label_bytes.size())
        << "\",\"count_fields_offset\":" << save.count_fields_offset << ",\"frame_depth\":" << save.frame_depth
        << ",\"frame_stride\":168,\"opaque_header\":{\"offset\":" << save.opaque_header_offset
        << ",\"bytes\":" << save.opaque_header_bytes << ",\"semantics_verified\":false},"
           "\"picture_hash_offset\":" << save.picture_hash_offset
        << ",\"thumbnail\":{\"width\":" << save.thumbnail_width << ",\"height\":" << save.thumbnail_height
        << ",\"pixel_bytes\":" << save.thumbnail_pixel_bytes << ",\"bytes_hex\":\""
        << hexbytes(save.thumbnail) << "\"},\"frames\":[";
    for (size_t i = 0; i < save.frames.size(); ++i) {
        if (i) out << ',';
        const auto& frame = save.frames[i];
        out << "{\"script_bytes_hex\":\""
            << hexbytes(reinterpret_cast<const uint8_t*>(frame.script_bytes.data()), frame.script_bytes.size())
            << "\",\"serialized_hex\":\"" << hexbytes(frame.serialized.data(), frame.serialized.size())
            << "\",\"fields\":[";
        for (size_t j = 0; j < 10; ++j) {
            if (j) out << ',';
            out << frame.fields[j];
        }
        out << "]}";
    }
    out << "],\"groups\":[";
    for (size_t i = 0; i < save.groups.size(); ++i) {
        if (i) out << ',';
        const auto& group = save.groups[i];
        NativeSystemSave common;
        common.variables = group.values;
        // This nested object is explicitly a common value-decoder view, not an
        // assertion that the story file is a MajiroSys file.
        out << "{\"serialized_slots_hex\":\"" << hexbytes(group.serialized_slots)
            << "\",\"common_value_view\":" << native_system_save_json(common) << '}';
    }
    out << "],\"object_pool_hex\":\"" << hexbytes(save.serialized_object_pool) << "\",\"script_requirements\":[";
    auto requirements = native_story_script_requirements(save);
    for (size_t i = 0; i < requirements.size(); ++i) {
        if (i) out << ',';
        const auto& requirement = requirements[i];
        out << "{\"script_bytes_hex\":\""
            << hexbytes(reinterpret_cast<const uint8_t*>(requirement.script_bytes.data()), requirement.script_bytes.size())
            << "\",\"line_id\":" << requirement.line_id << ",\"code_offsets\":[";
        for (size_t j = 0; j < requirement.code_offsets.size(); ++j) {
            if (j) out << ',';
            out << requirement.code_offsets[j];
        }
        out << "],\"identity_verified\":false}";
    }
    out << "]}";
    return std::move(buffer.data);
}
}
