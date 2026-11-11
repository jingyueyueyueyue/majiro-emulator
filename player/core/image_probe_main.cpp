#include "majiro_core.hpp"
#include "majiro_image.hpp"
#include <filesystem>
#include <fstream>
#include <iostream>
#include <map>
#include <set>
#include <stdexcept>

static std::string key_for(std::string name) {
    // Preserve CP932 lead/trail pairs: a trail byte may look like ASCII B
    // or a backslash, so byte-wise lowercasing corrupts Japanese names.
    for (size_t i = 0; i < name.size(); ++i) {
        auto b = static_cast<uint8_t>(name[i]);
        if ((b >= 0x81 && b <= 0x9F) || (b >= 0xE0 && b <= 0xFC)) {
            if (i + 1 < name.size()) ++i;
        } else if (b >= 'A' && b <= 'Z') name[i] = char(b - 'A' + 'a');
        else if (b == '\\') name[i] = '/';
    }
    return name;
}

static int inspect(const std::filesystem::path& path, const std::filesystem::path& output,
                   const std::string& key_hex) {
    std::ifstream input(path, std::ios::binary);
    if (!input) throw std::runtime_error("cannot open image fixture archive");
    auto size = std::filesystem::file_size(path);
    majiro::ReadAt read = [&](uint64_t at, uint8_t* bytes, size_t count) {
        input.clear(); input.seekg(static_cast<std::streamoff>(at));
        input.read(reinterpret_cast<char*>(bytes), static_cast<std::streamsize>(count));
        if (!input || input.gcount() != static_cast<std::streamsize>(count))
            throw std::runtime_error("truncated fixture archive");
    };
    auto archive = majiro::read_archive(read, size);
    if (archive.members.empty()) throw std::runtime_error("empty image archive");
    std::map<std::string, const majiro::Member*> names;
    for (const auto& member : archive.members) names[key_for(member.raw_name)] = &member;
    std::vector<uint8_t> password;
    if (key_hex != "-") {
        if (key_hex.size() % 2) throw std::runtime_error("invalid password hex");
        for (size_t i = 0; i < key_hex.size(); i += 2)
            password.push_back(static_cast<uint8_t>(std::stoul(key_hex.substr(i, 2), nullptr, 16)));
    }
    std::set<std::string> active;
    std::function<majiro::Image(const std::string&)> pixels = [&](const std::string& name) {
        auto key = key_for(name);
        if (active.size() >= 12 || !active.insert(key).second)
            throw std::runtime_error("base image cycle or depth");
        auto item = names.find(key);
        if (item == names.end()) item = names.find(key + ".rct");
        if (item == names.end()) item = names.find(key + ".rc8");
        if (item == names.end()) throw std::runtime_error("base image not in fixture");
        const auto& member = *item->second;
        if (member.size > 64u * 1024 * 1024) throw std::runtime_error("image read limit");
        std::vector<uint8_t> data(member.size);
        read(member.offset, data.data(), data.size());
        auto image = majiro::read_image(data, key_hex == "-" ? nullptr : &password);
        if (!image.base_name.empty()) majiro::combine_base(image, pixels(image.base_name));
        active.erase(key);
        return image;
    };
    auto first = archive.members.front().raw_name;
    auto image = pixels(first);
    if (first.size() > 4 && first.substr(first.size() - 4) == ".rct") {
        auto mask = first.substr(0, first.size() - 4) + "_.rc8";
        if (names.count(mask)) majiro::apply_mask(image, pixels(mask));
    }
    std::ofstream file(output, std::ios::binary);
    file.write(reinterpret_cast<const char*>(image.rgba.data()),
               static_cast<std::streamsize>(image.rgba.size()));
    if (!file) throw std::runtime_error("cannot write image probe result");
    std::cout << "{\"status\":\"passed\",\"width\":" << image.width
              << ",\"height\":" << image.height << "}" << std::endl;
    return 0;
}

#ifdef _WIN32
int wmain(int argc, wchar_t** argv) {
#else
int main(int argc, char** argv) {
#endif
    try {
        if (argc != 4) throw std::runtime_error("usage: majiro_image_probe <archive> <rgba-output> <key-hex|->");
        auto hex_path = std::filesystem::path(argv[3]).u8string();
        std::string hex(reinterpret_cast<const char*>(hex_path.data()), hex_path.size());
        return inspect(std::filesystem::path(argv[1]), std::filesystem::path(argv[2]), hex);
    } catch (const std::exception& error) {
        std::cerr << error.what() << std::endl;
        return 1;
    }
}
