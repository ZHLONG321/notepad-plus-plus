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



#include "DocTabView.h"
#include "ScintillaEditView.h"

#ifndef _WIN32_IE
#define _WIN32_IE	0x0600
#endif //_WIN32_IE


bool DocTabView::_hideTabBarStatus = false;

namespace
{
TCITEM makeBufferItem(Buffer* buffer, int mask, TCHAR* encodedLabel, bool includeBuffer)
{
	TCITEM item = {};
	if (includeBuffer)
	{
		item.mask = TCIF_PARAM;
		item.lParam = reinterpret_cast<LPARAM>(buffer->getID());
	}
	if (mask & (BufferChangeReadonly | BufferChangeDirty))
	{
		item.mask |= TCIF_IMAGE;
		item.iImage = buffer->isDirty() ? UNSAVED_IMG_INDEX : SAVED_IMG_INDEX;
		if (buffer->isMonitoringOn())
			item.iImage = MONITORING_IMG_INDEX;
		else if (buffer->isReadOnly())
			item.iImage = REDONLY_IMG_INDEX;
	}
	if (mask & BufferChangeFilename)
	{
		item.mask |= TCIF_TEXT;
		item.pszText = encodedLabel;
		const TCHAR* in = buffer->getFileName();
		TCHAR* out = encodedLabel;
		// Preserve the tab control's existing ampersand encoding.
		while (*in != 0)
		{
			if (*in == '&')
			{
				*out++ = '&';
				*out++ = '&';
				while (*(++in) == '&')
					*out++ = '&';
			}
			else
				*out++ = *in++;
		}
		*out = '\0';
	}
	return item;
}
}

void DocTabView::addBuffer(BufferID buffer)
{
	if (buffer == BUFFER_INVALID)	//valid only
		return;
	if (this->getIndexByBuffer(buffer) != -1)	//no duplicates
		return;
	Buffer * buf = MainFileManager.getBufferByID(buffer);
	TCHAR encodedLabel[2 * MAX_PATH];
	TCITEM tie = makeBufferItem(buf, BufferChangeMask, encodedLabel, true);
	::SendMessage(_hSelf, TCM_INSERTITEM, _nbItem++, reinterpret_cast<LPARAM>(&tie));
	// Insert the final label and image together, without looking up and
	// rewriting the newly inserted item before the first layout.
	// Hidden tabs receive their geometry from the owner before display. Avoid
	// forcing native row calculation after each session item while hidden.
	if (!_hideTabBarStatus && ::IsWindowVisible(_hSelf) && getTabBarThickness() != _tabBarThickness)
		::SendMessage(_hParent, WM_SIZE, 0, 0);
}


void DocTabView::closeBuffer(BufferID buffer)
{
	int indexToClose = getIndexByBuffer(buffer);
	deletItemAt((size_t)indexToClose);
	::SendMessage(_hParent, WM_SIZE, 0, 0);
}


bool DocTabView::activateBuffer(BufferID buffer)
{
	int indexToActivate = getIndexByBuffer(buffer);
	if (indexToActivate == -1)
		return false;	//cannot activate

	activateAt(indexToActivate);
	// Native tabs arrange their own rows on selection. The host only needs to
	// resize the editor when the strip actually consumes a different extent.
	if (!_hideTabBarStatus && ::IsWindowVisible(_hSelf) && getTabBarThickness() != _tabBarThickness)
		::SendMessage(_hParent, WM_SIZE, 0, 0);
	return true;
}


BufferID DocTabView::activeBuffer()
{
	int index = getCurrentTabIndex();
	return static_cast<BufferID>(getBufferByIndex(index));
}


BufferID DocTabView::findBufferByName(const TCHAR * fullfilename) //-1 if not found, something else otherwise
{
	TCITEM tie;
	tie.lParam = -1;
	tie.mask = TCIF_PARAM;
	for (size_t i = 0; i < _nbItem; ++i)
	{
		::SendMessage(_hSelf, TCM_GETITEM, i, reinterpret_cast<LPARAM>(&tie));
		BufferID id = reinterpret_cast<BufferID>(tie.lParam);
		Buffer * buf = MainFileManager.getBufferByID(id);
		if (OrdinalIgnoreCaseCompareStrings(fullfilename, buf->getFullPathName()) == 0)
		{
			return id;
		}
	}
	return BUFFER_INVALID;
}


int DocTabView::getIndexByBuffer(BufferID id)
{
	TCITEM tie;
	tie.lParam = -1;
	tie.mask = TCIF_PARAM;
	for (size_t i = 0; i < _nbItem; ++i)
	{
		::SendMessage(_hSelf, TCM_GETITEM, i, reinterpret_cast<LPARAM>(&tie));
		if (reinterpret_cast<BufferID>(tie.lParam) == id)
			return static_cast<int>(i);
	}
	return -1;
}


BufferID DocTabView::getBufferByIndex(size_t index)
{
	TCITEM tie;
	tie.lParam = -1;
	tie.mask = TCIF_PARAM;
	::SendMessage(_hSelf, TCM_GETITEM, index, reinterpret_cast<LPARAM>(&tie));

	return reinterpret_cast<BufferID>(tie.lParam);
}


void DocTabView::bufferUpdated(Buffer * buffer, int mask)
{
	// Document metadata and tab geometry have separate update paths. Recent-use
	// ordering belongs to document selection, not to the native tab item.
	if (!(mask & (BufferChangeReadonly | BufferChangeDirty | BufferChangeFilename)))
		return;
	int index = getIndexByBuffer(buffer->getID());
	if (index == -1)
		return;

	TCHAR encodedLabel[2 * MAX_PATH];
	TCITEM tie = makeBufferItem(buffer, mask, encodedLabel, false);

	::SendMessage(_hSelf, TCM_SETITEM, index, reinterpret_cast<LPARAM>(&tie));

	// Native tabs recalculate their rows when an item changes. Relayout the
	// host only when those rows change the space available to the editor.
	if ((mask & BufferChangeFilename) && !_hideTabBarStatus && ::IsWindowVisible(_hSelf)
		&& getTabBarThickness() != _tabBarThickness)
		::SendMessage(_hParent, WM_SIZE, 0, 0);
}


void DocTabView::setBuffer(size_t index, BufferID id)
{
	if (index < 0 || index >= _nbItem)
		return;

	TCHAR encodedLabel[2 * MAX_PATH];
	TCITEM tie = makeBufferItem(MainFileManager.getBufferByID(id), BufferChangeMask, encodedLabel, true);
	::SendMessage(_hSelf, TCM_SETITEM, index, reinterpret_cast<LPARAM>(&tie));

	if (!_hideTabBarStatus && ::IsWindowVisible(_hSelf) && getTabBarThickness() != _tabBarThickness)
		::SendMessage(_hParent, WM_SIZE, 0, 0);
}


void DocTabView::reSizeTo(RECT & rc)
{
	int borderWidth = ((NppParameters::getInstance()).getSVP())._borderWidth;
	if (_hideTabBarStatus)
	{
		RECT rcTmp = rc;
		TabBar::reSizeTo(rcTmp);
		_pView->reSizeTo(rc);
	}
	else
	{
		TabBar::reSizeTo(rc);
		rc.left	 += borderWidth;
		rc.right -= borderWidth * 2;
		rc.top   += borderWidth;
		rc.bottom -= (borderWidth * 2);
		_pView->reSizeTo(rc);
	}
	SendMessage(_hParent, NPPM_INTERNAL_UPDATECLICKABLELINKS, reinterpret_cast<WPARAM>(_pView), 0);
}

