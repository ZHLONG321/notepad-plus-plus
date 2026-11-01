// This file is part of Notepad++ project
// Copyright (C)2021 Don HO <don.h@free.fr>

// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// at your option any later version.
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
// GNU General Public License for more details.
//
// You should have received a copy of the GNU General Public License
// along with this program.  If not, see <https://www.gnu.org/licenses/>.


#pragma once
#include <windows.h>
#include "StartupNoActivate.h"

class Window
{
public:
	//! \name Constructors & Destructor
	//@{
	Window() = default;
	Window(const Window&) = delete;
	virtual ~Window() = default;
	//@}


	virtual void init(HINSTANCE hInst, HWND parent)
	{
		_hInst = hInst;
		_hParent = parent;
	}

	virtual void destroy() = 0;

	virtual void display(bool toShow = true) const
	{
		::ShowWindow(_hSelf, toShow ? SW_SHOW : SW_HIDE);
	}


	virtual void reSizeTo(RECT & rc) // should NEVER be const !!!
	{
		// Let the message loop paint the completed layout. MoveWindow(TRUE)
		// paints immediately, exposing intermediate sibling positions, and the
		// following redraw used to erase that freshly painted content again.
		::SetWindowPos(_hSelf, nullptr, rc.left, rc.top, rc.right, rc.bottom,
			SWP_NOZORDER | SWP_NOACTIVATE | SWP_DEFERERASE);
	}


	virtual void reSizeToWH(RECT& rc) // should NEVER be const !!!
	{
		::SetWindowPos(_hSelf, nullptr, rc.left, rc.top, rc.right - rc.left, rc.bottom - rc.top,
			SWP_NOZORDER | SWP_NOACTIVATE | SWP_DEFERERASE);
	}


	virtual void redraw(bool forceUpdate = false) const
	{
		::InvalidateRect(_hSelf, nullptr, TRUE);
		if (forceUpdate)
			::UpdateWindow(_hSelf);
	}


    virtual void getClientRect(RECT & rc) const
	{
		::GetClientRect(_hSelf, &rc);
	}

	virtual void getWindowRect(RECT & rc) const
	{
		::GetWindowRect(_hSelf, &rc);
	}

	virtual int getWidth() const
	{
		RECT rc;
		::GetClientRect(_hSelf, &rc);
		return (rc.right - rc.left);
	}

	virtual int getHeight() const
	{
		RECT rc;
		::GetClientRect(_hSelf, &rc);
		if (::IsWindowVisible(_hSelf) == TRUE)
			return (rc.bottom - rc.top);
		return 0;
	}

	virtual bool isVisible() const
	{
    	return (::IsWindowVisible(_hSelf)?true:false);
	}

	HWND getHSelf() const
	{
		return _hSelf;
	}

	HWND getHParent() const {
		return _hParent;
	}

	void getFocus() const {
		// A background-only launch must not activate itself when a dialog
		// returns focus to its editor. Normal and foreground use stay unchanged.
		if (StartupNoActivate::permitsFocus())
			::SetFocus(_hSelf);
	}

    HINSTANCE getHinst() const
	{
		//assert(_hInst != 0);
		return _hInst;
	}


	Window& operator = (const Window&) = delete;


protected:
	HINSTANCE _hInst = NULL;
	HWND _hParent = NULL;
	HWND _hSelf = NULL;
};
