#pragma once
#include <cstddef>
#include <cstdint>
#include <functional>
#include <string>
#include <vector>

namespace majiro {
using ReadAt = std::function<void(uint64_t, uint8_t*, size_t)>;
struct Member {
    std::string raw_name;
    uint64_t hash = 0;
    uint32_t offset = 0, size = 0;
};
struct Archive {
    unsigned version = 0;
    uint64_t bytes = 0;
    std::vector<Member> members;
};
struct ScriptSummary {
    uint32_t main = 0, functions = 0, instructions = 0;
    bool has_main = false;
};
Archive read_archive(const ReadAt& read, uint64_t size);
ScriptSummary read_script(std::vector<uint8_t> data);
std::string inspect_archive(const ReadAt& read, uint64_t size, bool scripts);
}
