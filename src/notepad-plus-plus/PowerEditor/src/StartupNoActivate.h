// Startup-only protection for the explicitly requested -noActivate launch.
// Plugins may create hidden top-level dialogs whose default WM_INITDIALOG
// processing otherwise activates the application before its main window opens.
#pragma once
#include <windows.h>
#include <commctrl.h>
#include "StartupTrace.h"

class StartupNoActivate final
{
public:
	explicit StartupNoActivate(bool enabled)
	{
		if (enabled)
		{
			_enabled = true;
			++depth();
			_hook = ::SetWindowsHookEx(WH_CBT, hookProc, nullptr, ::GetCurrentThreadId());
			_positionHook = ::SetWindowsHookEx(WH_CALLWNDPROCRET, positionHookProc, nullptr, ::GetCurrentThreadId());
			::EnumThreadWindows(::GetCurrentThreadId(), updateSubclass, TRUE);
		}
	}
	~StartupNoActivate()
	{
		if (_hook)
			::UnhookWindowsHookEx(_hook);
		if (_positionHook)
			::UnhookWindowsHookEx(_positionHook);
		if (_enabled && --depth() == 0)
			::EnumThreadWindows(::GetCurrentThreadId(), updateSubclass, FALSE);
	}
	StartupNoActivate(const StartupNoActivate&) = delete;
	StartupNoActivate& operator=(const StartupNoActivate&) = delete;

private:
	HHOOK _hook = nullptr;
	HHOOK _positionHook = nullptr;
	bool _enabled = false;
	static unsigned& depth()
	{
		static thread_local unsigned value = 0;
		return value;
	}
	static BOOL CALLBACK updateSubclass(HWND hwnd, LPARAM enabled)
	{
		if (enabled)
		{
			BOOL installed = ::SetWindowSubclass(hwnd, positionProc, 0x4e505042, 0);
			tracePosition(hwnd, WM_NCCREATE, installed, nullptr);
		}
		else
			::RemoveWindowSubclass(hwnd, positionProc, 0x4e505042);
		return TRUE;
	}
	static void tracePosition(HWND hwnd, UINT message, UINT_PTR value, const WINDOWPOS* position)
	{
		if (startupTraceHandle() == INVALID_HANDLE_VALUE)
			return;
		wchar_t detail[160];
		swprintf_s(detail, L"hwnd=%p msg=%x value=%llx after=%p flags=%x", hwnd, message,
			static_cast<unsigned long long>(value), position ? position->hwndInsertAfter : nullptr,
			position ? position->flags : 0);
		startupTrace("background.order", detail);
	}
	static LRESULT CALLBACK positionProc(HWND hwnd, UINT message, WPARAM wParam, LPARAM lParam,
		UINT_PTR subclass, DWORD_PTR)
	{
		if (message == WM_NCDESTROY)
			::RemoveWindowSubclass(hwnd, positionProc, subclass);
		LRESULT result = ::DefSubclassProc(hwnd, message, wParam, lParam);
		if (message == WM_WINDOWPOSCHANGING || message == WM_WINDOWPOSCHANGED)
			tracePosition(hwnd, message, depth(), reinterpret_cast<WINDOWPOS*>(lParam));
		if (message == WM_WINDOWPOSCHANGING && depth())
		{
			auto position = reinterpret_cast<WINDOWPOS*>(lParam);
			if (!(position->flags & SWP_NOZORDER) || (position->flags & SWP_SHOWWINDOW))
			{
				position->hwndInsertAfter = HWND_BOTTOM;
				position->flags &= ~SWP_NOZORDER;
			}
		}
		return result;
	}
	static LRESULT CALLBACK positionHookProc(int code, WPARAM wParam, LPARAM lParam)
	{
		if (code == HC_ACTION)
		{
			auto message = reinterpret_cast<CWPRETSTRUCT*>(lParam);
			if (message->message == WM_INITDIALOG || message->message == WM_SHOWWINDOW)
			{
				DWORD_PTR installed = 0;
				tracePosition(message->hwnd, message->message,
					::GetWindowSubclass(message->hwnd, positionProc, 0x4e505042, &installed), nullptr);
			}
			// Install after successful non-client creation, before any show call.
			if (message->message == WM_NCCREATE && message->lResult &&
				!(::GetWindowLongPtr(message->hwnd, GWL_STYLE) & WS_CHILD))
				updateSubclass(message->hwnd, TRUE);
		}
		return ::CallNextHookEx(nullptr, code, wParam, lParam);
	}
	static LRESULT CALLBACK hookProc(int code, WPARAM wParam, LPARAM lParam)
	{
		if (code == HCBT_CREATEWND)
		{
			auto creation = reinterpret_cast<CBT_CREATEWND*>(lParam);
			if (!(creation->lpcs->style & WS_CHILD))
				creation->hwndInsertAfter = HWND_BOTTOM;
		}
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
