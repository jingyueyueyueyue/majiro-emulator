#include "../../player/android/media_cache.hpp"
#include <fstream>
#include <iostream>
#include <stdexcept>

int main(int argc, char** argv) {
    if (argc != 2) return 2;
    namespace fs = std::filesystem;
    auto directory = fs::path(argv[1]);
    fs::create_directories(directory);
    auto file = directory / "fixture.ogg";
    auto verify = [](const std::string& path, uint64_t) {
        std::ifstream input(path, std::ios::binary);
        std::string bytes((std::istreambuf_iterator<char>(input)), {});
        return !input.bad() && bytes == "ogg!";
    };
    auto valid = [&] {
        return majiro::android_host::cached_media_file_valid(file.string(), 4, verify);
    };
    if (valid()) throw std::runtime_error("missing file accepted");
    std::ofstream(file, std::ios::binary).write("ogg!", 4);
    if (!valid()) throw std::runtime_error("healthy file rejected");
    std::ofstream(file, std::ios::binary).write("bad!", 4);
    if (valid()) throw std::runtime_error("same-size corrupt file accepted");
    std::ofstream(file, std::ios::binary).write("ogg!", 4);
    fs::resize_file(file, 2);
    if (valid()) throw std::runtime_error("truncated file accepted");
    fs::remove(file);
    fs::create_directory(file);
    if (valid()) throw std::runtime_error("directory accepted");
    fs::remove(file);
    std::ofstream(file, std::ios::binary).write("ogg!", 4);
    if (!valid()) throw std::runtime_error("rebuilt file rejected");
    fs::remove(file);
    std::cout << "{\"status\":\"passed\",\"checks\":6}";
}
