#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include "StartupImeWindows.h"
#include "StartupTrace.h"
#include <cwchar>
#include <commctrl.h>
#include <atomic>
#include <cstring>
#include <new>

namespace {
struct Pending {
    HWND window, owner;
    DWORD extended;
    int x, y, width, height;
    Pending* next;
    bool restoring = false;
    bool destroyed = false;
};
thread_local Pending* pending = nullptr;
std::atomic<HMODULE> original_engine{nullptr};
UINT restore_message;
std::atomic<bool> enabled{false};
constexpr wchar_t pending_property[] = L"NotepadPlusPlus.PendingImePopup.93249AC7-8C55-4464-83B7-412D8A633C94";

Pending* detach(HWND window) {
    auto link = &pending;
    while (*link) {
        if ((*link)->window == window) {
            Pending* found = *link;
            *link = found->next;
            return found;
        }
        link = &(*link)->next;
    }
    return nullptr;
}

LRESULT CALLBACK pending_proc(HWND, UINT, WPARAM, LPARAM, UINT_PTR, DWORD_PTR);

bool restore_local(HWND window) {
    Pending* entry = pending;
    while (entry && entry->window != window) entry = entry->next;
    if (!entry) return true;
    if (entry->restoring) return true;
    // Synchronous notifications can reenter or destroy the window; retain this record until restoration returns.
    entry->restoring = true;
    const Pending saved = *entry;
    const bool success = [&] {
        SetLastError(0);
        if (!SetParent(window, nullptr) && GetLastError()) return false;
        if (entry->destroyed) return false;
        const LONG_PTR style = GetWindowLongPtrW(window, GWL_STYLE);
        SetLastError(0);
        if (!SetWindowLongPtrW(window, GWL_STYLE, (style & ~WS_CHILD) | WS_POPUP) && GetLastError()) return false;
        if (entry->destroyed) return false;
        SetLastError(0);
        if (!SetWindowLongPtrW(window, GWLP_HWNDPARENT, reinterpret_cast<LONG_PTR>(saved.owner)) && GetLastError()) return false;
        if (entry->destroyed) return false;
        return SetWindowPos(window, (saved.extended & WS_EX_TOPMOST) ? HWND_TOPMOST : HWND_NOTOPMOST,
            saved.x, saved.y, saved.width, saved.height, SWP_NOACTIVATE | SWP_FRAMECHANGED) != FALSE;
    }();
    const DWORD failure = entry->destroyed ? ERROR_INVALID_WINDOW_HANDLE : GetLastError();
    const bool complete = success && !entry->destroyed;
    entry->restoring = false;
    if (complete) {
        startupTrace("inputServices.imeWindows.restored");
        detach(window);
        RemovePropW(window, pending_property);
        RemoveWindowSubclass(window, pending_proc, 1);
    }
    if (complete || entry->destroyed) delete entry;
    if (!complete) SetLastError(failure ? failure : ERROR_GEN_FAILURE);
    return complete;
}

LRESULT CALLBACK pending_proc(HWND window, UINT message, WPARAM wp, LPARAM lp, UINT_PTR id, DWORD_PTR) {
    if (message == restore_message) return restore_local(window) ? 0 : GetLastError();
    if (message == WM_NCDESTROY) {
        RemovePropW(window, pending_property);
        Pending* entry = detach(window);
        if (entry) {
            entry->destroyed = true;
            if (!entry->restoring) delete entry;
        }
        RemoveWindowSubclass(window, pending_proc, id);
    }
    return DefSubclassProc(window, message, wp, lp);
}

bool restore(HWND window) {
    // Only pending windows require a synchronous call to their owning thread.
    if (!GetPropW(window, pending_property)) return true;
    DWORD process = 0;
    const DWORD thread = GetWindowThreadProcessId(window, &process);
    if (process != GetCurrentProcessId()) return true;
    if (thread == GetCurrentThreadId()) return restore_local(window);
    // Subclass operations and restoration must run on the window owner thread.
    const auto error = static_cast<DWORD>(SendMessageW(window, restore_message, 0, 0));
    if (error) SetLastError(error);
    return error == 0;
}

HWND WINAPI create_window(DWORD extended, LPCWSTR name, LPCWSTR title, DWORD style,
    int x, int y, int width, int height, HWND owner, HMENU menu, HINSTANCE instance, LPVOID param) {
    const DWORD saved_error = GetLastError();
    WNDCLASSEXW cls{sizeof(cls)};
    const bool stage = enabled.load(std::memory_order_acquire) && instance == original_engine.load(std::memory_order_acquire) && (style & WS_POPUP) && !(style & WS_VISIBLE) &&
        owner && !menu && GetWindowThreadProcessId(owner, nullptr) == GetCurrentThreadId() &&
        GetClassInfoExW(instance, name, &cls) && (cls.style & CS_IME);
    // For both popup and child windows, CW_USEDEFAULT zeros the corresponding pair.
    // Preserve creation arguments and restore the coordinates actually selected by Windows.
    Pending* entry = stage ? new (std::nothrow) Pending{nullptr, owner, extended,
        x == CW_USEDEFAULT ? 0 : x, x == CW_USEDEFAULT ? 0 : y,
        width == CW_USEDEFAULT ? 0 : width, width == CW_USEDEFAULT ? 0 : height, nullptr} : nullptr;
    SetLastError(saved_error);
    HWND window = CreateWindowExW(extended, name, title,
        entry ? ((style & ~WS_POPUP) | WS_CHILD) : style, x, y, width, height, owner, menu, instance, param);
    const DWORD result_error = GetLastError();
    if (entry && window) {
        startupTrace("inputServices.imeWindows.deferred", name && !IS_INTRESOURCE(name) ? name : L"atom");
        entry->window = window;
        entry->next = pending;
        pending = entry;
        if (!SetWindowSubclass(window, pending_proc, 1, 0) ||
            !SetPropW(window, pending_property, reinterpret_cast<HANDLE>(1))) {
            const DWORD failure = GetLastError();
            delete detach(window);
            // Without destruction notifications the record cannot safely outlive this call.
            DestroyWindow(window);
            SetLastError(failure ? failure : ERROR_GEN_FAILURE);
            return nullptr;
        }
    } else delete entry;
    SetLastError(result_error);
    return window;
}

BOOL WINAPI show_window(HWND window, int command) {
    const DWORD error = GetLastError();
    if (command != SW_HIDE && !restore(window)) return FALSE;
    SetLastError(error);
    return ShowWindow(window, command);
}
BOOL WINAPI position_window(HWND window, HWND after, int x, int y, int width, int height, UINT flags) {
    const DWORD error = GetLastError();
    if (!restore(window)) return FALSE;
    SetLastError(error);
    return SetWindowPos(window, after, x, y, width, height, flags);
}
BOOL WINAPI move_window(HWND window, int x, int y, int width, int height, BOOL repaint) {
    const DWORD error = GetLastError();
    if (!restore(window)) return FALSE;
    SetLastError(error);
    return MoveWindow(window, x, y, width, height, repaint);
}
BOOL WINAPI update_layered(HWND window, HDC dest, POINT* position, SIZE* size, HDC source,
    POINT* source_position, COLORREF key, BLENDFUNCTION* blend, DWORD flags) {
    const DWORD error = GetLastError();
    if (!restore(window)) return FALSE;
    SetLastError(error);
    return UpdateLayeredWindow(window, dest, position, size, source, source_position, key, blend, flags);
}
LONG_PTR WINAPI set_long(HWND window, int index, LONG_PTR value) {
    const DWORD error = GetLastError();
    if ((index == GWL_STYLE || index == GWL_EXSTYLE || index == GWLP_HWNDPARENT) && !restore(window)) return 0;
    SetLastError(error);
    return SetWindowLongPtrW(window, index, value);
}
LONG_PTR WINAPI get_long_ptr(HWND window, int index) {
    const DWORD error = GetLastError();
    // Only the ordinary style and parent relation change; extended styles remain readable.
    if ((index == GWL_STYLE || index == GWLP_HWNDPARENT) && !restore(window)) return 0;
    SetLastError(error);
    return GetWindowLongPtrW(window, index);
}
LONG WINAPI get_long(HWND window, int index) {
    const DWORD error = GetLastError();
    if (index == GWL_STYLE && !restore(window)) return 0;
    SetLastError(error);
    return GetWindowLongW(window, index);
}
BOOL WINAPI get_rect(HWND window, RECT* rectangle) {
    const DWORD error = GetLastError();
    if (!restore(window)) return FALSE;
    SetLastError(error);
    return GetWindowRect(window, rectangle);
}
int WINAPI map_points(HWND from, HWND to, POINT* points, UINT count) {
    const DWORD error = GetLastError();
    if (!restore(from) || !restore(to)) return 0;
    SetLastError(error);
    return MapWindowPoints(from, to, points, count);
}

struct Import {
    const char* name;
    ULONG_PTR replacement;
    ULONG_PTR* slot;
    ULONG_PTR original;
};

bool replace(Import& item, bool install) {
    const auto expected = install ? item.original : item.replacement;
    const auto replacement = install ? item.replacement : item.original;
    DWORD previous;
    if (!VirtualProtect(item.slot, sizeof(*item.slot), PAGE_READWRITE, &previous)) return false;
    const bool exchanged = reinterpret_cast<ULONG_PTR>(InterlockedCompareExchangePointer(
        reinterpret_cast<PVOID volatile*>(item.slot), reinterpret_cast<PVOID>(replacement),
        reinterpret_cast<PVOID>(expected))) == expected;
    DWORD ignored;
    const bool protected_again = VirtualProtect(item.slot, sizeof(*item.slot), previous, &ignored) != FALSE;
    return exchanged && protected_again;
}


bool installEngine(HMODULE engine) {
    Import imports[] = {
        {"CreateWindowExW", reinterpret_cast<ULONG_PTR>(&create_window), nullptr, 0},
        {"ShowWindow", reinterpret_cast<ULONG_PTR>(&show_window), nullptr, 0},
        {"SetWindowPos", reinterpret_cast<ULONG_PTR>(&position_window), nullptr, 0},
        {"MoveWindow", reinterpret_cast<ULONG_PTR>(&move_window), nullptr, 0},
        {"UpdateLayeredWindow", reinterpret_cast<ULONG_PTR>(&update_layered), nullptr, 0},
        {"SetWindowLongPtrW", reinterpret_cast<ULONG_PTR>(&set_long), nullptr, 0},
        {"GetWindowLongPtrW", reinterpret_cast<ULONG_PTR>(&get_long_ptr), nullptr, 0},
        {"GetWindowLongW", reinterpret_cast<ULONG_PTR>(&get_long), nullptr, 0},
        {"GetWindowRect", reinterpret_cast<ULONG_PTR>(&get_rect), nullptr, 0},
        {"MapWindowPoints", reinterpret_cast<ULONG_PTR>(&map_points), nullptr, 0},
    };
    const HMODULE user = GetModuleHandleW(L"user32.dll");
    auto base = reinterpret_cast<BYTE*>(engine);
    const auto dos = reinterpret_cast<IMAGE_DOS_HEADER*>(base);
    const auto nt = reinterpret_cast<IMAGE_NT_HEADERS*>(base + dos->e_lfanew);
    const auto directory = nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT];
    if (!directory.VirtualAddress) return false;
    for (auto descriptor = reinterpret_cast<IMAGE_IMPORT_DESCRIPTOR*>(base + directory.VirtualAddress);
         descriptor->Name; ++descriptor) {
        if (!descriptor->OriginalFirstThunk) continue;
        auto names = reinterpret_cast<IMAGE_THUNK_DATA*>(base + descriptor->OriginalFirstThunk);
        auto slots = reinterpret_cast<IMAGE_THUNK_DATA*>(base + descriptor->FirstThunk);
        for (; names->u1.AddressOfData; ++names, ++slots) {
            if (IMAGE_SNAP_BY_ORDINAL(names->u1.Ordinal)) continue;
            const auto name = reinterpret_cast<IMAGE_IMPORT_BY_NAME*>(base + names->u1.AddressOfData)->Name;
            for (auto& item : imports) {
                if (strcmp(name, item.name)) continue;
                if (item.slot) return false;
                item.original = reinterpret_cast<ULONG_PTR>(GetProcAddress(user, item.name));
                if (!item.original) return false;
                item.slot = &slots->u1.Function;
            }
        }
    }
    bool alreadyInstalled = true;
    for (const auto& item : imports) {
        if (!item.slot) return false;
        alreadyInstalled = alreadyInstalled && *item.slot == item.replacement;
    }
    if (alreadyInstalled) return true;
    // A different extension may own an import. Leave its input path intact.
    for (const auto& item : imports) if (*item.slot != item.original) return false;
    if (!restore_message)
        restore_message = RegisterWindowMessageW(L"NotepadPlusPlus.RestoreImePopup.93249AC7-8C55-4464-83B7-412D8A633C94");
    if (!restore_message) return false;
    for (auto& item : imports) {
        if (replace(item, true)) continue;
        for (auto& changed : imports)
            if (*changed.slot == changed.replacement) replace(changed, false);
        return false;
    }
    original_engine.store(engine, std::memory_order_release);
    enabled.store(true, std::memory_order_release);
    startupTrace("inputServices.imeWindows.installed");
    return true;
}

LRESULT CALLBACK observeWindow(int code, WPARAM wp, LPARAM lp) {
    const DWORD error = GetLastError();
    if (code == HCBT_CREATEWND) {
        const auto creation = reinterpret_cast<const CBT_CREATEWNDW*>(lp)->lpcs;
        WNDCLASSEXW cls{sizeof(cls)};
        if (creation->hInstance && GetClassInfoExW(creation->hInstance, creation->lpszClass, &cls) &&
            (cls.style & CS_IME)) {
            wchar_t path[32768] = {};
            const DWORD length = GetModuleFileNameW(creation->hInstance, path, _countof(path));
            if (length && length < _countof(path)) {
                const wchar_t* leaf = wcsrchr(path, L'\\');
                leaf = leaf ? leaf + 1 : path;
                // The already loaded QQ engine owns this class. Other IMEs are untouched.
                if ((!_wcsicmp(leaf, L"QQPinyin.ime") || !_wcsicmp(leaf, L"QQPinyinOriginal.dll")) &&
                    GetProcAddress(creation->hInstance, "ImeInquire") &&
                    GetProcAddress(creation->hInstance, "NotifyIME"))
                    installEngine(creation->hInstance);
            }
        }
    }
    SetLastError(error);
    return CallNextHookEx(nullptr, code, wp, lp);
}
} // namespace

StartupImeWindows::StartupImeWindows() {
    // Observe natural IME loading. No input profile activation or DLL preload.
    _hook = SetWindowsHookExW(WH_CBT, observeWindow, nullptr, GetCurrentThreadId());
}

StartupImeWindows::~StartupImeWindows() {
    if (_hook) UnhookWindowsHookEx(_hook);
    // Import targets and window callbacks live in the EXE until process exit.
}
