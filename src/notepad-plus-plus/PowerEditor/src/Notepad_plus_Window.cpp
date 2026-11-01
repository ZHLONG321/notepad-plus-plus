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


#include <time.h>
#include <shlwapi.h>
#include <dwmapi.h>
#include "Notepad_plus_Window.h"
#include "NppDarkMode.h"
#include "StartupNoActivate.h"
#include <algorithm>

const TCHAR Notepad_plus_Window::_className[32] = TEXT("Notepad++");
HWND Notepad_plus_Window::gNppHWND = NULL;



#include "StartupTrace.h"

void Notepad_plus_Window::init(HINSTANCE hInst, HWND parent, const TCHAR *cmdLine, CmdLineParams *cmdLineParams)
{
	startupTrace("window.init.begin");
	StartupNoActivate noActivate(cmdLineParams->_noActivate);
	_startupNoActivate = cmdLineParams->_noActivate;
	time_t timestampBegin = 0;
	if (cmdLineParams->_showLoadingTime)
		timestampBegin = time(NULL);

	Window::init(hInst, parent);
	WNDCLASS nppClass;

	nppClass.style = CS_BYTEALIGNWINDOW | CS_DBLCLKS;
	nppClass.lpfnWndProc = Notepad_plus_Proc;
	nppClass.cbClsExtra = 0;
	nppClass.cbWndExtra = 0;
	nppClass.hInstance = _hInst;
	nppClass.hIcon = ::LoadIcon(hInst, MAKEINTRESOURCE(IDI_M30ICON));
	nppClass.hCursor = ::LoadCursor(NULL, IDC_ARROW);
	// Theme brushes can be recreated when custom colors change. Keep a stable
	// class brush; WM_ERASEBKGND obtains the current dark brush when painting.
	nppClass.hbrBackground = ::GetSysColorBrush(COLOR_MENU);
	nppClass.lpszMenuName = MAKEINTRESOURCE(IDR_M30_MENU);
	nppClass.lpszClassName = _className;

	_isPrelaunch = cmdLineParams->_isPreLaunch;

	if (!::RegisterClass(&nppClass))
	{
		throw std::runtime_error("Notepad_plus_Window::init : RegisterClass() function failed");
	}

	NppParameters& nppParams = NppParameters::getInstance();
	NppGUI & nppGUI = nppParams.getNppGUI();

	if (cmdLineParams->_isNoPlugin)
		_notepad_plus_plus_core._pluginsManager.disable();

	nppGUI._isCmdlineNosessionActivated = cmdLineParams->_isNoSession;

	_hIconAbsent = ::LoadIcon(hInst, MAKEINTRESOURCE(IDI_ICONABSENT));

	_hSelf = ::CreateWindowEx(
		WS_EX_ACCEPTFILES | (_notepad_plus_plus_core._nativeLangSpeaker.isRTL()?WS_EX_LAYOUTRTL:0),
		_className,
		TEXT("Notepad++"),
		(WS_OVERLAPPEDWINDOW | WS_CLIPCHILDREN | (cmdLineParams->_noActivate && nppGUI._isMaximized ? WS_MAXIMIZE : 0)),
		// CreateWindowEx bug : set all 0 to walk around the pb
		0, 0, 0, 0,
		_hParent, nullptr, _hInst,
		(LPVOID) this); // pass the ptr of this instantiated object
        // for retrieve it in Notepad_plus_Proc from
        // the CREATESTRUCT.lpCreateParams afterward.

	if (NULL == _hSelf)
		throw std::runtime_error("Notepad_plus_Window::init : CreateWindowEx() function return null");


	startupTrace("window.created");

	_notepad_plus_plus_core.staticCheckMenuAndTB();

	gNppHWND = _hSelf;

	if (cmdLineParams->isPointValid())
	{
		::MoveWindow(_hSelf, cmdLineParams->_point.x, cmdLineParams->_point.y, nppGUI._appPos.right, nppGUI._appPos.bottom, TRUE);
	}
	else
	{
		WINDOWPLACEMENT posInfo;
	    posInfo.length = sizeof(WINDOWPLACEMENT);
		posInfo.flags = 0;
		if (_isPrelaunch)
			posInfo.showCmd = SW_HIDE;
		else
			posInfo.showCmd = SW_HIDE; // Size the hidden window before restoring its contents.

		posInfo.ptMinPosition.x = (LONG)-1;
		posInfo.ptMinPosition.y = (LONG)-1;
		posInfo.ptMaxPosition.x = (LONG)-1;
		posInfo.ptMaxPosition.y = (LONG)-1;
		posInfo.rcNormalPosition.left   = nppGUI._appPos.left;
		posInfo.rcNormalPosition.top    = nppGUI._appPos.top;
		posInfo.rcNormalPosition.bottom = nppGUI._appPos.top + nppGUI._appPos.bottom;
		posInfo.rcNormalPosition.right  = nppGUI._appPos.left + nppGUI._appPos.right;

		//SetWindowPlacement will take care of situations, where saved position was in no longer available monitor
		::SetWindowPlacement(_hSelf,&posInfo);
	}

	if ((nppGUI._tabStatus & TAB_MULTILINE) != 0)
		::SendMessage(_hSelf, WM_COMMAND, IDM_VIEW_DRAWTABBAR_MULTILINE, 0);

	if (!nppGUI._menuBarShow)
		::SetMenu(_hSelf, NULL);

	if (cmdLineParams->_isNoTab || (nppGUI._tabStatus & TAB_HIDE))
	{
		const int tabStatusOld = nppGUI._tabStatus;
		::SendMessage(_hSelf, NPPM_HIDETABBAR, 0, TRUE);
		if (cmdLineParams->_isNoTab)
		{
			// Restore old settings when tab bar has been hidden from tab bar.
			nppGUI._tabStatus = tabStatusOld;
		}
	}

	if (cmdLineParams->_alwaysOnTop)
		::SendMessage(_hSelf, WM_COMMAND, IDM_VIEW_ALWAYSONTOP, 0);

	if (nppGUI._rememberLastSession && !nppGUI._isCmdlineNosessionActivated)
		_notepad_plus_plus_core.loadLastSession();

	startupTrace("window.positioned");
	{
	StartupTraceScope trace(L"startup.configurationCatalogs");
	std::vector<generic_string> fileNames;
	std::vector<generic_string> patterns;
	patterns.push_back(TEXT("*.xml"));

	generic_string nppDir = nppParams.getNppPath();

	LocalizationSwitcher & localizationSwitcher = nppParams.getLocalizationSwitcher();
	std::wstring localizationDir = nppDir;
	PathAppend(localizationDir, TEXT("localization\\"));

	_notepad_plus_plus_core.getMatchedFileNames(localizationDir.c_str(), patterns, fileNames, false, false);
	for (size_t i = 0, len = fileNames.size(); i < len; ++i)
		localizationSwitcher.addLanguageFromXml(fileNames[i]);

	fileNames.clear();
	ThemeSwitcher & themeSwitcher = nppParams.getThemeSwitcher();

	//  Get themes from both npp install themes dir and app data themes dir with the per user
	//  overriding default themes of the same name.

	generic_string appDataThemeDir;
    if (nppParams.getAppDataNppDir() && nppParams.getAppDataNppDir()[0])
    {
		appDataThemeDir = nppParams.getAppDataNppDir();
	    PathAppend(appDataThemeDir, TEXT("themes\\"));
	    _notepad_plus_plus_core.getMatchedFileNames(appDataThemeDir.c_str(), patterns, fileNames, false, false);
	    for (size_t i = 0, len = fileNames.size() ; i < len ; ++i)
	    {
		    themeSwitcher.addThemeFromXml(fileNames[i]);
	    }
    }

	fileNames.clear();

	generic_string nppThemeDir;
	nppThemeDir = nppDir.c_str(); // <- should use the pointer to avoid the constructor of copy
	PathAppend(nppThemeDir, TEXT("themes\\"));

	// Set theme directory to their installation directory
	themeSwitcher.setThemeDirPath(nppThemeDir);

	_notepad_plus_plus_core.getMatchedFileNames(nppThemeDir.c_str(), patterns, fileNames, false, false);
	for (size_t i = 0, len = fileNames.size(); i < len ; ++i)
	{
		generic_string themeName( themeSwitcher.getThemeFromXmlFileName(fileNames[i].c_str()) );
		if (!themeSwitcher.themeNameExists(themeName.c_str()) )
		{
			themeSwitcher.addThemeFromXml(fileNames[i]);
			
			if (!appDataThemeDir.empty())
			{
				generic_string appDataThemePath = appDataThemeDir;

				if (!::PathFileExists(appDataThemePath.c_str()))
				{
					::CreateDirectory(appDataThemePath.c_str(), NULL);
				}

				TCHAR* fn = PathFindFileName(fileNames[i].c_str());
				PathAppend(appDataThemePath, fn);
				themeSwitcher.addThemeStylerSavePath(fileNames[i], appDataThemePath);
			}
		}
	}

	}
	// Restore all dockable panels from the last session
	{
	StartupTraceScope trace(L"startup.savedPanels");
	for (size_t i = 0, len = _notepad_plus_plus_core._internalFuncIDs.size() ; i < len ; ++i)
		::SendMessage(_hSelf, WM_COMMAND, _notepad_plus_plus_core._internalFuncIDs[i], 0);
	}

	std::vector<generic_string> fns;
	{
	StartupTraceScope trace(L"startup.commandLineFiles");
	if (cmdLine)
		fns = _notepad_plus_plus_core.loadCommandlineParams(cmdLine, cmdLineParams);

	// Launch folder as workspace after all this dockable panel being restored from the last session
	// To avoid dockable panel toggle problem.
	if (cmdLineParams->_openFoldersAsWorkspace)
	{
		generic_string emptyStr;
		_notepad_plus_plus_core.launchFileBrowser(fns, emptyStr, true);
	}
	}
	// Present only after layout, files and saved panels have been restored.
	if (nppParams.doFunctionListExport() || nppParams.doPrintAndExit())
	{
		::ShowWindow(_hSelf, SW_HIDE);
	}
	else if (!cmdLineParams->_isPreLaunch)
	{
		// Avoid exposing an animated, only partly composed startup surface.
		// Resolve optionally so systems without DWM keep the original path.
		HMODULE dwm = ::LoadLibraryExW(L"dwmapi.dll", nullptr, LOAD_LIBRARY_SEARCH_SYSTEM32);
		using SetAttribute = HRESULT(WINAPI*)(HWND, DWORD, LPCVOID, DWORD);
		using FlushComposition = HRESULT(WINAPI*)();
		auto setAttribute = dwm ? reinterpret_cast<SetAttribute>(::GetProcAddress(dwm, "DwmSetWindowAttribute")) : nullptr;
		BOOL disableTransition = TRUE;
		bool transitionDisabled = setAttribute && SUCCEEDED(setAttribute(_hSelf,
			DWMWA_TRANSITIONS_FORCEDISABLED, &disableTransition, sizeof(disableTransition)));
		if (cmdLineParams->_noActivate)
			::ShowWindow(_hSelf, SW_SHOWNA);
		else if (cmdLineParams->isPointValid())
			::ShowWindow(_hSelf, SW_SHOW);
		else
			::ShowWindow(_hSelf, nppGUI._isMaximized ? SW_MAXIMIZE : SW_SHOW);
		// Paint the themed client and children before READY handlers can block
		// the UI thread. Otherwise DWM can expose their unpainted white surfaces.
		::RedrawWindow(_hSelf, nullptr, nullptr,
			RDW_INVALIDATE | RDW_ERASE | RDW_ALLCHILDREN | RDW_UPDATENOW);
		if (transitionDisabled)
		{
			auto flush = reinterpret_cast<FlushComposition>(::GetProcAddress(dwm, "DwmFlush"));
			if (flush)
				flush();
			// Later minimize, restore and maximize animations retain their default.
			disableTransition = FALSE;
			setAttribute(_hSelf, DWMWA_TRANSITIONS_FORCEDISABLED, &disableTransition, sizeof(disableTransition));
		}
		if (dwm)
			::FreeLibrary(dwm);
		startupTrace("window.present.committed");
	}
	else
	{
		_notepad_plus_plus_core._pTrayIco = new trayIconControler(_hSelf, IDI_M30ICON, NPPM_INTERNAL_MINIMIZED_TRAY, ::LoadIcon(_hInst, MAKEINTRESOURCE(IDI_M30ICON)), TEXT(""));
		_notepad_plus_plus_core._pTrayIco->doTrayIcon(ADD);
	}
	startupTrace("window.shown");
	if (!cmdLineParams->_noActivate)
		::SendMessage(_hSelf, WM_ACTIVATE, WA_ACTIVE, 0);

	// Notify plugins that Notepad++ is ready
	SCNotification scnN;
	scnN.nmhdr.code = NPPN_READY;
	scnN.nmhdr.hwndFrom = _hSelf;
	scnN.nmhdr.idFrom = 0;
	_notepad_plus_plus_core._pluginsManager.notify(&scnN);
	startupTrace("plugins.ready");
	if (_notepad_plus_plus_core._pluginsManager.hasDelayedPlugins())
	{
		// With a window, WM_TIMER carries nIDEvent, not SetTimer's return value.
		_delayedPluginTimer = reinterpret_cast<UINT_PTR>(this);
		if (!::SetTimer(_hSelf, _delayedPluginTimer, 25, nullptr))
			_delayedPluginTimer = 0;
		startupTrace(_delayedPluginTimer ? "plugins.timer.armed" : "plugins.timer.failed");
	}

	if (!cmdLineParams->_easterEggName.empty())
	{
		if (cmdLineParams->_quoteType == 0) // Easter Egg Name
		{
			int iQuote = _notepad_plus_plus_core.getQuoteIndexFrom(cmdLineParams->_easterEggName.c_str());
			if (iQuote != -1)
			{
				_notepad_plus_plus_core.showQuoteFromIndex(iQuote);
			}
		}
		else if (cmdLineParams->_quoteType == 1) // command line quote
		{
			_userQuote = cmdLineParams->_easterEggName;
			_quoteParams.reset();
			_quoteParams._quote = _userQuote.c_str();
			_quoteParams._quoter = TEXT("Anonymous #999");
			_quoteParams._shouldBeTrolling = false;
			_quoteParams._lang = cmdLineParams->_langType;
			if (cmdLineParams->_ghostTypingSpeed == 1)
				_quoteParams._speed = QuoteParams::slow;
			else if (cmdLineParams->_ghostTypingSpeed == 2)
				_quoteParams._speed = QuoteParams::rapid;
			else if (cmdLineParams->_ghostTypingSpeed == 3)
				_quoteParams._speed = QuoteParams::speedOfLight;

			_notepad_plus_plus_core.showQuote(&_quoteParams);
		}
		else if (cmdLineParams->_quoteType == 2) // content drom file
		{
			if (::PathFileExists(cmdLineParams->_easterEggName.c_str()))
			{
				std::string content = getFileContent(cmdLineParams->_easterEggName.c_str());
				WcharMbcsConvertor& wmc = WcharMbcsConvertor::getInstance();
				_userQuote = wmc.char2wchar(content.c_str(), SC_CP_UTF8);
				if (!_userQuote.empty())
				{
					_quoteParams.reset();
					_quoteParams._quote = _userQuote.c_str();
					_quoteParams._quoter = TEXT("Anonymous #999");
					_quoteParams._shouldBeTrolling = false;
					_quoteParams._lang = cmdLineParams->_langType;
					if (cmdLineParams->_ghostTypingSpeed == 1)
						_quoteParams._speed = QuoteParams::slow;
					else if (cmdLineParams->_ghostTypingSpeed == 2)
						_quoteParams._speed = QuoteParams::rapid;
					else if (cmdLineParams->_ghostTypingSpeed == 3)
						_quoteParams._speed = QuoteParams::speedOfLight;

					_notepad_plus_plus_core.showQuote(&_quoteParams);
				}
			}
		}
	}

	if (cmdLineParams->_showLoadingTime)
	{
		time_t timestampEnd = time(NULL);
		double loadTime = difftime(timestampEnd, timestampBegin);

		char dest[256];
		sprintf(dest, "Loading time : %.0lf seconds", loadTime);
		::MessageBoxA(NULL, dest, "", MB_OK);
	}

	bool isSnapshotMode = nppGUI.isSnapshotMode();
	if (isSnapshotMode)
	{
		_notepad_plus_plus_core.checkModifiedDocument(false);
		// Lauch backup task
		_notepad_plus_plus_core.launchDocumentBackupTask();
	}

	// Make this call later to take effect
	::SendMessage(_hSelf, NPPM_INTERNAL_SETWORDCHARS, 0, 0);

	if (nppParams.doFunctionListExport())
		::SendMessage(_hSelf, NPPM_INTERNAL_EXPORTFUNCLISTANDQUIT, 0, 0);

	if (nppParams.doPrintAndExit())
		::SendMessage(_hSelf, NPPM_INTERNAL_PRNTANDQUIT, 0, 0);
}


bool Notepad_plus_Window::isDlgsMsg(MSG *msg) const
{
	for (size_t i = 0, len = _notepad_plus_plus_core._hModelessDlgs.size(); i < len; ++i)
	{
		if (_notepad_plus_plus_core.processIncrFindAccel(msg))
			return true;

		if (_notepad_plus_plus_core.processFindAccel(msg))
			return true;

		if (::IsDialogMessageW(_notepad_plus_plus_core._hModelessDlgs[i], msg))
			return true;
	}
	return false;
}

void Notepad_plus_Window::loadOneDelayedPlugin()
{
	static bool firstTick = true;
	if (firstTick)
	{
		startupTrace(_startupEditorPainted ? "plugins.idle.painted" : "plugins.idle.wait_paint");
		firstTick = false;
	}
	auto& core = _notepad_plus_plus_core;
	auto& manager = core._pluginsManager;
	if (!manager.hasDelayedPlugins())
	{
		::KillTimer(_hSelf, _delayedPluginTimer);
		_delayedPluginTimer = 0;
		return;
	}
	if (_delayedLoadInProgress || !_startupEditorPainted || !::IsWindowVisible(_hSelf) ||
		HIWORD(::GetQueueStatus(QS_INPUT | QS_PAINT)))
		return;
	// An empty queue immediately after a keystroke is not an input pause.
	if (_lastInputTick && ::GetTickCount() - _lastInputTick < 100)
		return;
	// Input in another application must not indefinitely postpone our plugins.
	// Keep the quiet period while any window of this process is foreground.
	DWORD foregroundProcess = 0;
	::GetWindowThreadProcessId(::GetForegroundWindow(), &foregroundProcess);
	if (foregroundProcess == ::GetCurrentProcessId())
	{
		LASTINPUTINFO input = {sizeof(input)};
		if (::GetLastInputInfo(&input) && ::GetTickCount() - input.dwTime < 100)
			return;
	}

	_delayedLoadInProgress = true;
	struct ResetBusy { bool& busy; ~ResetBusy() { busy = false; } } resetBusy{_delayedLoadInProgress};
	StartupNoActivate noActivate(_startupNoActivate);
	auto& params = NppParameters::getInstance();
	const auto priorShortcuts = params.getPluginCommandList();
	const int index = manager.loadNextDelayedPlugin();
	if (index < 0)
		return;
	params.reloadPluginCmds();
	// Preserve any shortcut edited while this plugin was waiting for idle.
	auto& shortcuts = params.getPluginCommandList();
	for (size_t i = 0; i < priorShortcuts.size(); ++i)
		shortcuts[i] = priorShortcuts[i];
	manager.restoreShortcutOrder();
	core._accelerator.updateShortcuts();
	params.reloadContextMenuFromXmlTree(core._mainMenuHandle, manager.getMenuHandle());

	SCNotification notification = {};
	notification.nmhdr.hwndFrom = _hSelf;
	notification.nmhdr.code = NPPN_TBMODIFICATION;
	manager.notify(index, &notification);
	manager.runDelayedStartupCommands(index);
	std::vector<BufferID> buffers;
	for (auto* tabs : {&core._mainDocTab, &core._subDocTab})
		for (size_t i = 0; i < tabs->nbItem(); ++i)
		{
			const BufferID buffer = tabs->getBufferByIndex(i);
			if (std::find(buffers.begin(), buffers.end(), buffer) == buffers.end() &&
				(!buffer->isUntitled() || buffer->isLoadedDirty()))
				buffers.push_back(buffer);
		}
	for (const auto buffer : buffers)
	{
		if (MainFileManager.getBufferIndexByID(buffer) < 0)
			continue;
		const bool restoredSnapshot = buffer->isLoadedDirty();
		// Match NppIO's startup load lifecycle, including unnamed snapshots.
		// A plugin may close a document from a callback, so recheck its lifetime.
		auto send = [&](UINT code, HWND from, uptr_t id) {
			if (MainFileManager.getBufferIndexByID(buffer) < 0)
				return false;
			notification.nmhdr.code = code;
			notification.nmhdr.hwndFrom = from;
			notification.nmhdr.idFrom = id;
			manager.notify(index, &notification);
			return MainFileManager.getBufferIndexByID(buffer) >= 0;
		};
		const uptr_t bufferID = reinterpret_cast<uptr_t>(buffer);
		if (!send(NPPN_FILEBEFORELOAD, _hSelf, 0))
			continue;
		if (restoredSnapshot && !send(NPPN_SNAPSHOTDIRTYFILELOADED, nullptr, bufferID))
			continue;
		if (send(NPPN_FILEBEFOREOPEN, _hSelf, bufferID))
			send(NPPN_FILEOPENED, _hSelf, bufferID);
	}
	notification.nmhdr.hwndFrom = _hSelf;
	notification.nmhdr.code = NPPN_READY;
	notification.nmhdr.idFrom = 0;
	manager.notify(index, &notification);
	notification.nmhdr.code = NPPN_BUFFERACTIVATED;
	notification.nmhdr.idFrom = reinterpret_cast<uptr_t>(core._pEditView->getCurrentBufferID());
	manager.notify(index, &notification);
	core._toolBar.commitDynamicButtons();
	::DrawMenuBar(_hSelf);
	startupTrace("plugin.delayed.ready");
}
