#include "../core/majiro_core.hpp"
#include "../core/majiro_image.hpp"
#include "../core/majiro_save.hpp"
#include "../core/majiro_story_save.hpp"
#include <jni.h>
#include <cerrno>
#include <cstdint>
#include <stdexcept>
#include <unistd.h>

extern "C" JNIEXPORT jstring JNICALL
Java_org_majiro_player_NativeCore_inspectArchive(JNIEnv* env, jclass, jint descriptor, jlong size) {
    if (descriptor < 0 || size < 0)
        return env->NewStringUTF("{\"status\":\"failed\",\"error\":\"unseekable archive\"}");
    majiro::ReadAt read = [descriptor](uint64_t at, uint8_t* data, size_t count) {
        while (count) {
            ssize_t got = pread(descriptor, data, count, static_cast<off_t>(at));
            if (got < 0 && errno == EINTR) continue;
            if (got <= 0) throw std::runtime_error("archive provider cannot read at requested offset");
            at += static_cast<uint64_t>(got);
            data += got;
            count -= static_cast<size_t>(got);
        }
    };
    auto result = majiro::inspect_archive(read, static_cast<uint64_t>(size), true);
    return env->NewStringUTF(result.c_str());
}

extern "C" JNIEXPORT jstring JNICALL
Java_org_majiro_player_NativeCore_inspectImage(JNIEnv* env, jclass, jbyteArray source, jbyteArray key) {
    if (source == nullptr || env->GetArrayLength(source) > 64 * 1024 * 1024)
        return env->NewStringUTF("{\"status\":\"failed\",\"error\":\"image read limit\"}");
    std::vector<uint8_t> data(static_cast<size_t>(env->GetArrayLength(source)));
    env->GetByteArrayRegion(source, 0, static_cast<jsize>(data.size()),
                           reinterpret_cast<jbyte*>(data.data()));
    std::vector<uint8_t> password;
    if (key != nullptr) {
        password.resize(static_cast<size_t>(env->GetArrayLength(key)));
        env->GetByteArrayRegion(key, 0, static_cast<jsize>(password.size()),
                               reinterpret_cast<jbyte*>(password.data()));
    }
    if (env->ExceptionCheck()) return nullptr;
    auto result = majiro::inspect_image(data, key == nullptr ? nullptr : &password);
    return env->NewStringUTF(result.c_str());
}

extern "C" JNIEXPORT jstring JNICALL
Java_org_majiro_player_NativeCore_inspectNativeSave(JNIEnv* env, jclass, jint descriptor,
                                                  jlong size, jboolean readmarks) {
    try {
        if (descriptor < 0 || size < 0) throw std::runtime_error("unseekable native save");
        majiro::ReadAt read = [descriptor](uint64_t at, uint8_t* data, size_t count) {
            while (count) {
                ssize_t got = pread(descriptor, data, count, static_cast<off_t>(at));
                if (got < 0 && errno == EINTR) continue;
                if (got <= 0) throw std::runtime_error("native save provider cannot read requested offset");
                at += static_cast<uint64_t>(got); data += got; count -= static_cast<size_t>(got);
            }
        };
        std::string report;
        if (readmarks) {
            report=majiro::native_readmarks_json(majiro::read_native_readmarks(read, static_cast<uint64_t>(size)));
        } else {
            std::vector<uint8_t> signature(size>=16?16:0);
            if (!signature.empty()) read(0,signature.data(),signature.size());
            auto kind=majiro::identify_native_save(signature);
            report=(kind==majiro::NativeSaveKind::story_103||kind==majiro::NativeSaveKind::story_105)
                ? majiro::native_story_save_json(majiro::read_native_story_save(read,static_cast<uint64_t>(size)))
                : majiro::native_system_save_json(majiro::read_native_system_save(read,static_cast<uint64_t>(size)));
        }
        report.insert(1, "\"status\":\"passed\",");
        // JSON exports contain only ASCII, including raw game-string bytes.
        return env->NewStringUTF(report.c_str());
    } catch (const std::exception& error) {
        // Diagnostics are a fixed failure object; original save bytes and
        // format exceptions never become executable Java/JSON content.
        return env->NewStringUTF("{\"status\":\"failed\",\"error\":\"native save format rejected\"}");
    }
}
