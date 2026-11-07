#pragma once

#include "StartupTrace.h"

// The explicit-Activate experiment did not preserve Chinese composition in
// Scintilla's IMM editor. Activating TSF without integrating a document context
// and keyboard handling is not a supported replacement for native IMM startup.
// Keep old experiment launchers observable, but always use native input services.
inline void recordRejectedStartupTextServicesExperiment()
{
	wchar_t requested[2] = {};
	if (::GetEnvironmentVariableW(L"NPP_STARTUP_TSF", requested, 2) == 1 && requested[0] == L'1')
		startupTrace("inputServices.experiment.rejected", L"Native IMM startup retained; explicit TSF activation failed composition validation");
}
