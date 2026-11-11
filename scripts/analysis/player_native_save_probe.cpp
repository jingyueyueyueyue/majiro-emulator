// Standalone format probe: no game launch, SDL host, JNI or save writes.
#include "../../player/core/majiro_save.hpp"
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <map>
#include <stdexcept>

namespace {
void verify_aliases(const majiro::NativeSystemSave& save) {
    std::map<uint32_t, const majiro::NativeSaveObject*> seen;
    const auto visit = [&](const auto& self, const std::shared_ptr<majiro::NativeSaveObject>& object) -> void {
        if (!object) return;
        auto [at, inserted] = seen.emplace(object->offset, object.get());
        if (!inserted) {
            if (at->second != object.get()) throw std::runtime_error("serialized object sharing lost");
            return;
        }
        for (const auto& child : object->strings) self(self, child);
    };
    for (const auto& value : save.variables) visit(visit, value.object);
}
}

int wmain(int argc, wchar_t** argv) {
    try {
        if (argc == 2 && std::wstring(argv[1]) == L"--limits") {
            bool called = false, rejected = false;
            majiro::ReadAt read = [&](uint64_t, uint8_t*, size_t) { called = true; };
            try { majiro::read_native_system_save(read, 64 * 1024 * 1024ULL + 1); }
            catch (const std::exception&) { rejected = true; }
            if (!rejected || called) throw std::runtime_error("oversized input read before size validation");
            std::cout << "{\"oversized_input_rejected_before_read\":true}\n";
            return 0;
        }
        if (argc != 4) throw std::runtime_error("usage: probe system|readmarks memory|readat input");
        const auto path = std::filesystem::path(argv[3]);
        uint64_t bytes = std::filesystem::file_size(path);
        if (bytes > 64 * 1024 * 1024ULL) throw std::runtime_error("probe file exceeds size bound");
        std::ifstream file(path, std::ios::binary);
        if (!file) throw std::runtime_error("cannot open probe input");
        majiro::ReadAt read = [&](uint64_t offset, uint8_t* output, size_t size) {
            if (offset > bytes || size > bytes - offset) throw std::runtime_error("read outside probe input");
            file.seekg(static_cast<std::streamoff>(offset));
            file.read(reinterpret_cast<char*>(output), static_cast<std::streamsize>(size));
            if (static_cast<size_t>(file.gcount()) != size) throw std::runtime_error("short probe input read");
        };
        std::vector<uint8_t> data;
        if (std::wstring(argv[2]) == L"memory") {
            data.resize(static_cast<size_t>(bytes));
            if (bytes) read(0, data.data(), data.size());
        } else if (std::wstring(argv[2]) != L"readat") throw std::runtime_error("unknown probe read mode");
        bool memory = std::wstring(argv[2]) == L"memory";
        if (std::wstring(argv[1]) == L"system") {
            auto result = memory ? majiro::read_native_system_save(data) :
                                   majiro::read_native_system_save(read, bytes);
            verify_aliases(result);
            std::cout << majiro::native_system_save_json(result) << '\n';
        } else if (std::wstring(argv[1]) == L"readmarks") {
            auto result = memory ? majiro::read_native_readmarks(data) :
                                   majiro::read_native_readmarks(read, bytes);
            std::cout << majiro::native_readmarks_json(result) << '\n';
        } else throw std::runtime_error("unknown probe format");
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
