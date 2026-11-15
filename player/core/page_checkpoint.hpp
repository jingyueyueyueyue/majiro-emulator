#pragma once
#include <algorithm>
#include <cstdint>
#include <sstream>
#include <stdexcept>
#include <string>

namespace majiro::checkpoint {
inline void require(bool value, const char* message) {
    if (!value) throw std::runtime_error(message);
}
inline void hex(std::string& output, uint32_t value) {
    static constexpr char digits[] = "0123456789abcdef";
    for (int shift = 28; shift >= 0; shift -= 4) output += digits[(value >> shift) & 15];
}
inline uint32_t unhex(const std::string& input, size_t& at) {
    require(at <= input.size() && input.size() - at >= 8, "truncated checkpoint pixel");
    uint32_t value = 0;
    for (int i = 0; i < 8; ++i) {
        char c = input[at++];
        require((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'), "invalid checkpoint hex");
        value = (value << 4) | uint32_t(c <= '9' ? c - '0' : c - 'a' + 10);
    }
    return value;
}
template<class Page> uint32_t pixel(const Page& page, size_t at) {
    return uint32_t(page.rgba[at * 4]) | uint32_t(page.rgba[at * 4 + 1]) << 8 |
        uint32_t(page.rgba[at * 4 + 2]) << 16 | uint32_t(page.rgba[at * 4 + 3]) << 24;
}
// Data commands are bounded independently of the number/size of live pages.
// Literal blocks preserve arbitrary pixels; runs keep cleared pages small.
template<class Map> std::string export_pages(const Map& pages) {
    std::string result = "OK\n";
    auto append = [&](const std::string& command) {
        require(command.size() < 55000, "checkpoint pixel command too large");
        require(result.size() + command.size() + 16 <= 16u * 1024 * 1024,
                "checkpoint pixels exceed portable save bound");
        result += std::to_string(command.size()) + "\n" + command;
    };
    for (const auto& entry : pages) {
        const auto& page = entry.second;
        std::string palette;
        for (auto color : page.palette) hex(palette, color);
        append("page_state " + std::to_string(entry.first) + " " + std::to_string(page.w) +
            " " + std::to_string(page.h) + " " + std::to_string(page.depth) + " " +
            std::to_string(page.drawmode) + " " + std::to_string(page.antidata) + " " +
            std::to_string(page.owns_alpha) + " " + std::to_string(page.release_requested) +
            " " + std::to_string(page.alpha) + " " + palette);
        const size_t count = page.rgba.size() / 4;
        size_t at = 0;
        while (at < count) {
            const size_t begin = at, end = std::min(count, at + 4096);
            std::string data;
            while (at < end) {
                const auto value = pixel(page, at);
                size_t run = 1;
                while (at + run < end && pixel(page, at + run) == value) ++run;
                if (run >= 3) {
                    data += "R" + std::to_string(run) + ":";
                    hex(data, value); at += run;
                } else {
                    const size_t first = at;
                    at += run;
                    while (at < end) {
                        if (at + 2 < end && pixel(page, at) == pixel(page, at + 1) &&
                            pixel(page, at) == pixel(page, at + 2)) break;
                        ++at;
                    }
                    data += "L" + std::to_string(at - first) + ":";
                    for (size_t i = first; i < at; ++i) hex(data, pixel(page, i));
                }
            }
            append("page_pixels " + std::to_string(entry.first) + " " +
                   std::to_string(begin) + " " + data);
        }
    }
    return result;
}
template<class Page> void restore_pixels(Page& page, size_t offset, const std::string& data) {
    const size_t capacity = page.rgba.size() / 4;
    require(offset <= capacity, "checkpoint pixel offset outside page");
    size_t at = 0, written = 0;
    auto put = [&](uint32_t value) {
        require(offset < capacity && ++written <= 4096, "checkpoint pixels outside page");
        for (unsigned channel = 0; channel < 4; ++channel)
            page.rgba[offset * 4 + channel] = uint8_t(value >> (channel * 8));
        ++offset;
    };
    while (at < data.size()) {
        const char kind = data[at++];
        require(kind == 'L' || kind == 'R', "invalid checkpoint pixel encoding");
        size_t count = 0, digits = 0;
        while (at < data.size() && data[at] >= '0' && data[at] <= '9') {
            require(++digits <= 4, "checkpoint pixel run exceeds bound");
            count = count * 10 + size_t(data[at++] - '0');
        }
        require(digits && count > 0 && count <= 4096 - written &&
            count <= capacity - offset && at < data.size() && data[at++] == ':',
            "invalid checkpoint pixel run");
        if (kind == 'R') {
            auto value = unhex(data, at);
            for (size_t i = 0; i < count; ++i) put(value);
        } else for (size_t i = 0; i < count; ++i) put(unhex(data, at));
    }
    require(written > 0, "empty checkpoint pixel data");
}
}
