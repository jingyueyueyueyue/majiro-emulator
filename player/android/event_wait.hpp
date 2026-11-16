#pragma once
#include <SDL3/SDL.h>
#include <stdexcept>

namespace majiro::android_host {
// SDL 3.4.20's Android wait pumps a poll sentinel that wakes its own lifecycle
// queue. Wait on actual queued events instead; leave consumption to PollEvent.
class EventWait {
    SDL_Semaphore* wake_;
    static bool SDLCALL observe(void* owner, SDL_Event* event) {
        if (event->type != SDL_EVENT_POLL_SENTINEL)
            SDL_SignalSemaphore(static_cast<EventWait*>(owner)->wake_);
        return true;
    }
public:
    EventWait() : wake_(SDL_CreateSemaphore(0)) {
        if (!wake_) throw std::runtime_error("event wake semaphore unavailable");
        if (!SDL_AddEventWatch(observe, this)) {
            SDL_DestroySemaphore(wake_);
            throw std::runtime_error("event wake observer unavailable");
        }
    }
    EventWait(const EventWait&) = delete;
    EventWait& operator=(const EventWait&) = delete;
    ~EventWait() {
        SDL_RemoveEventWatch(observe, this);
        SDL_DestroySemaphore(wake_);
    }
    void before_poll() {
        while (SDL_TryWaitSemaphore(wake_)) {}
    }
    void wait(int milliseconds) {
        // Raw Android lifecycle notifications and Java host commands are
        // translated during polling, so keep their maximum latency bounded.
        SDL_WaitSemaphoreTimeout(wake_, milliseconds < 16 ? milliseconds : 16);
    }
};
}
