#include "majiro_core.hpp"
#include "majiro_opcodes.hpp"
#include <algorithm>
#include <array>
#include <cstring>
#include <limits>
#include <set>
#include <sstream>
#include <stdexcept>
#include <utility>

namespace majiro {
namespace {
void require(bool ok, const char* why) {
    if (!ok) throw std::runtime_error(why);
}
uint16_t u16(const uint8_t* p) {
    return uint16_t(p[0]) | uint16_t(p[1]) << 8;
}
uint32_t u32(const uint8_t* p) {
    return uint32_t(p[0]) | uint32_t(p[1]) << 8 | uint32_t(p[2]) << 16 | uint32_t(p[3]) << 24;
}
uint64_t u64(const uint8_t* p) {
    return uint64_t(u32(p)) | uint64_t(u32(p + 4)) << 32;
}
std::vector<uint8_t> bytes(const ReadAt& read, uint64_t total, uint64_t at, uint64_t size) {
    require(at <= total && size <= total - at, "read outside file");
    require(size <= 64u * 1024 * 1024, "read exceeds memory limit");
    std::vector<uint8_t> result(static_cast<size_t>(size));
    if (size) read(at, result.data(), result.size());
    return result;
}
class Cursor {
public:
    const std::vector<uint8_t>& data;
    size_t pos = 0;
    explicit Cursor(const std::vector<uint8_t>& value) : data(value) {}
    void skip(size_t count) {
        require(pos <= data.size() && count <= data.size() - pos, "truncated bytecode operand");
        pos += count;
    }
    uint16_t word() {
        auto at = pos; skip(2); return u16(data.data() + at);
    }
    uint32_t dword() {
        auto at = pos; skip(4); return u32(data.data() + at);
    }
};
std::array<uint8_t, 1024> xor_key() {
    std::array<uint8_t, 1024> key{};
    for (uint32_t i = 0; i < 256; ++i) {
        uint32_t value = i;
        for (int n = 0; n < 8; ++n) value = (value >> 1) ^ ((value & 1) ? 0xEDB88320u : 0u);
        for (unsigned n = 0; n < 4; ++n) key[i * 4 + n] = uint8_t(value >> (n * 8));
    }
    return key;
}
std::string quote(const std::string& value) {
    std::ostringstream out; out << '"';
    constexpr char hex[] = "0123456789abcdef";
    for (unsigned char c : value) {
        if (c == '"' || c == '\\') out << '\\' << char(c);
        else if (c < 32 || c > 126)
            out << "\\u00" << hex[c >> 4] << hex[c & 15];
        else out << char(c);
    }
    return out.str() + '"';
}
bool script_name(const std::string& name) {
    if (name.size() < 4) return false;
    std::string suffix = name.substr(name.size() - 4);
    for (auto& c : suffix) if (c >= 'A' && c <= 'Z') c = char(c - 'A' + 'a');
    return suffix == ".mjo";
}
}

Archive read_archive(const ReadAt& read, uint64_t total) {
    auto header = bytes(read, total, 0, 28);
    unsigned version = header[10] - '0';
    require(version >= 1 && version <= 3, "unsupported ARC version");
    auto signature = std::string("MajiroArcV") + char('0' + version) + ".000";
    require(std::memcmp(header.data(), signature.c_str(), 16) == 0, "invalid ARC signature");
    uint32_t count = u32(header.data() + 16);
    uint32_t names_at = u32(header.data() + 20), data_at = u32(header.data() + 24);
    uint64_t stride = (version + 1) * 4, table_count = uint64_t(count) + (version == 1);
    require(count <= 1000000 && names_at == 28 + stride * table_count, "invalid ARC index");
    require(names_at <= data_at && data_at <= total, "invalid ARC data offset");
    auto entries = bytes(read, total, 28, stride * table_count);
    auto names = bytes(read, total, names_at, uint64_t(data_at) - names_at);
    if (version == 1) {
        auto last = entries.data() + count * stride;
        require(u32(last) == 0 && u32(last + 4) == total, "invalid ARC V1 terminal entry");
    }
    require(std::count(names.begin(), names.end(), uint8_t(0)) == count, "ARC filename count mismatch");
    require(names.empty() || names.back() == 0, "unterminated ARC filename table");
    require(count != 0 || names.empty(), "nonempty ARC names for empty archive");
    Archive archive{version, total, {}};
    archive.members.reserve(count);
    size_t name_at = 0;
    std::vector<std::pair<uint64_t, uint64_t>> spans;
    for (uint32_t i = 0; i < count; ++i) {
        auto end = std::find(names.begin() + name_at, names.end(), 0);
        require(end != names.end() && end != names.begin() + name_at, "empty or invalid ARC filename");
        auto p = entries.data() + i * stride;
        uint64_t hash = version == 3 ? u64(p) : u32(p);
        auto hash_size = version == 3 ? 8 : 4;
        uint32_t offset = u32(p + hash_size);
        uint64_t size = 0;
        if (version == 1) {
            auto next = u32(p + stride + 4);
            require(next >= offset, "descending ARC V1 offsets");
            size = uint64_t(next) - offset;
        } else size = u32(p + hash_size + 4);
        require(offset >= data_at && offset <= total && size <= total - offset, "ARC member outside file");
        archive.members.push_back(Member{
            std::string(names.begin() + name_at, end), hash, offset, static_cast<uint32_t>(size)});
        if (size) spans.emplace_back(offset, uint64_t(offset) + size);
        name_at = size_t(end - names.begin()) + 1;
    }
    std::sort(spans.begin(), spans.end());
    for (size_t i = 1; i < spans.size(); ++i)
        require(spans[i - 1].second <= spans[i].first, "overlapping ARC members");
    return archive;
}

ScriptSummary read_script(std::vector<uint8_t> data) {
    require(data.size() >= 32, "truncated MJO header");
    bool encrypted = std::memcmp(data.data(), "MajiroObjX1.000", 16) == 0;
    require(encrypted || std::memcmp(data.data(), "MajiroObjV1.000", 16) == 0
                      || std::memcmp(data.data(), "MjPlainBytecode", 16) == 0, "unsupported MJO signature");
    uint32_t main = u32(data.data() + 16), count = u32(data.data() + 24);
    uint64_t code_at = 32ull + uint64_t(count) * 8;
    require(code_at <= data.size(), "truncated MJO function table");
    uint32_t size = u32(data.data() + code_at - 4);
    require(code_at + size == data.size(), "invalid MJO bytecode size");
    std::vector<uint32_t> functions;
    functions.reserve(count);
    for (uint32_t i = 0; i < count; ++i) functions.push_back(u32(data.data() + 32 + i * 8));
    std::vector<uint8_t> code(data.begin() + static_cast<size_t>(code_at), data.end());
    if (encrypted) {
        static const auto key = xor_key();
        for (size_t i = 0; i < code.size(); ++i) code[i] ^= key[i & 1023];
    }
    Cursor in(code);
    std::set<uint32_t> boundaries;
    std::vector<int64_t> targets;
    uint32_t instructions = 0;
    while (in.pos < code.size()) {
        boundaries.insert(static_cast<uint32_t>(in.pos));
        uint16_t opcode = in.word();
        auto definition = std::find_if(kOpcodes.begin(), kOpcodes.end(),
                                      [opcode](const auto& row) { return row.code == opcode; });
        require(definition != kOpcodes.end(), "unknown MJO opcode");
        for (const char* field = definition->encoding; *field; ++field) {
            switch (*field) {
            case 's': {
                uint16_t length = in.word();
                in.skip(length);
                require(length != 0 && code[in.pos - 1] == 0, "unterminated MJO string");
                break;
            }
            case 't': { auto length = in.word(); in.skip(length); break; }
            case 'c': {
                auto count_entries = in.word();
                for (uint32_t i = 0; i < count_entries; ++i) {
                    int32_t relative = static_cast<int32_t>(in.dword());
                    targets.push_back(static_cast<int64_t>(in.pos) + relative);
                }
                break;
            }
            case 'j': {
                int32_t relative = static_cast<int32_t>(in.dword());
                targets.push_back(static_cast<int64_t>(in.pos) + relative);
                break;
            }
            case '0': require(in.dword() == 0, "nonzero MJO call placeholder"); break;
            case 'h': case 'i': case 'r': in.skip(4); break;
            case 'f': case 'o': case 'a': case 'l': in.skip(2); break;
            default: throw std::runtime_error("unsupported opcode field definition");
            }
        }
        ++instructions;
    }
    if (main != 0xFFFFFFFFu) require(boundaries.count(main) != 0, "MJO main outside instruction boundary");
    for (auto offset : functions) require(boundaries.count(offset) != 0, "invalid MJO function entry");
    for (auto target : targets)
        require(target >= 0 && target <= std::numeric_limits<uint32_t>::max()
                && boundaries.count(static_cast<uint32_t>(target)) != 0, "invalid MJO branch target");
    return {main, count, instructions, main != 0xFFFFFFFFu};
}

std::string inspect_archive(const ReadAt& read, uint64_t size, bool scripts) {
    try {
        auto archive = read_archive(read, size);
        uint32_t parsed = 0, failed = 0, no_main = 0;
        uint64_t instructions = 0;
        std::ostringstream errors;
        for (const auto& member : archive.members) {
            if (!scripts || !script_name(member.raw_name)) continue;
            try {
                auto summary = read_script(bytes(read, size, member.offset, member.size));
                ++parsed; instructions += summary.instructions;
                if (!summary.has_main) ++no_main;
            } catch (const std::exception& error) {
                if (failed < 16) {
                    if (failed) errors << ',';
                    errors << "{\"member_bytes\":" << quote(member.raw_name)
                           << ",\"error\":" << quote(error.what()) << '}';
                }
                ++failed;
            }
        }
        std::ostringstream out;
        out << "{\"status\":\"" << (failed ? "failed" : "passed")
            << "\",\"arc_version\":" << archive.version << ",\"members\":" << archive.members.size()
            << ",\"parsed_scripts\":" << parsed << ",\"failed_scripts\":" << failed
            << ",\"script_instructions\":" << instructions << ",\"scripts_without_main\":" << no_main
            << ",\"playable\":false,\"errors\":[" << errors.str() << "]}";
        return out.str();
    } catch (const std::exception& error) {
        return "{\"status\":\"failed\",\"playable\":false,\"error\":" + quote(error.what()) + "}";
    }
}
}
