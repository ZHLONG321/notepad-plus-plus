#pragma once

#include <windows.h>
#include <cstdio>

// Opt-in diagnostics: absent NPP_STARTUP_TRACE means no file and no timing calls.
inline HANDLE startupTraceHandle()
{
    static HANDLE handle = []() {
        wchar_t path[32768] = {};
        DWORD size = GetEnvironmentVariableW(L"NPP_STARTUP_TRACE", path, 32768);
        if (!size || size >= 32768)
            return INVALID_HANDLE_VALUE;
        return CreateFileW(path, GENERIC_WRITE, FILE_SHARE_READ, nullptr, CREATE_ALWAYS,
            FILE_ATTRIBUTE_NORMAL, nullptr);
    }();
    return handle;
}

inline void startupTrace(const char* event, const wchar_t* detail = L"")
{
    HANDLE handle = startupTraceHandle();
    if (handle == INVALID_HANDLE_VALUE)
        return;
    LARGE_INTEGER counter, frequency;
    QueryPerformanceCounter(&counter);
    QueryPerformanceFrequency(&frequency);
    char text[1024] = {};
    WideCharToMultiByte(CP_UTF8, 0, detail, -1, text, sizeof(text), nullptr, nullptr);
    FILETIME created, exited, kernel, user;
    GetThreadTimes(GetCurrentThread(), &created, &exited, &kernel, &user);
    ULARGE_INTEGER kt, ut;
    kt.LowPart = kernel.dwLowDateTime; kt.HighPart = kernel.dwHighDateTime;
    ut.LowPart = user.dwLowDateTime; ut.HighPart = user.dwHighDateTime;
    ULONG64 cycles = 0;
    QueryThreadCycleTime(GetCurrentThread(), &cycles);
    char line[1400] = {};
    int size = snprintf(line, sizeof(line), "%lld,%lld,%lu,%s,%s,%llu,%llu,%llu\n",
        counter.QuadPart, frequency.QuadPart, GetCurrentThreadId(), event, text, kt.QuadPart, ut.QuadPart, cycles);
    if (size > 0 && size < static_cast<int>(sizeof(line)))
    {
        DWORD written;
        WriteFile(handle, line, static_cast<DWORD>(size), &written, nullptr);
    }
}

struct StartupTraceScope
{
    const wchar_t* detail;
    explicit StartupTraceScope(const wchar_t* value) : detail(value) { startupTrace("begin", detail); }
    ~StartupTraceScope() { startupTrace("end", detail); }
};
