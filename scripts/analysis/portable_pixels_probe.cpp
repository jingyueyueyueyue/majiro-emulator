// Binary pipe harness for the same generated pixel provider used by Android.
#include "portable_pixels.hpp"
#include <cstdio>
#include <fcntl.h>
#include <io.h>
#include <stdexcept>
#include <utility>
#include <filesystem>
#include <fstream>

static void word(uint32_t value) { std::fwrite(&value, 4, 1, stdout); }
int main() {
    _setmode(_fileno(stdin), _O_BINARY); _setmode(_fileno(stdout), _O_BINARY);
    std::vector<uint8_t> frame;
    int width = 0, height = 0;
    majiro::pixels::Host host;
    host.present = [&](int w, int h, const std::vector<uint8_t>& pixels) {
        width = w; height = h; frame = pixels;
    };
    host.load = [](const std::string& name, bool) -> majiro::Image {
        if(!name.starts_with("fixture://"))throw std::runtime_error("unconfigured image fixture");
        std::ifstream input(std::filesystem::u8path(name.substr(10)),std::ios::binary);
        uint32_t fields[3]={};input.read(reinterpret_cast<char*>(fields),sizeof(fields));
        if(!input||!fields[0]||!fields[1]||uint64_t(fields[0])*fields[1]>16u*1024*1024)
            throw std::runtime_error("invalid image fixture header");
        majiro::Image image;image.width=fields[0];image.height=fields[1];
        input.read(reinterpret_cast<char*>(image.palette.data()),image.palette.size());
        image.rgba.resize(size_t(fields[0])*fields[1]*4);
        input.read(reinterpret_cast<char*>(image.rgba.data()),image.rgba.size());
        if(fields[2]==8){
            image.indices.resize(size_t(fields[0])*fields[1]);
            input.read(reinterpret_cast<char*>(image.indices.data()),image.indices.size());
        }
        if(!input)throw std::runtime_error("truncated image fixture pixels");
        return image;
    };
    host.glyphs = [](const std::string&, int, const std::vector<int>&, const std::string&, bool)
        -> majiro::pixels::GlyphRaster { throw std::runtime_error("unconfigured font fixture"); };
    host.png = [](const std::string&, int, int, const std::vector<uint8_t>&) {
        throw std::runtime_error("file output is unavailable in pipe fixture");
    };
    host.platform = [](const std::string&) -> std::string { throw std::runtime_error("unconfigured platform fixture"); };
    majiro::pixels::install(std::move(host));
    uint32_t length;
    while (std::fread(&length, 4, 1, stdin) == 1) {
        if (length > 1024 * 1024) return 2;
        std::string request(length, '\0');
        if (std::fread(request.data(), 1, length, stdin) != length) return 3;
        frame.clear(); width = height = 0;
        auto result = majiro::pixels::command(request);
        word(uint32_t(result.size())); std::fwrite(result.data(), 1, result.size(), stdout);
        word(uint32_t(width)); word(uint32_t(height)); word(uint32_t(frame.size()));
        std::fwrite(frame.data(), 1, frame.size(), stdout); std::fflush(stdout);
    }
    return std::ferror(stdin) ? 4 : 0;
}
