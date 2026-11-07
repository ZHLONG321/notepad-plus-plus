// Isolate Windows creation/region costs with equivalent hidden-window styles.
// No QQ module is loaded. No window is shown, activated, or given focus.
// Synthetic results identify platform costs; they are not app startup scores.
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <cstdio>
#include <string>

LARGE_INTEGER frequency;
bool privateDesktop;
double milliseconds(LARGE_INTEGER start, LARGE_INTEGER end) {
    return (end.QuadPart-start.QuadPart)*1000.0/frequency.QuadPart;
}
std::wstring inputDesktopName() {
    HDESK input = OpenInputDesktop(0, FALSE, DESKTOP_READOBJECTS);
    if (!input) return {};
    wchar_t name[256] = {}; DWORD size = 0;
    const bool ok = GetUserObjectInformationW(input, UOI_NAME, name, sizeof(name), &size) != FALSE;
    CloseDesktop(input); return ok ? name : L"";
}
BOOL CALLBACK countWindow(HWND, LPARAM value) { ++*reinterpret_cast<unsigned*>(value); return TRUE; }
DWORD WINAPI run(void* desktop) {
    if (desktop && !SetThreadDesktop(static_cast<HDESK>(desktop))) return 2;
    std::printf("thread,%lu,%lu\n", GetCurrentProcessId(), GetCurrentThreadId());
    WNDCLASSW cls = {};
    cls.style = CS_IME;
    cls.lpfnWndProc = DefWindowProcW;
    cls.hInstance = GetModuleHandleW(nullptr);
    cls.lpszClassName = L"NppChildWindowCostProbe";
    if (!RegisterClassW(&cls)) return 3;
    unsigned windows = 0;
    EnumDesktopWindows(GetThreadDesktop(GetCurrentThreadId()), countWindow, reinterpret_cast<LPARAM>(&windows));
    std::printf("desktop,%s,windows,%u\n", privateDesktop ? "private" : "current", windows);
    bool ok = true;
    for (int iteration = 0; iteration < 3; ++iteration) {
        for (int item = 0; item < 2; ++item) {
            const bool childFirst = (item == (iteration % 2));
            HWND parent = CreateWindowExW(0x8000088, cls.lpszClassName, L"", 0x88000000,
                0, 0, 1, 1, nullptr, nullptr, cls.hInstance, nullptr);
            if (!parent) return 4;
            HWND owned[6] = {};
            for (int i = 0; i < 6; ++i) {
                const DWORD ex = 0x88 | ((i < 5) ? WS_EX_LAYERED : 0);
                LARGE_INTEGER start, end;
                QueryPerformanceCounter(&start);
                owned[i] = CreateWindowExW(ex, cls.lpszClassName, L"", childFirst ? (0x0C000000 | WS_CHILD) : 0x8C000000,
                    0, 0, i == 3 ? 0 : 1, i == 3 ? 0 : 1, parent, nullptr, cls.hInstance, nullptr);
                const DWORD createError = GetLastError();
                QueryPerformanceCounter(&end);
                if (!owned[i]) std::printf("create_error,%lu\n", createError);
                const bool visible = owned[i] && IsWindowVisible(owned[i]);
                ok = ok && owned[i] && !visible;
                std::printf("create,%d,%d,%d,%.4f,%d,%d,%lld,%lld\n", iteration, childFirst, i,
                    milliseconds(start,end), owned[i] != nullptr, visible, start.QuadPart, end.QuadPart);
            }
            for (int repeat = 0; repeat < 2; ++repeat) {
                for (int i = 0; i < 3; ++i) {
                    HRGN query = CreateRectRgn(0, 0, 0, 0);
                    const int before = GetWindowRgn(owned[i], query);
                    DeleteObject(query);
                    LARGE_INTEGER start, end;
                    QueryPerformanceCounter(&start);
                    const int result = SetWindowRgn(owned[i], nullptr, FALSE);
                    QueryPerformanceCounter(&end);
                    ok = ok && result != 0;
                    std::printf("region,%d,%d,%d,%d,%.4f,%d,%d,%lld,%lld\n", iteration, childFirst, repeat, i,
                        milliseconds(start,end), result, before, start.QuadPart, end.QuadPart);
                }
            }
            if (childFirst) {
                for (int i = 0; i < 6; ++i) {
                    LARGE_INTEGER start, end;
                    QueryPerformanceCounter(&start);
                    SetLastError(0);
                    const HWND previous = SetParent(owned[i], nullptr);
                    const DWORD error = GetLastError();
                    const LONG_PTR style = GetWindowLongPtrW(owned[i], GWL_STYLE);
                    SetWindowLongPtrW(owned[i], GWL_STYLE, (style & ~WS_CHILD) | WS_POPUP);
                    SetWindowPos(owned[i], nullptr, 0, 0, 0, 0,
                        SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED);
                    SetLastError(0);
                    const LONG_PTR oldOwner = SetWindowLongPtrW(owned[i], GWLP_HWNDPARENT,
                        reinterpret_cast<LONG_PTR>(parent));
                    const DWORD ownerError = GetLastError();
                    QueryPerformanceCounter(&end);
                    const bool success = (previous || !error) && (oldOwner || !ownerError) &&
                        GetWindow(owned[i], GW_OWNER) == parent && !IsWindowVisible(owned[i]) &&
                        (GetWindowLongPtrW(owned[i], GWL_STYLE) & (WS_CHILD | WS_POPUP)) == WS_POPUP &&
                        (i >= 5 || (GetWindowLongPtrW(owned[i], GWL_EXSTYLE) & WS_EX_LAYERED));
                    ok = ok && success;
                    std::printf("attach,%d,%d,%.4f,%d,%lu,%lu,%lld,%lld\n", iteration, i,
                        milliseconds(start,end), success, error, ownerError, start.QuadPart, end.QuadPart);
                }
            }
            for (HWND window : owned) if (window) DestroyWindow(window);
            DestroyWindow(parent);
        }
    }
    return ok ? 0 : 5;
}
int main(int argc, char**) {
    QueryPerformanceFrequency(&frequency);
    std::printf("qpc_frequency,%lld\n", frequency.QuadPart);
    const auto originalInputDesktop = inputDesktopName();
    if (originalInputDesktop.empty()) return 6;
    privateDesktop = argc > 1;
    HDESK desktop = nullptr;
    if (privateDesktop) {
        wchar_t name[96];
        swprintf_s(name, L"NppChildWindowCost_%lu_%llu", GetCurrentProcessId(), GetTickCount64());
        desktop = CreateDesktopW(name, nullptr, nullptr, 0,
            DESKTOP_CREATEWINDOW | DESKTOP_READOBJECTS | DESKTOP_WRITEOBJECTS | DESKTOP_ENUMERATE, nullptr);
        if (!desktop) return 7;
    }
    HANDLE thread = CreateThread(nullptr, 0, run, desktop, 0, nullptr);
    if (!thread) return 8;
    if (WaitForSingleObject(thread, 15000) != WAIT_OBJECT_0) {
        std::puts("worker_timeout"); std::fflush(stdout);
        TerminateProcess(GetCurrentProcess(), 9); return 9;
    }
    DWORD result = 10; GetExitCodeThread(thread, &result);
    CloseHandle(thread); if (desktop) CloseDesktop(desktop);
    const bool unchanged = inputDesktopName() == originalInputDesktop;
    std::printf("done,%lu,input_desktop_unchanged,%d\n", result, unchanged);
    return unchanged ? static_cast<int>(result) : 11;
}
