// Suppress premature activation while the startup window tree is incomplete.
// A normal launch releases this guard before presenting its main window;
// -noActivate keeps it until startup and delayed plugin initialization finish.
#pragma once
#include <windows.h>
#include <commctrl.h>
#include <algorithm>
#include <vector>
#include "StartupTrace.h"

class StartupNoActivate final
{
public:
	static void setBackgroundMode(bool enabled)
	{
		backgroundMode() = enabled;
	}
	static bool permitsFocus()
	{
		if (!backgroundMode())
			return true;
		DWORD process = 0;
		::GetWindowThreadProcessId(::GetForegroundWindow(), &process);
		return process == ::GetCurrentProcessId();
	}
	explicit StartupNoActivate(bool enabled)
	{
		if (enabled)
		{
			_enabled = true;
			if (++depth() == 1)
			{
				active() = this;
				_hook = ::SetWindowsHookEx(WH_CBT, hookProc, nullptr, ::GetCurrentThreadId());
				::EnumThreadWindows(::GetCurrentThreadId(), updateSubclass, TRUE);
			}
		}
	}
	~StartupNoActivate()
	{
		finish();
	}
	void finish()
	{
		if (!_enabled)
			return;
		_enabled = false;
		if (--depth() == 0)
		{
			if (_hook)
				::UnhookWindowsHookEx(_hook);
			if (_creationHook)
				::UnhookWindowsHookEx(_creationHook);
			// Message-only windows seen by the creation hook are not returned by
			// EnumThreadWindows. Release exactly what this guard installed.
			for (HWND hwnd : _subclassedWindows)
				::RemoveWindowSubclass(hwnd, positionProc, 0x4e505042);
			_subclassedWindows.clear();
			active() = nullptr;
		}
	}
	StartupNoActivate(const StartupNoActivate&) = delete;
	StartupNoActivate& operator=(const StartupNoActivate&) = delete;

private:
	static bool& backgroundMode()
	{
		static bool value = false;
		return value;
	}
	HHOOK _hook = nullptr;
	HHOOK _creationHook = nullptr;
	std::vector<HWND> _pendingCreation;
	std::vector<HWND> _subclassedWindows;
	bool _enabled = false;
	static StartupNoActivate*& active()
	{
		static thread_local StartupNoActivate* value = nullptr;
		return value;
	}
	static unsigned& depth()
	{
		static thread_local unsigned value = 0;
		return value;
	}
	void beginCreation(HWND hwnd)
	{
		if (std::find(_pendingCreation.begin(), _pendingCreation.end(), hwnd) == _pendingCreation.end())
			_pendingCreation.push_back(hwnd);
		if (!_creationHook)
		{
			_creationHook = ::SetWindowsHookEx(WH_CALLWNDPROCRET, creationHookProc, nullptr, ::GetCurrentThreadId());
			startupTrace(_creationHook ? "background.creationHook.begin" : "background.creationHook.failed");
		}
	}
	void finishCreation(HWND hwnd, bool succeeded)
	{
		auto found = std::find(_pendingCreation.begin(), _pendingCreation.end(), hwnd);
		if (found == _pendingCreation.end())
			return;
		_pendingCreation.erase(found);
		if (succeeded && !(::GetWindowLongPtr(hwnd, GWL_STYLE) & WS_CHILD))
			updateSubclass(hwnd, TRUE);
		if (_pendingCreation.empty() && _creationHook)
		{
			::UnhookWindowsHookEx(_creationHook);
			_creationHook = nullptr;
			startupTrace("background.creationHook.end");
		}
	}
	static BOOL CALLBACK updateSubclass(HWND hwnd, LPARAM enabled)
	{
		if (enabled)
		{
			BOOL installed = ::SetWindowSubclass(hwnd, positionProc, 0x4e505042, 0);
			if (installed && active() && std::find(active()->_subclassedWindows.begin(),
				active()->_subclassedWindows.end(), hwnd) == active()->_subclassedWindows.end())
				active()->_subclassedWindows.push_back(hwnd);
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
		if (message == WM_INITDIALOG || message == WM_SHOWWINDOW)
			tracePosition(hwnd, message, depth(), nullptr);
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
		if (depth() && ((message == WM_WINDOWPOSCHANGED &&
				(reinterpret_cast<WINDOWPOS*>(lParam)->flags & SWP_SHOWWINDOW)) ||
				(message == WM_INITDIALOG && ::IsWindowVisible(hwnd))) &&
				!(::GetWindowLongPtr(hwnd, GWL_STYLE) & WS_CHILD) && ::GetWindow(hwnd, GW_OWNER))
		{
			// Owned dialogs can be reordered by the modal manager. Ownerless
			// windows already honor HWND_BOTTOM in WM_WINDOWPOSCHANGING.
			::SetWindowPos(hwnd, HWND_BOTTOM, 0, 0, 0, 0,
				SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOOWNERZORDER);
		}
		return result;
	}
	static LRESULT CALLBACK creationHookProc(int code, WPARAM wParam, LPARAM lParam)
	{
		if (code == HC_ACTION && active())
		{
			auto message = reinterpret_cast<CWPRETSTRUCT*>(lParam);
			// Install after successful non-client creation, before any show call.
			// The thread-wide message hook is needed only until this return.
			if (message->message == WM_NCCREATE)
				active()->finishCreation(message->hwnd, message->lResult != FALSE);
			else if (message->message == WM_NCDESTROY)
				active()->finishCreation(message->hwnd, false);
		}
		return ::CallNextHookEx(nullptr, code, wParam, lParam);
	}
	static LRESULT CALLBACK hookProc(int code, WPARAM wParam, LPARAM lParam)
	{
		if (code == HCBT_CREATEWND)
		{
			auto creation = reinterpret_cast<CBT_CREATEWND*>(lParam);
			if (!(creation->lpcs->style & WS_CHILD))
			{
				creation->hwndInsertAfter = HWND_BOTTOM;
				if (active())
					active()->beginCreation(reinterpret_cast<HWND>(wParam));
			}
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
