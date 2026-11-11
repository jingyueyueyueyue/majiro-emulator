#pragma once
#include <array>
#include <cstdint>
#include <string>
#include <vector>

namespace majiro {
struct Image {
    uint32_t width = 0, height = 0;
    std::string base_name;
    std::vector<uint8_t> rgba;
    std::vector<uint8_t> indices;
    std::array<uint8_t, 768> palette{};
};
Image read_image(const std::vector<uint8_t>& data, const std::vector<uint8_t>* password = nullptr);
void combine_base(Image& overlay, const Image& base);
void apply_mask(Image& color, const Image& mask);
Image transparency_mask(const Image& color);
std::string inspect_image(const std::vector<uint8_t>& data, const std::vector<uint8_t>* password);
}
