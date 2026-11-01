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


#include <stdexcept>
#include <memory>
#include "ImageListSet.h"
#include "StartupTrace.h"
#include "NppDarkMode.h"

void IconList::init(HINSTANCE hInst, int iconSize) 
{
	InitCommonControls();
	_hInst = hInst;
	_iconSize = iconSize;
	const int nbMore = 45;
	_hImglst = ImageList_Create(iconSize, iconSize, ILC_COLOR32 | ILC_MASK, 0, nbMore);
	if (!_hImglst)
		throw std::runtime_error("IconList::create : ImageList_Create() function returns null");
};

void IconList::create(int iconSize, HINSTANCE hInst, int *iconIDArray, int iconIDArraySize)
{
	StartupTraceScope trace(L"icons.resourceList");
	init(hInst, iconSize);
	_pIconIDArray = iconIDArray;
	_iconIDArraySize = iconIDArraySize;

	for (int i = 0 ; i < iconIDArraySize ; ++i)
		addIcon(iconIDArray[i]);
};

void IconList::addIcon(int iconID) const
{
	startupTrace("begin", L"icons.loadResource");
	HICON hIcon = ::LoadIcon(_hInst, MAKEINTRESOURCE(iconID));
	startupTrace("end", L"icons.loadResource");
	if (!hIcon)
		throw std::runtime_error("IconList::addIcon : LoadIcon() function return null");

	startupTrace("begin", L"icons.appendResource");
	ImageList_AddIcon(_hImglst, hIcon);
	startupTrace("end", L"icons.appendResource");
	::DestroyIcon(hIcon);
};


void IconList::addIcon(HICON hIcon) const
{
	if (hIcon)
		ImageList_AddIcon(_hImglst, hIcon);
};

bool IconList::changeIcon(int index, const TCHAR *iconLocation) const
{
	HBITMAP hBmp = (HBITMAP)::LoadImage(_hInst, iconLocation, IMAGE_ICON, _iconSize, _iconSize, LR_LOADFROMFILE | LR_LOADMAP3DCOLORS | LR_LOADTRANSPARENT);
	if (!hBmp)
		return false;
	int i = ImageList_ReplaceIcon(_hImglst, index, (HICON)hBmp);
	ImageList_AddMasked(_hImglst, (HBITMAP)hBmp, RGB(255,0,255));
	::DeleteObject(hBmp);
	return (i == index);
}

// used by tabbar only
void IconList::addIcons(int size) const
{
	ImageList_SetIconSize(_hImglst, size, size);
	for (int i = 0 ; i < _iconIDArraySize ; ++i)
		addIcon(_pIconIDArray[i]);
}

void ToolBarIcons::init(ToolBarButtonUnit *buttonUnitArray, int arraySize, std::vector<DynamicCmdIcoBmp> moreCmds)
{
	for (int i = 0 ; i < arraySize ; ++i)
		_tbiis.push_back(buttonUnitArray[i]);
	
	_moreCmds = moreCmds;
}

void ToolBarIcons::reInit(int size)
{
    // The next access rebuilds only the lists selected by the toolbar.
    for (bool& dirty : _listDirty)
        dirty = true;
    _iconSize = size;
}

HIMAGELIST ToolBarIcons::getList(int index) const
{
    IconList& list = _iconListVector[index];
    if (list.getHandle() && !_listDirty[index])
        return list.getHandle();
    StartupTraceScope trace(L"toolbar.selectedIcons");
    if (list.getHandle())
        ImageList_SetIconSize(list.getHandle(), _iconSize, _iconSize);
    else
        list.init(_hInst, _iconSize);
    for (const auto& button : _tbiis)
    {
        if (button._defaultIcon != -1)
        {
            const int icons[] = {button._defaultIcon, button._grayIcon,
                button._defaultIcon2, button._grayIcon2,
                button._defaultDarkModeIcon, button._grayDarkModeIcon,
                button._defaultDarkModeIcon2, button._grayDarkModeIcon2};
            list.addIcon(icons[index]);
        }
    }
    startupTrace("toolbar.selected.builtin.icons.ready");
    addDynamicIcons(list, index);
    _listDirty[index] = false;
    return list.getHandle();
}

void ToolBarIcons::addDynamicIcons(IconList& list, int index) const
{
    HDC dcScreen = nullptr;
    for (const auto& i : _moreCmds)
    {
		HICON hIcon = i._hIcon;
		bool ownsIcon = false;

		if (index < HLIST_DEFAULT_DM)
		{
			// Light toolbar lists retain the original plugin icon.
		}
		else if (i._hIcon_DM)
		{
			hIcon = i._hIcon_DM;
		}
		else
		{
			ICONINFO iconinfoSrc;
			GetIconInfo(i._hIcon, &iconinfoSrc);

			if (!dcScreen)
				dcScreen = ::GetDC(NULL);

			BITMAP bmp;
			int nbByteBmp = ::GetObject(iconinfoSrc.hbmColor, sizeof(BITMAP), &bmp);

			if (!nbByteBmp)
			{
				hIcon = i._hIcon;
			}
			else
			{
				BITMAPINFOHEADER bi = { 0 };

				bi.biSize = sizeof(BITMAPINFOHEADER);
				bi.biWidth = bmp.bmWidth;
				bi.biHeight = bmp.bmHeight;
				bi.biPlanes = 1;
				bi.biBitCount = 32;
				bi.biCompression = BI_RGB;
				bi.biSizeImage = 0;
				bi.biXPelsPerMeter = 0;
				bi.biYPelsPerMeter = 0;
				bi.biClrUsed = 0;
				bi.biClrImportant = 0;

				DWORD dwLineSize = ((bmp.bmWidth * bi.biBitCount + 31) / 32) * 4;
				DWORD dwBmpSize = dwLineSize * bmp.bmHeight;

				std::unique_ptr<BYTE[]> dibits(new BYTE[dwBmpSize]);

				GetDIBits(dcScreen, iconinfoSrc.hbmColor, 0, bi.biHeight, dibits.get(), (BITMAPINFO*)&bi, DIB_RGB_COLORS);

				for (int scanLine = 0; scanLine < bi.biHeight; ++scanLine)
				{
					BYTE* rawLine = dibits.get() + (dwLineSize * scanLine);
					RGBQUAD* pLine = (RGBQUAD*)rawLine;

					for (int pixel = 0; pixel < bi.biWidth; ++pixel)
					{
						RGBQUAD rgba = pLine[pixel];

						COLORREF c = RGB(rgba.rgbRed, rgba.rgbGreen, rgba.rgbBlue);
						COLORREF invert = NppDarkMode::invertLightness(c);

						rgba.rgbRed = GetRValue(invert);
						rgba.rgbBlue = GetBValue(invert);
						rgba.rgbGreen = GetGValue(invert);

						pLine[pixel] = rgba;
					}
				}

				HBITMAP hBmpNew = ::CreateCompatibleBitmap(dcScreen, bmp.bmWidth, bmp.bmHeight);

				SetDIBits(dcScreen, hBmpNew, 0, bi.biHeight, dibits.get(), (BITMAPINFO*)&bi, DIB_RGB_COLORS);


				ICONINFO iconinfoDest = { 0 };
				iconinfoDest.fIcon = TRUE;
				iconinfoDest.hbmColor = hBmpNew;
				iconinfoDest.hbmMask = iconinfoSrc.hbmMask;

				hIcon = ::CreateIconIndirect(&iconinfoDest);
				if (hIcon) ownsIcon = true;
				else hIcon = i._hIcon;

				::DeleteObject(hBmpNew);
				::DeleteObject(iconinfoSrc.hbmColor);
				::DeleteObject(iconinfoSrc.hbmMask);
			}
		}

        list.addIcon(hIcon);
        if (ownsIcon) ::DestroyIcon(hIcon);
    }
    if (dcScreen) ::ReleaseDC(NULL, dcScreen);
}

void ToolBarIcons::setDynamicIcons(const std::vector<DynamicCmdIcoBmp>& icons)
{
	_moreCmds = icons;
	int builtinCount = 0;
	for (const auto& button : _tbiis)
		if (button._defaultIcon != -1)
			++builtinCount;
	for (size_t index = 0; index < _iconListVector.size(); ++index)
	{
		auto& list = _iconListVector[index];
		if (list.getHandle() && !_listDirty[index])
		{
			::ImageList_SetImageCount(list.getHandle(), builtinCount);
			addDynamicIcons(list, static_cast<int>(index));
		}
	}
}


void ToolBarIcons::create(HINSTANCE hInst, int iconSize)
{
    _hInst = hInst;
    _iconSize = iconSize;
    _iconListVector.resize(8);
}

void ToolBarIcons::destroy()
{
    for (auto& list : _iconListVector)
        list.destroy();
}
