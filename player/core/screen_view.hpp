#pragma once
#include <algorithm>
#include <cstdint>
#include <stdexcept>
#include <vector>

namespace majiro::screen {
// These are emulated client pixels. SDL/other hosts apply the final device
// aspect fit after this operation, independently of the engine's own zoom.
enum class Kind { modern = 0, fixed = 1, legacy = 2 };

class Frame {
    int width_ = 0, height_ = 0;
    std::vector<uint8_t> pixels_;
    void black(int64_t left, int64_t top, int64_t right, int64_t bottom) {
        left = std::max<int64_t>(0, left); top = std::max<int64_t>(0, top);
        right = std::min<int64_t>(width_, right); bottom = std::min<int64_t>(height_, bottom);
        for (int64_t y = top; y < bottom; ++y)
            for (int64_t x = left; x < right; ++x) {
                auto at = (size_t(y) * width_ + size_t(x)) * 4;
                pixels_[at] = pixels_[at+1] = pixels_[at+2] = 0;
                pixels_[at+3] = 255;
            }
    }
public:
    void reset() { width_ = height_ = 0; std::vector<uint8_t>().swap(pixels_); }
    const std::vector<uint8_t>& compose(const std::vector<uint8_t>& source, int width, int height,
                                       int32_t home_x, int32_t home_y, Kind kind) {
        if (width <= 0 || height <= 0 || source.size() != size_t(width) * height * 4)
            throw std::runtime_error("invalid screen view source");
        if (kind != Kind::modern && kind != Kind::fixed && kind != Kind::legacy)
            throw std::runtime_error("unknown screen view profile");
        if (source.data() == pixels_.data()) {
            auto frozen = source;
            return compose(frozen, width, height, home_x, home_y, kind);
        }
        if (width_ != width || height_ != height) {
            width_ = width; height_ = height;
            pixels_.assign(source.size(), 0);
            for (size_t at = 3; at < pixels_.size(); at += 4) pixels_[at] = 255;
        }
        int64_t dx = home_x, dy = home_y;
        if (kind == Kind::legacy) {
            const int64_t rx = home_x % width, ry = home_y % height;
            // Preserve original FillRect bounds, including the untouched last
            // row/column and the X remainder used by positive-Y clearing.
            if (rx > 0) black(width-rx-1, 0, width-1, height-1);
            if (rx < 0) black(0, 0, -rx, height-1);
            if (ry > 0) black(0, height-rx-1, width, height-1);
            if (ry < 0) black(0, 0, width, -1-ry);
            dx = -rx; dy = -ry;
        } else {
            black(0, 0, width, height);
            if (kind == Kind::fixed) { dx = -dx; dy = -dy; }
        }
        const int64_t x0 = std::max<int64_t>(0, -dx), x1 = std::min<int64_t>(width, width-dx);
        const int64_t y0 = std::max<int64_t>(0, -dy), y1 = std::min<int64_t>(height, height-dy);
        if (x1 > x0) for (int64_t sy = y0; sy < y1; ++sy) {
            auto src = (size_t(sy) * width + size_t(x0)) * 4;
            auto dst = (size_t(sy+dy) * width + size_t(x0+dx)) * 4;
            std::copy_n(source.data()+src, size_t(x1-x0)*4, pixels_.data()+dst);
        }
        return pixels_;
    }
};
}
