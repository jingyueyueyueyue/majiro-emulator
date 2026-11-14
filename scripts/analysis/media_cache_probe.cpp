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
    auto valid = [&] { return majiro::android_host::cached_media_file_valid(file.string(), 4); };
    if (valid()) throw std::runtime_error("missing file accepted");
    std::ofstream(file, std::ios::binary).write("ogg!", 4);
    if (!valid()) throw std::runtime_error("healthy file rejected");
    fs::resize_file(file, 2);
    if (valid()) throw std::runtime_error("truncated file accepted");
    fs::remove(file);
    fs::create_directory(file);
    if (valid()) throw std::runtime_error("directory accepted");
    fs::remove(file);
    std::ofstream(file, std::ios::binary).write("ogg!", 4);
    if (!valid()) throw std::runtime_error("rebuilt file rejected");
    fs::remove(file);
    std::cout << "{\"status\":\"passed\",\"checks\":5}";
}
