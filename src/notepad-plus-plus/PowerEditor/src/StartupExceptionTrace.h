#pragma once

#include "StartupTrace.h"
#include <cstring>

// First-chance evidence only. Never handle, recover, or suppress an exception.
namespace StartupExceptionTrace
{
static HANDLE output = INVALID_HANDLE_VALUE;
static thread_local bool recording = false;

// Documented prefix of an unload event. The actual element stride is supplied
// by ntdll and can exceed sizeof(UnloadEvent). Resolve before an exception.
struct UnloadEvent
{
    PVOID base;
    SIZE_T size;
    ULONG sequence;
    ULONG timestamp;
    ULONG checksum;
    WCHAR name[32];
};
using GetUnloadEventsEx = VOID (WINAPI*)(PULONG*, PULONG*, PVOID*);
static PULONG unloadElementSize = nullptr;
static PULONG unloadElementCount = nullptr;
// Ex returns the address of the loader's array-pointer variable. Its value can
// change when the loader first allocates the dynamic event array.
static PVOID unloadEventsLocation = nullptr;

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
        const DWORD64 registers[] = { context.Rax, context.Rcx, context.Rdx,
            context.R8, context.R9, context.Rbx, context.Rdi };
        const char* registerNames[] = { "register.rax", "register.rcx", "register.rdx",
            "register.r8", "register.r9", "register.rbx", "register.rdi" };
        for (unsigned i = 0; i < sizeof(registers) / sizeof(registers[0]); ++i)
            writeRow(code, registerNames[i], i, context.Rip, context.Rsp, registers[i]);
        for (DWORD i = 0; i < exception->ExceptionRecord->NumberParameters; ++i)
            writeRow(code, "parameter", i, context.Rip, context.Rsp,
                exception->ExceptionRecord->ExceptionInformation[i]);
        MEMORY_BASIC_INFORMATION memory = {};
        if (VirtualQuery(reinterpret_cast<LPCVOID>(context.Rip), &memory, sizeof(memory)))
        {
            writeRow(code, "memory.base", 0, context.Rip, context.Rsp,
                reinterpret_cast<DWORD64>(memory.AllocationBase));
            writeRow(code, "memory.state", 0, context.Rip, context.Rsp, memory.State);
            writeRow(code, "memory.protect", 0, context.Rip, context.Rsp, memory.Protect);
            writeRow(code, "memory.type", 0, context.Rip, context.Rsp, memory.Type);
        }
        const ULONG stride = unloadElementSize ? *unloadElementSize : 0;
        const ULONG count = unloadElementCount ? *unloadElementCount : 0;
        const PVOID events = unloadEventsLocation ?
            *static_cast<PVOID*>(unloadEventsLocation) : nullptr;
        for (ULONG i = 0; events && stride >= sizeof(UnloadEvent) && i < count; ++i)
        {
            UnloadEvent image = {};
            memcpy(&image, static_cast<const BYTE*>(events) +
                static_cast<SIZE_T>(i) * stride, sizeof(image));
            const DWORD64 base = reinterpret_cast<DWORD64>(image.base);
            if (!base || context.Rip < base || context.Rip - base >= image.size)
                continue;
            writeRow(code, "unloaded.base", image.sequence, context.Rip, context.Rsp, base);
            writeRow(code, "unloaded.size", image.sequence, context.Rip, context.Rsp, image.size);
            writeRow(code, "unloaded.rva", image.sequence, context.Rip, context.Rsp, context.Rip - base);
            wchar_t name[33] = {};
            memcpy(name, image.name, sizeof(image.name));
            char utf8[160] = {};
            WideCharToMultiByte(CP_UTF8, 0, name, -1, utf8, sizeof(utf8), nullptr, nullptr);
            char line[320] = {};
            int size = snprintf(line, sizeof(line), "%08lX,%lu,unloaded.name,%lu,%016llX,%016llX,%s\n",
                code, GetCurrentThreadId(), image.sequence, context.Rip, context.Rsp, utf8);
            if (size > 0 && size < static_cast<int>(sizeof(line)))
            {
                DWORD written;
                WriteFile(output, line, static_cast<DWORD>(size), &written, nullptr);
            }
        }
        // A null indirect call still leaves its caller's return address on the
        // stack. Include that first frame before applying the unwind terminator.
        for (unsigned frame = 0; frame < 64 && (frame == 0 || context.Rip); ++frame)
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
    auto getUnloadEvents = reinterpret_cast<GetUnloadEventsEx>(
        GetProcAddress(GetModuleHandleW(L"ntdll.dll"), "RtlGetUnloadEventTraceEx"));
    if (getUnloadEvents)
        getUnloadEvents(&unloadElementSize, &unloadElementCount, &unloadEventsLocation);
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
