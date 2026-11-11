// Generated adapter: pixel algorithms are taken verbatim from the tested native provider.
#include "portable_pixels.hpp"
#include "screen_view.hpp"
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <map>
#include <mutex>
#include <sstream>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>
namespace majiro::pixels {
static Host host;
/*PAGE_ALGORITHMS*/

static Page load(const std::string& path, bool alpha, bool indexed = false) {
    auto image = host.load(path, alpha);
    const uint64_t pixels=uint64_t(image.width)*image.height;
    require(image.width&&image.height&&image.width<=32768&&image.height<=32768&&
            pixels<=PAGE_LIMIT/4&&image.rgba.size()==pixels*4,"invalid host image pixels");
    require(!indexed||image.indices.size()==pixels,"invalid host image indices");
    Page page = allocate(int(image.width), int(image.height), alpha);
    page.rgba = std::move(image.rgba);
    if (indexed) {
        require(!image.indices.empty(), "indexed image required");
        page.depth = 8;
        for (unsigned i = 0; i < 256; ++i)
            page.palette[i] = 0xff000000u | uint32_t(image.palette[i * 3]) << 16
                | uint32_t(image.palette[i * 3 + 1]) << 8 | image.palette[i * 3 + 2];
        for (size_t i = 0; i < image.indices.size(); ++i) {
            page.rgba[i * 4] = page.rgba[i * 4 + 1] = page.rgba[i * 4 + 2] = image.indices[i];
            page.rgba[i * 4 + 3] = 255;
        }
    } else if (!alpha) for (size_t i = 3; i < page.rgba.size(); i += 4) page.rgba[i] = 255;
    return page;
}
static void png(const Page& page, const std::string& path) {
    // Native PNG export materializes the indexed display palette and the
    // independently attached reverse-alpha page. Raw storage is not RGBA
    // whenever either of those page representations is present.
    std::vector<uint8_t> rgba;
    const auto* output=&page.rgba;
    if(page.depth==8||page.antidata){
        rgba=page.rgba;
        for(size_t at=0;at<rgba.size();at+=4){
            if(page.depth==8){
                const auto color=page.palette[page.rgba[at]];
                rgba[at]=uint8_t(color>>16);rgba[at+1]=uint8_t(color>>8);
                rgba[at+2]=uint8_t(color);rgba[at+3]=255;
            }else rgba[at+3]=alpha_at(page,at/4);
        }
        output=&rgba;
    }
    if (path == "screen") host.present(page.w, page.h, *output);
    else host.png(path, page.w, page.h, *output);
}
static void png_fast(const Page& page, const std::string& path) { png(page, path); }
static std::string run(const std::string& request) {
    std::istringstream in(request);
    std::string op; in >> op;
    if (op == "batch") {
        size_t offset = request.find('\n');
        require(offset != std::string::npos, "batch missing"); ++offset;
        while (offset < request.size()) {
            auto end = request.find('\n', offset); require(end != std::string::npos, "batch length missing");
            size_t length = std::stoul(request.substr(offset, end - offset)); offset = end + 1;
            require(length > 0 && length <= request.size() - offset, "batch command truncated");
            auto command = request.substr(offset, length);
            require(command.find("batch") != 0, "nested batch");
            require(run(command).rfind("OK", 0) == 0, "batch command failed");
            offset += length;
        }
        return "OK";
    }
    if (op == "reset" || op == "reset_pages") {
        pages.clear(); last_presented.clear(); last_width = last_height = 0;
        if (op == "reset") screen_view.reset();
        return "OK";
    }
    if (op == "drawmode") {
        int id, mode; require(bool(in >> id >> mode), "drawmode arguments");
        auto& page = get(id); require(page.depth == 32 && mode >= 0 && mode <= 4, "invalid drawmode");
        page.drawmode = mode; return "OK";
    }
    if (op == "session") return "OK portable_" + std::to_string(++frame_session);
    if (op == "fontfile" || op == "font_dialog" || op.rfind("save_", 0) == 0 || op == "sha256")
        return host.platform(request);
    /*BASIC_OPERATIONS*/
    if (op == "glyphs" || op == "text") {
        int id, height, half, boxw = 0, boxh = 0, align = -1;
        int64_t x,y;
        uint32_t color, shadow = 0;
        if (op == "glyphs")
            require(bool(in >> id >> x >> y >> height >> half >> color >> shadow), "glyph arguments");
        else
            require(bool(in >> id >> x >> y >> height >> half >> color >> boxw >> boxh >> align),
                    "text arguments");
        auto& page = get(id);
        require(page.depth == 32 && height > 0 && height <= 256
                && half >= (op == "glyphs" ? 1 : 0) && half <= 256,
                "text page/font bounds");
        std::string face, advances, message;
        std::getline(in, face); std::getline(in, face);
        std::vector<int> dx;
        if (op == "glyphs") {
            std::getline(in, advances);
            std::istringstream values(advances); int step;
            while (values >> step) {
                require(step >= 0 && step <= 512, "glyph advance invalid"); dx.push_back(step);
            }
        } else dx = {half, boxw, boxh, align};
        std::getline(in, message, '\0');
        auto raster = host.glyphs(face, height, dx, message, op == "text");
        require(raster.width > 0 && raster.height > 0
                && raster.alpha.size() == size_t(raster.width) * raster.height, "invalid glyph raster");
        if (op == "text" && boxw > 0) {
            if (align == 0) x += (boxw - raster.width) / 2;
            else if (align == -2) x += boxw - raster.width;
        }
        if (op == "text" && boxh > 0) y += (boxh - raster.height) / 2;
        auto paint = [&](int xx, int yy, unsigned alpha, uint32_t rgb) {
            int64_t px = x + xx, py = y + yy;
            if (!alpha || px < 0 || py < 0 || px >= page.w || py >= page.h) return;
            size_t at = (size_t(py) * page.w + px) * 4;
            if (page.antidata) {
                auto& mask = get(page.antidata);
                unsigned previous = 255 - mask.rgba[at];
                unsigned out = alpha + (previous * (255 - alpha) + 127) / 255;
                for (unsigned c = 0; c < 3; ++c) {
                    page.rgba[at + c] = uint8_t((unsigned((rgb >> (c * 8)) & 255) * alpha
                        + (page.rgba[at + c] * previous * (255 - alpha) + 127) / 255 + out / 2)
                        / std::max(1u, out));
                    mask.rgba[at + c] = uint8_t(255 - out);
                }
            } else for (unsigned c = 0; c < 3; ++c)
                page.rgba[at + c] = uint8_t((page.rgba[at + c] * (255 - alpha)
                    + ((rgb >> (c * 8)) & 255) * alpha + 127) / 255);
            page.rgba[at + 3] = 255;
        };
        int outline = std::min(8, int(shadow >> 24));
        if (outline) for (int yy = 0; yy < raster.height; ++yy)
            for (int xx = 0; xx < raster.width; ++xx) {
                unsigned alpha = 0;
                for (int dy = -outline; dy <= outline; ++dy)
                    for (int dx = -outline; dx <= outline; ++dx) {
                        int sx = xx + dx, sy = yy + dy;
                        if (dx * dx + dy * dy > outline * outline || sx < 0 || sy < 0
                            || sx >= raster.width || sy >= raster.height) continue;
                        alpha = std::max(alpha, unsigned(raster.alpha[size_t(sy) * raster.width + sx]));
                    }
                paint(xx, yy, alpha, shadow & 0xffffff);
            }
        for (int yy = 0; yy < raster.height; ++yy)
            for (int xx = 0; xx < raster.width; ++xx)
                paint(xx, yy, raster.alpha[size_t(yy) * raster.width + xx], color);
        return "OK " + std::to_string(raster.width) + " " + std::to_string(raster.height);
    }
    if (op == "png") {
        int id; require(bool(in >> id), "png arguments"); png(get(id), path_arg(in)); return "OK";
    }
    /*ADVANCED_OPERATIONS*/
void install(Host callbacks) {
    host = std::move(callbacks); pages.clear();checkpoint_pages.clear();checkpoint_active=false;
    last_presented.clear();last_width=last_height=0;screen_view.reset();
}
std::string command(const std::string& request) {
    try { return run(request); }
    catch (const std::exception& error) { return "ERR " + std::string(error.what()); }
}
}
