#pragma once
#include <windows.h>

// Optimize eligible windows of an already active IME inside this process only.
class StartupImeWindows final {
public:
    StartupImeWindows();
    ~StartupImeWindows();
    StartupImeWindows(const StartupImeWindows&) = delete;
    StartupImeWindows& operator=(const StartupImeWindows&) = delete;
private:
    HHOOK _hook = nullptr;
};
