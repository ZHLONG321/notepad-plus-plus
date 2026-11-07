#pragma once

#include "StartupTrace.h"
#include <cstring>

// First-chance evidence only. Never handle, recover, or suppress an exception.
namespace StartupExceptionTrace
{
static HANDLE output = INVALID_HANDLE_VALUE;
static thread_local bool recording = false;

inline void writeRow(DWORD code, const char* kind, unsigned index,
    DWORD64 ip, DWORD64 sp, DWORD64 value)
{
    char line[256] = {};
    int size = snprintf(line, sizeof(line), "%08lX,%lu,%s,%u,%016llX,%016llX,%016llX\n",
        code, GetCurrentThreadId(), kind, index, ip, sp, value);
    if (size > 0 && size < static_cast<int>(sizeof(line)))
    {
        DWORD written;
        WriteFile(output, line, static_cast<DWORD>(size), &written, nullptr);
    }
}

inline LONG CALLBACK observe(EXCEPTION_POINTERS* exception)
{
    const DWORD code = exception->ExceptionRecord->ExceptionCode;
    if (recording || (code != EXCEPTION_ACCESS_VIOLATION && code != EXCEPTION_STACK_OVERFLOW))
        return EXCEPTION_CONTINUE_SEARCH;
    const DWORD savedError = GetLastError();
    recording = true;
    // A corrupt stack or unwind record must not turn diagnosis into recovery.
    __try
    {
#ifdef _WIN64
        CONTEXT context = *exception->ContextRecord;
        LARGE_INTEGER counter;
        QueryPerformanceCounter(&counter);
        writeRow(code, "qpc", 0, context.Rip, context.Rsp, counter.QuadPart);
        writeRow(code, "exception", 0, context.Rip, context.Rsp,
            exception->ExceptionRecord->ExceptionFlags);
        for (DWORD i = 0; i < exception->ExceptionRecord->NumberParameters; ++i)
            writeRow(code, "parameter", i, context.Rip, context.Rsp,
                exception->ExceptionRecord->ExceptionInformation[i]);
        for (unsigned frame = 0; frame < 64 && context.Rip; ++frame)
        {
            DWORD64 imageBase = 0;
            PRUNTIME_FUNCTION function = RtlLookupFunctionEntry(context.Rip, &imageBase, nullptr);
            writeRow(code, "frame", frame, context.Rip, context.Rsp, imageBase);
            const DWORD64 priorSp = context.Rsp;
            if (function)
            {
                PVOID handlerData = nullptr;
                DWORD64 establisherFrame = 0;
                RtlVirtualUnwind(UNW_FLAG_NHANDLER, imageBase, context.Rip, function,
                    &context, &handlerData, &establisherFrame, nullptr);
            }
            else
            {
                context.Rip = *reinterpret_cast<DWORD64*>(context.Rsp);
                context.Rsp += sizeof(DWORD64);
            }
            if (context.Rsp <= priorSp)
                break;
        }
#else
        writeRow(code, "exception", 0, exception->ContextRecord->Eip,
            exception->ContextRecord->Esp, exception->ExceptionRecord->ExceptionFlags);
#endif
    }
    __except (EXCEPTION_EXECUTE_HANDLER)
    {
        writeRow(code, "unwind.failed", 0, 0, 0, 0);
    }
    recording = false;
    SetLastError(savedError);
    return EXCEPTION_CONTINUE_SEARCH;
}

inline void install()
{
    if (startupTraceHandle() == INVALID_HANDLE_VALUE)
        return;
    wchar_t path[32768] = {};
    DWORD length = GetEnvironmentVariableW(L"NPP_STARTUP_TRACE", path, 32768);
    const wchar_t suffix[] = L".exceptions.tsv";
    if (!length || length + sizeof(suffix) / sizeof(wchar_t) > 32768)
        return;
    memcpy(path + length, suffix, sizeof(suffix));
    output = CreateFileW(path, GENERIC_WRITE, FILE_SHARE_READ, nullptr, CREATE_ALWAYS,
        FILE_ATTRIBUTE_NORMAL, nullptr);
    if (output == INVALID_HANDLE_VALUE)
    {
        startupTrace("exceptionTrace.open.failed");
        return;
    }
    using StackGuarantee = BOOL(WINAPI*)(PULONG);
    auto guarantee = reinterpret_cast<StackGuarantee>(
        GetProcAddress(GetModuleHandleW(L"kernel32.dll"), "SetThreadStackGuarantee"));
    ULONG stackBytes = 64 * 1024;
    startupTrace(guarantee && guarantee(&stackBytes)
        ? "exceptionTrace.stack.ready" : "exceptionTrace.stack.unavailable");
    startupTrace(AddVectoredExceptionHandler(1, observe)
        ? "exceptionTrace.ready" : "exceptionTrace.install.failed");
}
}
