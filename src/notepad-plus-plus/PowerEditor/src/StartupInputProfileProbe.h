#pragma once

#include "StartupTrace.h"

// Diagnostic builds only. Compare installed input profiles in this process
// without explicitly activating a thread manager or changing user defaults.
#if defined(NPP_STARTUP_PROFILE_PROBE)
#include <msctf.h>

class StartupInputProfileProbe
{
public:
	explicit StartupInputProfileProbe(bool editorOnly)
	{
		if (!editorOnly || startupTraceHandle() == INVALID_HANDLE_VALUE)
			return;
		wchar_t requested[80] = {};
		const DWORD length = ::GetEnvironmentVariableW(L"NPP_STARTUP_INPUT_PROFILE", requested, 80);
		if (!length)
			return;
		StartupTraceScope trace(L"inputServices.profile.select");
		// Two GUIDs separated by '|'; the language must already be current.
		HRESULT result = E_INVALIDARG;
		if (length == 77 && requested[38] == L'|')
		{
			requested[38] = 0;
			result = ::CLSIDFromString(requested, &_class);
			if (SUCCEEDED(result))
				result = ::CLSIDFromString(requested + 39, &_profile);
		}
		if (SUCCEEDED(result))
		{
			result = ::CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
			_comInitialized = SUCCEEDED(result);
		}
		if (SUCCEEDED(result))
			result = ::CoCreateInstance(CLSID_TF_InputProcessorProfiles, nullptr, CLSCTX_INPROC_SERVER,
				IID_PPV_ARGS(&_manager));
		if (SUCCEEDED(result))
		{
			_language = LOWORD(reinterpret_cast<ULONG_PTR>(::GetKeyboardLayout(0)));
			result = _manager->ActivateProfile(TF_PROFILETYPE_INPUTPROCESSOR, _language,
				_class, _profile, nullptr, TF_IPPMF_FORPROCESS);
		}
		wchar_t detail[64] = {};
		swprintf_s(detail, L"hr=0x%08lx lang=0x%04x", result, _language);
		startupTrace("inputServices.profile.selectionResult", detail);
		_selected = result == S_OK;
		verify(L"beforePresentation");
	}

	~StartupInputProfileProbe()
	{
		if (!_manager && !_comInitialized)
			return;
		verify(L"afterPresentation");
		StartupTraceScope trace(L"inputServices.profile.release");
		if (_manager)
			_manager->Release();
		if (_comInitialized)
			::CoUninitialize();
	}

private:
	void verify(const wchar_t* stage)
	{
		if (!_manager)
			return;
		TF_INPUTPROCESSORPROFILE active = {};
		const HRESULT result = _manager->GetActiveProfile(GUID_TFCAT_TIP_KEYBOARD, &active);
		const bool matches = _selected && result == S_OK &&
			active.dwProfileType == TF_PROFILETYPE_INPUTPROCESSOR && active.langid == _language &&
			::IsEqualGUID(active.clsid, _class) && ::IsEqualGUID(active.guidProfile, _profile);
		wchar_t className[40] = {}, profileName[40] = {}, detail[192] = {};
		::StringFromGUID2(active.clsid, className, 40);
		::StringFromGUID2(active.guidProfile, profileName, 40);
		swprintf_s(detail, L"%s match=%d hr=0x%08lx %s|%s", stage, matches, result, className, profileName);
		startupTrace("inputServices.profile.verified", detail);
	}

	ITfInputProcessorProfileMgr* _manager = nullptr;
	CLSID _class = {};
	GUID _profile = {};
	LANGID _language = 0;
	bool _comInitialized = false;
	bool _selected = false;
};
#else
struct StartupInputProfileProbe
{
	explicit StartupInputProfileProbe(bool) {}
};
#endif
