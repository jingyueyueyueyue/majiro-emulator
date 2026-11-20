#pragma once
#include <SDL3/SDL.h>
#include <algorithm>
#include <cstdint>
#include <cstring>
#include <iomanip>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

namespace majiro::android_host {

inline void validate_rgba(int width, int height, const std::vector<uint8_t>& bytes) {
    if (width <= 0 || height <= 0 || uint64_t(width) * uint64_t(height) > 16u * 1024 * 1024
        || uint64_t(width) * uint64_t(height) * 4 != bytes.size())
        throw std::runtime_error("retained RGBA frame dimensions/size invalid");
}

inline std::string socket_name(const std::string& package) {
    if (package == "org.majiro.player") return "majiro-debug";
    if (package == "org.majiro.player.recovery") return "majiro-debug-recovery";
    return "majiro-debug-" + package;
}

// Caller retains CPU bytes. No extra movie-frame copy is needed.
class TextureCache {
    SDL_Texture* texture_ = nullptr;
    int width_ = 0, height_ = 0;
    bool dirty_ = true;
    // A zero row count means a pending upload must restore the entire texture.
    SDL_Rect rows_{};
public:
    uint64_t uploads = 0, creations = 0, uploaded_bytes = 0;
    TextureCache() = default;
    TextureCache(const TextureCache&) = delete;
    TextureCache& operator=(const TextureCache&) = delete;
    SDL_Texture* get() const { return texture_; }
    bool dirty() const { return dirty_; }
    void changed() { dirty_ = true; rows_ = {}; }
    bool changed_frame(int width, int height, const std::vector<uint8_t>& previous,
                       const std::vector<uint8_t>& next) {
        validate_rgba(width, height, next);
        if (previous.size() != next.size()) { changed(); return true; }
        const size_t pitch = size_t(width) * 4;
        auto same_row = [&](int y) {
            const size_t offset = size_t(y) * pitch;
            return std::memcmp(previous.data() + offset, next.data() + offset, pitch) == 0;
        };
        // Search from the edges. A full-height change only inspects two rows.
        int top = 0, bottom = height;
        while (top < bottom && same_row(top)) ++top;
        if (top == bottom) return false;
        while (bottom > top + 1 && same_row(bottom - 1)) --bottom;
        if (!texture_ || width != width_ || height != height_) changed();
        else if (!dirty_ || rows_.h) {
            if (dirty_) {
                top = std::min(top, rows_.y);
                bottom = std::max(bottom, rows_.y + rows_.h);
            }
            rows_ = {0, top, width, bottom - top};
            dirty_ = true;
        }
        return true;
    }
    void invalidate() {
        if (texture_) SDL_DestroyTexture(texture_);
        texture_ = nullptr;
        width_ = height_ = 0;
        changed();
    }
    void clear() {
        invalidate();
        uploads = creations = uploaded_bytes = 0;
    }
    bool upload(SDL_Renderer* renderer, int width, int height, const std::vector<uint8_t>& bytes,
                const char* label, std::string& error) {
        validate_rgba(width, height, bytes);
        auto failed = [&](const char* operation) {
            error = std::string(label) + ": " + operation + ": " + SDL_GetError();
            invalidate();
            return false;
        };
        if (texture_ && (width != width_ || height != height_)) invalidate();
        if (!texture_) {
            texture_ = SDL_CreateTexture(renderer, SDL_PIXELFORMAT_RGBA32,
                                         SDL_TEXTUREACCESS_STREAMING, width, height);
            if (!texture_) return failed("SDL_CreateTexture");
            ++creations;
            if (!SDL_SetTextureScaleMode(texture_, SDL_SCALEMODE_LINEAR))
                return failed("SDL_SetTextureScaleMode");
            width_ = width; height_ = height;
            changed();
        }
        if (dirty_) {
            const auto* region = rows_.h ? &rows_ : nullptr;
            const auto* pixels = bytes.data() + size_t(rows_.y) * width * 4;
            if (!SDL_UpdateTexture(texture_, region, pixels, width * 4))
                return failed("SDL_UpdateTexture");
            ++uploads;
            uploaded_bytes += uint64_t(width) * (region ? region->h : height) * 4;
            dirty_ = false;
        }
        return true;
    }
};

struct FrameView {
    int width = 0, height = 0;
    const std::vector<uint8_t>* rgba = nullptr;
    bool available() const { return rgba && !rgba->empty(); }
};

class Presentation {
    bool has_frame_ = false, show_scene_ = false, show_movie_ = false;
    bool scene_available_ = false, video_available_ = false;
    SDL_FRect movie_rect_{};
public:
    TextureCache screen, movie;
    uint64_t attempts = 0, presented = 0, failures = 0;
    uint64_t device_resets = 0, target_resets = 0, foregrounds = 0;
    std::string last_error, last_event;

    // Called only on the SDL render thread. Never resets logical sizing/fit policy.
    bool event(Uint32 type) {
        if (type == SDL_EVENT_RENDER_DEVICE_RESET) {
            ++device_resets; last_event = "device_reset";
        } else if (type == SDL_EVENT_RENDER_TARGETS_RESET) {
            ++target_resets; last_event = "targets_reset";
        } else if (type == SDL_EVENT_DID_ENTER_FOREGROUND) {
            ++foregrounds; last_event = "foreground";
        } else return false;
        screen.invalidate(); movie.invalidate();
        return true;
    }
    void reupload() { screen.changed(); movie.changed(); }
    void redraw() { has_frame_ = false; }
    bool draw(SDL_Renderer* renderer, FrameView scene, FrameView video, bool show_scene,
              bool show_movie, const SDL_FRect& movie_rect) {
        ++attempts;
        const auto screen_uploads=screen.uploads,movie_uploads=movie.uploads;
        std::string error;
        auto failed = [&] {
            ++failures;
            last_error = error;
            screen.invalidate(); movie.invalidate();
            return false;
        };
        auto check = [&](bool ok, const char* operation) {
            if (!ok) error = std::string(operation) + ": " + SDL_GetError();
            return ok;
        };
        if (show_scene && scene.available()
            && !screen.upload(renderer, scene.width, scene.height, *scene.rgba, "screen", error))
            return failed();
        if (show_movie && video.available()
            && !movie.upload(renderer, video.width, video.height, *video.rgba, "movie", error))
            return failed();
        const bool same_layout=has_frame_&&show_scene_==show_scene&&show_movie_==show_movie
            &&scene_available_==scene.available()&&video_available_==video.available()
            &&(!show_movie||(movie_rect_.x==movie_rect.x&&movie_rect_.y==movie_rect.y
                &&movie_rect_.w==movie_rect.w&&movie_rect_.h==movie_rect.h));
        if(same_layout&&screen_uploads==screen.uploads&&movie_uploads==movie.uploads)
            return true;
        if (!check(SDL_SetRenderDrawColor(renderer, 0, 0, 0, 255), "SDL_SetRenderDrawColor")
            || !check(SDL_RenderClear(renderer), "SDL_RenderClear")) return failed();
        if (show_scene && scene.available()
            && !check(SDL_RenderTexture(renderer, screen.get(), nullptr, nullptr), "SDL_RenderTexture(screen)"))
            return failed();
        if (show_movie && video.available()
            && !check(SDL_RenderTexture(renderer, movie.get(), nullptr, &movie_rect), "SDL_RenderTexture(movie)"))
            return failed();
        // Do not publish a cleared/partial frame after any preceding failure.
        if (!check(SDL_RenderPresent(renderer), "SDL_RenderPresent")) return failed();
        has_frame_=true;show_scene_=show_scene;show_movie_=show_movie;
        scene_available_=scene.available();video_available_=video.available();movie_rect_=movie_rect;
        ++presented;
        return true;
    }
    void clear() {
        screen.clear(); movie.clear();
        has_frame_=false;
        attempts = presented = failures = device_resets = target_resets = foregrounds = 0;
        last_error.clear(); last_event.clear();
    }
};

// Full CPU-frame statistics, collected on demand/at the existing 1.5s status cadence.
// A non-black CPU frame and successful Present do not prove Android Surface visibility.
inline std::string pixel_stats(FrameView frame) {
    if (!frame.available()) return "{\"available\":false}";
    validate_rgba(frame.width, frame.height, *frame.rgba);
    const auto& bytes = *frame.rgba;
    uint64_t hash = 14695981039346656037ull;
    uint64_t nonblack = 0, visible_nonblack = 0, alpha_zero = 0, alpha_opaque = 0;
    for (size_t i = 0; i < bytes.size(); i += 4) {
        bool color = bytes[i] || bytes[i + 1] || bytes[i + 2];
        nonblack += color;
        visible_nonblack += color && bytes[i + 3];
        alpha_zero += bytes[i + 3] == 0;
        alpha_opaque += bytes[i + 3] == 255;
        for (size_t channel = 0; channel < 4; ++channel) {
            hash ^= bytes[i + channel]; hash *= 1099511628211ull;
        }
    }
    std::ostringstream out;
    out << "{\"available\":true,\"width\":" << frame.width << ",\"height\":" << frame.height
        << ",\"bytes\":" << bytes.size() << ",\"nonblack_pixels\":" << nonblack
        << ",\"visible_nonblack_pixels\":" << visible_nonblack
        << ",\"alpha_zero_pixels\":" << alpha_zero << ",\"alpha_opaque_pixels\":" << alpha_opaque
        << ",\"fnv1a64\":\"" << std::hex << std::setfill('0') << std::setw(16) << hash << "\"}";
    return out.str();
}
} // namespace majiro::android_host
