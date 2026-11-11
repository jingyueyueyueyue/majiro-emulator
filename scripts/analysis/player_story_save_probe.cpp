// Format-only native story probe. No VM import or game launch.
#include "../../player/core/majiro_story_save.hpp"
#include <filesystem>
#include <fstream>
#include <iostream>
#include <map>
#include <stdexcept>

int wmain(int argc, wchar_t** argv) {
    try {
        if (argc == 2 && std::wstring(argv[1]) == L"--limits") {
            bool called = false, rejected = false;
            majiro::ReadAt read = [&](uint64_t, uint8_t*, size_t) { called = true; };
            try { majiro::read_native_story_save(read, 64 * 1024 * 1024ULL + 1); }
            catch (const std::exception&) { rejected = true; }
            if (!rejected || called) throw std::runtime_error("oversized story read before validation");
            std::cout << "{\"oversized_input_rejected_before_read\":true}\n";
            return 0;
        }
        if (argc != 3) throw std::runtime_error("usage: probe memory|readat input");
        const auto path = std::filesystem::path(argv[2]);
        uint64_t bytes = std::filesystem::file_size(path);
        if (bytes > 64 * 1024 * 1024ULL) throw std::runtime_error("story probe input too large");
        std::ifstream file(path, std::ios::binary);
        if (!file) throw std::runtime_error("cannot open story probe input");
        majiro::ReadAt read = [&](uint64_t at, uint8_t* output, size_t count) {
            if (at > bytes || count > bytes - at) throw std::runtime_error("story probe read outside input");
            file.seekg(static_cast<std::streamoff>(at));
            file.read(reinterpret_cast<char*>(output), static_cast<std::streamsize>(count));
            if (static_cast<size_t>(file.gcount()) != count) throw std::runtime_error("short story probe read");
        };
        majiro::NativeStorySave result;
        if (std::wstring(argv[1]) == L"memory") {
            std::vector<uint8_t> data(static_cast<size_t>(bytes));
            if (bytes) read(0, data.data(), data.size());
            result = majiro::read_native_story_save(data);
        } else if (std::wstring(argv[1]) == L"readat") result = majiro::read_native_story_save(read, bytes);
        else throw std::runtime_error("unknown story probe read mode");
        std::map<uint32_t, const majiro::NativeSaveObject*> objects;
        const auto verify = [&](const auto& self, const std::shared_ptr<majiro::NativeSaveObject>& object) -> void {
            if (!object) return;
            auto [at, inserted] = objects.emplace(object->offset, object.get());
            if (!inserted) {
                if (at->second != object.get()) throw std::runtime_error("cross-group object sharing lost");
                return;
            }
            for (const auto& child : object->strings) self(self, child);
        };
        for (const auto& group : result.groups)
            for (const auto& value : group.values) verify(verify, value.object);
        std::cout << majiro::native_story_save_json(result) << '\n';
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
