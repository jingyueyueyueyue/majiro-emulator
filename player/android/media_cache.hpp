#pragma once
#include <cstdint>
#include <filesystem>
#include <string>
#include <system_error>

namespace majiro::android_host {
inline bool cached_media_file_valid(const std::string& path, uint64_t expected_size) {
    std::error_code error;
    if (!std::filesystem::is_regular_file(path, error) || error) return false;
    auto size = std::filesystem::file_size(path, error);
    return !error && size == expected_size;
}
}
