// Startup-only protection for the explicitly requested -noActivate launch.
// Plugins may create hidden top-level dialogs whose default WM_INITDIALOG
// processing otherwise activates the application before its main window opens.
#pragma once
#include <windows.h>

class StartupNoActivate final
{
public:
	explicit StartupNoActivate(bool enabled)
	{
		if (enabled)
			_hook = ::SetWindowsHookEx(WH_CBT, hookProc, nullptr, ::GetCurrentThreadId());
	}
	~StartupNoActivate()
	{
		if (_hook)
			::UnhookWindowsHookEx(_hook);
	}
	StartupNoActivate(const StartupNoActivate&) = delete;
	StartupNoActivate& operator=(const StartupNoActivate&) = delete;

private:
	HHOOK _hook = nullptr;
	static LRESULT CALLBACK hookProc(int code, WPARAM wParam, LPARAM lParam)
	{
		if (code == HCBT_ACTIVATE || code == HCBT_SETFOCUS)
		{
			DWORD process = 0;
			::GetWindowThreadProcessId(reinterpret_cast<HWND>(wParam), &process);
			if (process == ::GetCurrentProcessId())
				return 1;
		}
		return ::CallNextHookEx(nullptr, code, wParam, lParam);
	}
};
