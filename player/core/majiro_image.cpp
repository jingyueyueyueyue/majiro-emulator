// Portable RCT/RC8 decoding. Format reference: GARbro's Majiro ImageRCT/ImageRC8.
// Uses original byte strings for passwords and base names.
#include "majiro_image.hpp"
#include <algorithm>
#include <cstring>
#include <sstream>
#include <stdexcept>

namespace majiro {
namespace {
void need(bool ok, const char* why) { if (!ok) throw std::runtime_error(why); }
uint16_t word(const uint8_t* p) { return uint16_t(p[0]) | uint16_t(p[1]) << 8; }
uint32_t dword(const uint8_t* p) {
    return uint32_t(p[0]) | uint32_t(p[1]) << 8 | uint32_t(p[2]) << 16 | uint32_t(p[3]) << 24;
}
constexpr int rgb_shifts[] = {
    -16,-32,-48,-64,-80,-96,49,33,17,1,-15,-31,-47,50,34,18,2,-14,-30,-46,
    51,35,19,3,-13,-29,-45,36,20,4,-12,-28};
constexpr int indexed_shifts[] = {-16,-32,-48,-64,49,33,17,1,-15,-31,-47,34,18,2,-14,-30};
std::array<uint8_t, 1024> key_for(const std::vector<uint8_t>& password) {
    std::array<uint32_t, 256> table{};
    for (uint32_t i = 0; i < 256; ++i) {
        uint32_t value = i;
        for (int n = 0; n < 8; ++n) value = (value >> 1) ^ ((value & 1) ? 0xEDB88320u : 0);
        table[i] = value;
    }
    uint32_t crc = 0xFFFFFFFFu;
    for (auto b : password) crc = (crc >> 8) ^ table[(crc ^ b) & 255];
    crc ^= 0xFFFFFFFFu;
    std::array<uint8_t, 1024> key{};
    for (unsigned i = 0; i < 256; ++i) {
        uint32_t value = crc ^ table[(i + crc) & 255];
        for (unsigned j = 0; j < 4; ++j) key[i * 4 + j] = uint8_t(value >> (j * 8));
    }
    return key;
}
std::vector<uint8_t> decompress(const std::vector<uint8_t>& data, uint32_t w, uint32_t h,
                                unsigned pixel_bytes, bool encrypted) {
    size_t remaining = size_t(w) * h, pos = 0, cursor = 0, literal = 0;
    std::vector<uint8_t> output(remaining * pixel_bytes);
    const unsigned bits = pixel_bytes == 3 ? 2 : 3, mask = (1u << bits) - 1;
    auto read_word = [&]() {
        need(cursor + 2 <= data.size(), "truncated image run length");
        auto value = word(data.data() + cursor); cursor += 2; return value;
    };
    while (remaining) {
        size_t count = literal + 1;
        need(count <= remaining && cursor <= data.size()
             && count * pixel_bytes <= data.size() - cursor, "image literal exceeds bounds");
        std::memcpy(output.data() + pos, data.data() + cursor, count * pixel_bytes);
        pos += count * pixel_bytes; cursor += count * pixel_bytes; remaining -= count;
        while (remaining) {
            need(cursor < data.size(), "truncated image backreference");
            auto code = data[cursor++];
            if (!(code & 0x80)) {
                literal = code + (code == 0x7F ? read_word() : 0);
                break;
            }
            int shift = pixel_bytes == 3 ? rgb_shifts[(code >> bits) & 31]
                                        : indexed_shifts[(code >> bits) & 15];
            count = code & mask;
            if (count == mask) count += read_word();
            count += pixel_bytes == 3 ? 1 : 3;
            // Python uses floor shift for negative values; C++ signed shifts
            // are arithmetic in C++20, so the format's displacement agrees.
            int64_t displacement = (int64_t(shift >> 4) - int64_t(shift & 15) * w) * pixel_bytes;
            need(count <= remaining && displacement < 0
                 && int64_t(pos) + displacement >= 0, "image backreference exceeds bounds");
            for (size_t i = 0; i < count * pixel_bytes; ++i)
                output[pos + i] = output[static_cast<size_t>(int64_t(pos + i) + displacement)];
            pos += count * pixel_bytes; remaining -= count;
        }
    }
    need(cursor == data.size() || (encrypted && cursor + 2 == data.size()
                                  && data[cursor] == 'T' && data[cursor + 1] == 'S'),
         "unconsumed image payload");
    return output;
}
}

Image read_image(const std::vector<uint8_t>& data, const std::vector<uint8_t>* password) {
    constexpr uint8_t magic[] = {0x98, 0x5A, 0x92, 0x9A};
    need(data.size() >= 20 && std::memcmp(data.data(), magic, 4) == 0, "invalid Majiro image");
    Image image;
    image.width = dword(data.data() + 8); image.height = dword(data.data() + 12);
    size_t pixels = size_t(image.width) * image.height;
    need(image.width && image.height && image.width <= 32768 && image.height <= 32768
         && pixels <= 16u * 1024 * 1024, "image pixel memory limit exceeded");
    std::string variant(reinterpret_cast<const char*>(data.data() + 4), 4);
    image.rgba.resize(pixels * 4);
    if (variant == "8_00") {
        need(data.size() >= 20 + 768, "truncated RC8 palette");
        std::copy_n(data.begin() + 20, 768, image.palette.begin());
        image.indices = decompress(std::vector<uint8_t>(data.begin() + 788, data.end()),
                                   image.width, image.height, 1, false);
        for (size_t i = 0; i < pixels; ++i) {
            for (unsigned c = 0; c < 3; ++c)
                image.rgba[i * 4 + c] = image.palette[size_t(image.indices[i]) * 3 + c];
            image.rgba[i * 4 + 3] = 255;
        }
        return image;
    }
    bool encrypted = variant == "TS00" || variant == "TS01";
    bool base = variant == "TC01" || variant == "TS01";
    need(encrypted || variant == "TC00" || variant == "TC01", "unsupported RCT image variant");
    size_t offset = 20;
    if (base) {
        need(offset + 2 <= data.size(), "truncated base image name length");
        uint16_t count = word(data.data() + offset); offset += 2;
        need(count && count <= data.size() - offset && data[offset + count - 1] == 0,
             "invalid base image name");
        image.base_name.assign(reinterpret_cast<const char*>(data.data() + offset), count - 1);
        offset += count;
    }
    auto length = dword(data.data() + 16);
    need(offset <= data.size() && length == data.size() - offset, "RCT payload length mismatch");
    std::vector<uint8_t> payload(data.begin() + offset, data.end());
    if (encrypted) {
        need(password != nullptr, "encrypted RCT requires image password");
        auto key = key_for(*password);
        for (size_t i = 0; i < payload.size(); ++i) payload[i] ^= key[i & 1023];
    }
    auto bgr = decompress(payload, image.width, image.height, 3, encrypted);
    for (size_t i = 0; i < pixels; ++i) {
        image.rgba[i * 4] = bgr[i * 3 + 2];
        image.rgba[i * 4 + 1] = bgr[i * 3 + 1];
        image.rgba[i * 4 + 2] = bgr[i * 3];
        image.rgba[i * 4 + 3] = 255;
    }
    return image;
}

void combine_base(Image& overlay, const Image& base) {
    need(overlay.width == base.width && overlay.height == base.height, "base image dimensions mismatch");
    for (size_t i = 0; i < overlay.rgba.size(); i += 4)
        if (overlay.rgba[i] == 255 && overlay.rgba[i + 1] == 0 && overlay.rgba[i + 2] == 0)
            std::copy_n(base.rgba.begin() + i, 4, overlay.rgba.begin() + i);
}

void apply_mask(Image& color, const Image& mask) {
    need(color.width == mask.width && color.height == mask.height, "image mask dimensions mismatch");
    for (size_t i = 0; i < color.rgba.size(); i += 4)
        color.rgba[i + 3] = uint8_t(255 - (unsigned(mask.rgba[i]) + mask.rgba[i + 1] + mask.rgba[i + 2]) / 3);
}

Image transparency_mask(const Image& color) {
    const size_t pixels=size_t(color.width)*color.height;
    need(color.width&&color.height&&pixels<=16u*1024*1024&&color.rgba.size()==pixels*4,
         "invalid PNG transparency source");
    Image mask;mask.width=color.width;mask.height=color.height;
    mask.indices.resize(pixels);mask.rgba.resize(pixels*4);
    for(size_t i=0;i<256;++i)mask.palette[i*3]=mask.palette[i*3+1]=mask.palette[i*3+2]=uint8_t(i);
    for(size_t i=0;i<pixels;++i) {
        const auto value=uint8_t(255-color.rgba[i*4+3]);
        mask.indices[i]=value;
        mask.rgba[i*4]=mask.rgba[i*4+1]=mask.rgba[i*4+2]=value;mask.rgba[i*4+3]=255;
    }
    return mask;
}

std::string inspect_image(const std::vector<uint8_t>& data, const std::vector<uint8_t>* password) {
    try {
        auto image = read_image(data, password);
        std::string pixels;
        constexpr char hex[] = "0123456789abcdef";
        for (size_t i = 0; i < std::min<size_t>(image.rgba.size(), 32); ++i) {
            pixels += hex[image.rgba[i] >> 4]; pixels += hex[image.rgba[i] & 15];
        }
        std::ostringstream out;
        out << "{\"status\":\"passed\",\"width\":" << image.width << ",\"height\":" << image.height
            << ",\"pixel_prefix\":\"" << pixels << "\",\"requires_base\":"
            << (image.base_name.empty() ? "false" : "true") << "}";
        return out.str();
    } catch (const std::exception&) {
        return "{\"status\":\"failed\",\"error\":\"image decode failed\"}";
    }
}
}
