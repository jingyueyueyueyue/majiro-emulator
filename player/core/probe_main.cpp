#include "majiro_core.hpp"
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>

static int inspect(const std::filesystem::path& path) {
    std::ifstream input(path, std::ios::binary);
    if (!input) throw std::runtime_error("cannot open ARC");
    const auto size = std::filesystem::file_size(path);
    majiro::ReadAt read = [&](uint64_t offset, uint8_t* output, size_t count) {
        input.clear();
        input.seekg(static_cast<std::streamoff>(offset));
        input.read(reinterpret_cast<char*>(output), static_cast<std::streamsize>(count));
        if (!input || input.gcount() != static_cast<std::streamsize>(count))
            throw std::runtime_error("short ARC read");
    };
    auto report = majiro::inspect_archive(read, size, true);
    std::cout << report << std::endl;
    return report.find("\"status\":\"passed\"") == std::string::npos;
}
#ifdef _WIN32
int wmain(int argc, wchar_t** argv) {
#else
int main(int argc, char** argv) {
#endif
    try {
        if (argc != 2) throw std::runtime_error("usage: majiro_probe <archive>");
        return inspect(std::filesystem::path(argv[1]));
    } catch (const std::exception& error) {
        std::cerr << error.what() << std::endl;
        return 2;
    }
}
