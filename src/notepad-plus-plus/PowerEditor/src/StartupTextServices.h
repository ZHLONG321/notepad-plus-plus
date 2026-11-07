#pragma once

#include <msctf.h>
#include "StartupTrace.h"

// Opt-in startup experiment. The existing Scintilla IMM input path stays intact.
// Keep this owner alive until after the editors and plugins have been destroyed.
class StartupTextServices
{
public:
	StartupTextServices() = default;
	StartupTextServices(const StartupTextServices&) = delete;
	StartupTextServices& operator=(const StartupTextServices&) = delete;

	void activateIfRequested()
	{
		wchar_t requested[2] = {};
		if (::GetEnvironmentVariableW(L"NPP_STARTUP_TSF", requested, 2) != 1 || requested[0] != L'1')
			return;

		StartupTraceScope trace(L"inputServices.explicitStartup");
		const HRESULT initialized = ::CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
		logResult(L"CoInitializeEx", initialized);
		_initialized = SUCCEEDED(initialized);
		if (!_initialized)
			return;

		HRESULT result = ::CoCreateInstance(CLSID_TF_ThreadMgr, nullptr, CLSCTX_INPROC_SERVER,
			IID_PPV_ARGS(&_manager));
		logResult(L"CoCreateThreadManager", result);
		if (FAILED(result))
			return;

		TfClientId client = TF_CLIENTID_NULL;
		result = _manager->Activate(&client);
		_active = SUCCEEDED(result);
		logResult(L"Activate", result);
	}

	~StartupTextServices()
	{
		if (_active)
			logResult(L"Deactivate", _manager->Deactivate());
		if (_manager)
			_manager->Release();
		if (_initialized)
			::CoUninitialize();
	}

private:
	static void logResult(const wchar_t* operation, HRESULT result)
	{
		wchar_t detail[128] = {};
		swprintf_s(detail, L"%s=0x%08lx", operation, static_cast<unsigned long>(result));
		startupTrace("inputServices.result", detail);
	}

	ITfThreadMgr* _manager = nullptr;
	bool _initialized = false;
	bool _active = false;
};
