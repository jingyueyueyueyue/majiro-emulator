// In-process host for benchmarking Android's generated page provider on Windows.
#include "portable_pixels.hpp"
#include <cstddef>
#include <cstdint>
#include <string>
#include <utility>
#include <vector>

static std::vector<uint8_t> frame;
static majiro::Image input;

template<typename Host>
static void install_frame_cache(Host& host) {
    if constexpr (requires { host.present_frame; }) {
        host.present_frame = [](int, int, const std::vector<uint8_t>& pixels, bool comparable) {
            if (comparable && frame == pixels) return false;
            frame = pixels;
            return true;
        };
    }
}

extern "C" __declspec(dllexport) void bench_install() {
    majiro::pixels::Host host;
    host.present = [](int, int, auto&& pixels) {
        frame = std::forward<decltype(pixels)>(pixels);
    };
    install_frame_cache(host);
    host.load = [](const std::string&, bool) { return input; };
    majiro::pixels::install(std::move(host));
}

extern "C" __declspec(dllexport) void bench_input(unsigned w, unsigned h, const uint8_t* rgba) {
    input.width = w;
    input.height = h;
    input.rgba.assign(rgba, rgba + size_t(w) * h * 4);
}

extern "C" __declspec(dllexport) const char* bench_command(const char* text) {
    static std::string response;
    response = majiro::pixels::command(text);
    return response.c_str();
}

extern "C" __declspec(dllexport) const uint8_t* bench_frame(size_t* size) {
    *size = frame.size();
    return frame.data();
}
