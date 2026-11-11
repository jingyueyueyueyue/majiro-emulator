#pragma once
#include "majiro_image.hpp"
#include <functional>
#include <string>
#include <vector>
namespace majiro::pixels {
struct GlyphRaster {
    int width = 0, height = 0;
    std::vector<uint8_t> alpha;
};
struct Host {
    std::function<Image(const std::string&, bool)> load;
    std::function<GlyphRaster(const std::string&, int, const std::vector<int>&, const std::string&, bool)> glyphs;
    std::function<void(int, int, const std::vector<uint8_t>&)> present;
    std::function<void(const std::string&, int, int, const std::vector<uint8_t>&)> png;
    std::function<std::string(const std::string&)> platform;
};
void install(Host callbacks);
std::string command(const std::string& request);
}
