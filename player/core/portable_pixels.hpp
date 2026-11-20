#pragma once
#include "majiro_image.hpp"
#include <cstdint>
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
    std::function<void(int, int, std::vector<uint8_t>)> present;
    std::function<void(const std::string&, int, int, const std::vector<uint8_t>&)> png;
    std::function<std::string(const std::string&)> platform;
    // Optional screen sink with a retained CPU frame. Return false if an
    // equivalent frame is already displayed. A forced submission must publish.
    std::function<bool(int, int, const std::vector<uint8_t>&, bool comparable)> present_frame;
};
void install(Host callbacks);
std::string command(const std::string& request);
// Raster time spent inside page composition, excluding image I/O and export.
uint64_t compose_us();
void reset_perf();
}
