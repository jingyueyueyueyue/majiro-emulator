#include "majiro_save.hpp"
#include <algorithm>
#include <limits>
#include <locale>
#include <map>
#include <set>
#include <ostream>
#include <streambuf>
#include <stdexcept>
#include <string_view>

namespace majiro {
namespace {
constexpr size_t kSystemHeader = 0x9c58, kReadmarkHeader = 0x94;
constexpr uint64_t kMaxBytes = 64 * 1024 * 1024, kMaxRecords = 1000000;
constexpr std::string_view kSystem("MajiroSysV1.100\0", 16);
constexpr std::string_view kStory103("MajiroSavV1.103\0", 16);
constexpr std::string_view kStory105("MajiroSavV1.105\0", 16);

void require(bool good, const char* error) {
    if (!good) throw std::runtime_error(error);
}
uint32_t u32(const std::vector<uint8_t>& data, size_t at) {
    require(at <= data.size() && data.size() - at >= 4, "truncated native save field");
    return uint32_t(data[at]) | (uint32_t(data[at + 1]) << 8) |
           (uint32_t(data[at + 2]) << 16) | (uint32_t(data[at + 3]) << 24);
}
bool signature(const std::vector<uint8_t>& data, std::string_view value) {
    return data.size() >= value.size() &&
           std::equal(value.begin(), value.end(), data.begin());
}
std::string hex32(uint32_t value) {
    static constexpr char digits[] = "0123456789abcdef";
    std::string output(8, '0');
    for (int i = 7; i >= 0; --i) {
        output[i] = digits[value & 15];
        value >>= 4;
    }
    return output;
}
std::string hexbytes(const uint8_t* data, size_t size) {
    static constexpr char digits[] = "0123456789abcdef";
    std::string output;
    output.reserve(size * 2);
    for (size_t i = 0; i < size; ++i) {
        output.push_back(digits[data[i] >> 4]);
        output.push_back(digits[data[i] & 15]);
    }
    return output;
}
int64_t signed32(uint32_t value) {
    return value < 0x80000000u ? int64_t(value) : int64_t(value) - 0x100000000LL;
}
std::vector<uint8_t> read_file(const ReadAt& read, uint64_t bytes) {
    require(bytes <= kMaxBytes, "native save exceeds size bound");
    std::vector<uint8_t> data(static_cast<size_t>(bytes));
    if (bytes) read(0, data.data(), data.size());
    return data;
}
class JsonBuffer final : public std::streambuf {
public:
    std::string data;
protected:
    std::streamsize xsputn(const char* value, std::streamsize bytes) override {
        require(bytes >= 0 && static_cast<uint64_t>(bytes) <= kMaxBytes * 2 - data.size(),
                "native save JSON exceeds size bound");
        data.append(value, static_cast<size_t>(bytes));
        return bytes;
    }
    int_type overflow(int_type value) override {
        if (traits_type::eq_int_type(value, traits_type::eof())) return traits_type::not_eof(value);
        require(data.size() < kMaxBytes * 2, "native save JSON exceeds size bound");
        data.push_back(traits_type::to_char_type(value));
        return value;
    }
};

class Pool {
    const std::vector<uint8_t>& data_;
    size_t start_, size_;
    std::map<uint32_t, std::shared_ptr<NativeSaveObject>> objects_;
    std::map<uint32_t, uint64_t> spans_;
public:
    Pool(const std::vector<uint8_t>& data, size_t start) :
        data_(data), start_(start), size_(data.size() - start) {}
    uint32_t word(size_t at) const {
        require(at <= size_ && size_ - at >= 4, "truncated native object");
        return u32(data_, start_ + at);
    }
    void reserve(uint32_t offset, uint64_t bytes) {
        require(word(size_t(offset) + 4) == offset,
                "native object serialization offset disagrees with reference");
        const uint64_t end = uint64_t(offset) + bytes;
        auto next = spans_.lower_bound(offset);
        require(next == spans_.end() || next->first >= end, "overlapping native serialized objects");
        if (next != spans_.begin()) {
            auto prior = std::prev(next);
            require(prior->second <= offset, "overlapping native serialized objects");
        }
        spans_.emplace(offset, end);
    }
    std::shared_ptr<NativeSaveObject> object(uint32_t offset, uint32_t type) {
        require(type >= 2 && type <= 5, "unknown native object type");
        auto existing = objects_.find(offset);
        if (existing != objects_.end()) {
            require(existing->second->type == type, "native object alias has conflicting types");
            return existing->second;
        }
        auto result = std::make_shared<NativeSaveObject>();
        result->offset = offset;
        result->type = type;
        if (type == 2) {
            require(offset <= size_ && size_ - offset >= 12,
                    "string offset outside native object pool");
            uint32_t length = word(size_t(offset) + 8);
            require(length >= 1 && length <= size_ - offset - 12, "invalid native string length");
            const size_t begin = start_ + offset + 12;
            require(data_[begin + length - 1] == 0 &&
                    std::find(data_.begin() + begin, data_.begin() + begin + length - 1, 0)
                    == data_.begin() + begin + length - 1,
                    "native string length disagrees with C string");
            reserve(offset, uint64_t(length) + 12);
            result->bytes.assign(reinterpret_cast<const char*>(data_.data() + begin), length - 1);
        } else {
            require(offset <= size_ && size_ - offset >= 24,
                    "array offset outside native object pool");
            uint64_t count = 1;
            for (size_t i = 0; i < 3; ++i) {
                uint32_t dim = word(size_t(offset) + 8 + i * 4);
                require(dim <= kMaxRecords, "native array dimension exceeds bound");
                result->dimensions[i] = dim;
                count *= dim;
            }
            require(count <= kMaxRecords && 24 + count * 4 <= size_ - offset,
                    "native array size outside object pool");
            reserve(offset, 24 + count * 4);
            // Register before string-array children, so cross-type aliases fail.
            objects_.emplace(offset, result);
            size_t begin = size_t(offset) + (type == 5 ? 24 : 20);
            if (type == 5) {
                result->strings.reserve(static_cast<size_t>(count));
                for (size_t i = 0; i < count; ++i) {
                    uint32_t child = word(begin + i * 4);
                    result->strings.push_back(child == 0xffffffffu ? nullptr : object(child, 2));
                }
            } else {
                result->values.reserve(static_cast<size_t>(count));
                for (size_t i = 0; i < count; ++i) result->values.push_back(word(begin + i * 4));
            }
        }
        objects_.emplace(offset, result);
        return result;
    }
};

std::string name_key(std::string_view bytes) {
    // No locale conversion occurs in the core. CP932 trail bytes may include
    // 0x5c, so only ASCII code units may act as path separators.
    std::string key(bytes);
    for (size_t i = 0; i < bytes.size(); ++i) {
        uint8_t c = static_cast<uint8_t>(bytes[i]);
        if ((c >= 0x81 && c <= 0x9f) || (c >= 0xe0 && c <= 0xfc)) {
            require(i + 1 < bytes.size(), "truncated CP932 readmark script name");
            uint8_t trail = static_cast<uint8_t>(bytes[++i]);
            require((trail >= 0x40 && trail <= 0x7e) || (trail >= 0x80 && trail <= 0xfc),
                    "invalid CP932 readmark script name");
        } else {
            require(c != '/' && c != '\\' && c != ':', "invalid native readmark script name");
            if (c >= 'A' && c <= 'Z') key[i] = static_cast<char>(c + 32);
        }
    }
    require(key.size() >= 4 && key.substr(key.size() - 4) == ".mjo",
            "invalid native readmark script extension");
    return key;
}
void json_object(std::ostream& out, const NativeSaveObject& object) {
    constexpr const char* kinds[] = {"string", "int_array", "float_array", "string_array"};
    out << "{\"kind\":\"" << kinds[object.type - 2] << "\",\"object_offset\":" << object.offset;
    if (object.type == 2) {
        out << ",\"bytes_hex\":\"" << hexbytes(reinterpret_cast<const uint8_t*>(object.bytes.data()),
                                              object.bytes.size()) << "\"}";
        return;
    }
    out << ",\"dimensions\":[" << object.dimensions[0] << ',' << object.dimensions[1] << ','
        << object.dimensions[2] << "],\"values\":[";
    size_t count = object.type == 5 ? object.strings.size() : object.values.size();
    for (size_t i = 0; i < count; ++i) {
        if (i) out << ',';
        if (object.type == 3) out << signed32(object.values[i]);
        else if (object.type == 4) out << "{\"float32_bits\":\"" << hex32(object.values[i]) << "\"}";
        else if (object.strings[i]) json_object(out, *object.strings[i]);
        else out << "{\"kind\":\"string\",\"bytes_hex\":\"\",\"null_native_string\":true}";
    }
    out << "]}";
}
}

NativeSaveKind identify_native_save(const std::vector<uint8_t>& data) {
    if (signature(data, kSystem)) return NativeSaveKind::system;
    if (signature(data, kStory103)) return NativeSaveKind::story_103;
    if (signature(data, kStory105)) return NativeSaveKind::story_105;
    return NativeSaveKind::unknown;
}
NativeSystemSave read_native_system_save(const std::vector<uint8_t>& data) {
    require(data.size() <= kMaxBytes, "native save exceeds size bound");
    auto kind = identify_native_save(data);
    require(kind != NativeSaveKind::story_103 && kind != NativeSaveKind::story_105,
            "native story-slot import is not implemented; refusing partial restore");
    require(kind == NativeSaveKind::system, "unsupported native system-save signature");
    require(data.size() >= kSystemHeader, "truncated native system-save header");
    uint32_t count = u32(data, 16), pool_bytes = u32(data, 20);
    require(count <= kMaxRecords, "native variable count exceeds bound");
    require(kSystemHeader + uint64_t(count) * 16 + pool_bytes == data.size(),
            "native system-save size mismatch or trailing data");
    NativeSystemSave save;
    save.object_pool_bytes = pool_bytes;
    Pool pool(data, kSystemHeader + size_t(count) * 16);
    std::set<uint32_t> hashes;
    save.variables.reserve(count);
    for (size_t i = 0; i < count; ++i) {
        size_t at = kSystemHeader + i * 16;
        NativeSaveVariable value{u32(data, at), u32(data, at + 4), u32(data, at + 12), nullptr};
        require(hashes.insert(value.hash).second, "duplicate native persistent-variable hash");
        require(value.type <= 5, "unsupported native persistent-variable tag");
        if (value.type >= 2) {
            value.bits = 0;
            value.object = pool.object(u32(data, at + 8), value.type);
        }
        save.variables.push_back(std::move(value));
    }
    bool terminated = false;
    for (size_t i = 0; i < 10000; ++i) {
        uint32_t value = u32(data, 24 + i * 4);
        if (!value) terminated = true;
        else {
            require(!terminated, "nonzero native picture hashes after terminator");
            save.picture_basename_hashes.push_back(value);
        }
    }
    return save;
}
NativeSystemSave read_native_system_save(const ReadAt& read, uint64_t bytes) {
    return read_native_system_save(read_file(read, bytes));
}
bool NativeReadmark::marked(uint32_t line) const {
    return line <= line_count && size_t(line >> 3) < bits.size() &&
           (bits[line >> 3] & (uint8_t(1) << (line & 7))) != 0;
}
NativeReadmarks read_native_readmarks(const std::vector<uint8_t>& data) {
    require(data.size() <= kMaxBytes, "native readmarks exceed size bound");
    NativeReadmarks marks;
    std::set<std::string> names;
    size_t cursor = 0;
    while (cursor < data.size()) {
        require(marks.records.size() < kMaxRecords, "native readmark count exceeds bound");
        require(data.size() - cursor >= kReadmarkHeader, "truncated native readmark header");
        auto begin = data.begin() + cursor;
        auto terminator = std::find(begin, begin + 128, 0);
        require(terminator != begin && terminator != begin + 128,
                "unterminated or empty native readmark script name");
        require(std::all_of(terminator, begin + 128, [](uint8_t c) { return c == 0; }),
                "invalid native readmark script-name padding");
        NativeReadmark record;
        record.script_bytes.assign(reinterpret_cast<const char*>(data.data() + cursor),
                                   static_cast<size_t>(terminator - begin));
        require(names.insert(name_key(record.script_bytes)).second, "duplicate native readmark script name");
        uint32_t bytes = u32(data, cursor + 128);
        record.line_count = u32(data, cursor + 132);
        require(record.line_count <= 0x7fffffffu &&
                bytes == (uint64_t(record.line_count) + 7) / 8 + 16,
                "native readmark bitset length mismatch");
        require(bytes <= data.size() - cursor - kReadmarkHeader, "truncated native readmark bitset");
        record.source_offset = cursor;
        auto start = begin + kReadmarkHeader;
        record.bits.assign(start, start + bytes);
        marks.records.push_back(std::move(record));
        cursor += kReadmarkHeader + bytes;
    }
    return marks;
}
NativeReadmarks read_native_readmarks(const ReadAt& read, uint64_t bytes) {
    return read_native_readmarks(read_file(read, bytes));
}
std::string native_system_save_json(const NativeSystemSave& save) {
    JsonBuffer buffer;
    std::ostream out(&buffer);
    out.exceptions(std::ios::badbit | std::ios::failbit);
    out.imbue(std::locale::classic());
    out << "{\"schema\":1,\"format\":\"MajiroSysV1.100\",\"native_story_restore_supported\":false,"
           "\"variable_count\":" << save.variables.size() << ",\"object_pool_bytes\":"
        << save.object_pool_bytes << ",\"variables\":[";
    for (size_t i = 0; i < save.variables.size(); ++i) {
        const auto& value = save.variables[i];
        if (i) out << ',';
        out << "{\"hash\":\"" << hex32(value.hash) << "\",\"native_type\":" << value.type << ",\"value\":";
        if (value.type == 0) out << signed32(value.bits);
        else if (value.type == 1) out << "{\"float32_bits\":\"" << hex32(value.bits) << "\"}";
        else json_object(out, *value.object);
        out << '}';
    }
    out << "],\"picture_basename_hashes\":[";
    for (size_t i = 0; i < save.picture_basename_hashes.size(); ++i) {
        if (i) out << ',';
        out << '"' << hex32(save.picture_basename_hashes[i]) << '"';
    }
    out << "]}";
    return std::move(buffer.data);
}
std::string native_readmarks_json(const NativeReadmarks& marks) {
    JsonBuffer buffer;
    std::ostream out(&buffer);
    out.exceptions(std::ios::badbit | std::ios::failbit);
    out.imbue(std::locale::classic());
    out << "{\"schema\":1,\"format\":\"Majiro native readmarks\",\"native_story_restore_supported\":false,"
           "\"record_count\":" << marks.records.size() << ",\"records\":[";
    for (size_t i = 0; i < marks.records.size(); ++i) {
        const auto& record = marks.records[i];
        if (i) out << ',';
        out << "{\"script_bytes_hex\":\""
            << hexbytes(reinterpret_cast<const uint8_t*>(record.script_bytes.data()), record.script_bytes.size())
            << "\",\"line_count\":" << record.line_count << ",\"source_offset\":" << record.source_offset
            << ",\"bits_hex\":\"" << hexbytes(record.bits.data(), record.bits.size()) << "\",\"marked_line_ids\":[";
        bool emitted = false;
        for (size_t byte = 0; byte < record.bits.size() && byte * 8 <= record.line_count; ++byte) {
            if (!record.bits[byte]) continue;
            for (uint32_t bit = 0; bit < 8; ++bit) {
                uint64_t line = uint64_t(byte) * 8 + bit;
                if (line > record.line_count || !(record.bits[byte] & (uint8_t(1) << bit))) continue;
                if (emitted) out << ',';
                out << line;
                emitted = true;
            }
        }
        out << "]}";
    }
    out << "]}";
    return std::move(buffer.data);
}
}
