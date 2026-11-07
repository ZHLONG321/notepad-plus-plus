// Diagnostic only: redirect the QQ TSF engine load inside this isolated process.
#pragma once
#include "StartupTrace.h"
#include <windows.h>
#include <cstdio>
#include <cstring>
#include <cwchar>
namespace qqredirect {
static wchar_t proxyPath[32768] = {};
static HMODULE tsf;
static ULONG_PTR* slot;
static ULONG_PTR original;
static unsigned redirects = 0;
inline HMODULE WINAPI load(LPCWSTR name) {
    const wchar_t* leaf = name ? wcsrchr(name,L'\\') : nullptr;
    if (name && !_wcsicmp(leaf ? leaf+1 : name,L"QQPinyin.ime")) {
        ++redirects;
        return LoadLibraryW(proxyPath);
    }
    return LoadLibraryW(name);
}
inline bool install() {
    wchar_t tsfPath[32768] = {};
    if (!GetEnvironmentVariableW(L"QQ_TEST_PROXY_PATH",proxyPath,_countof(proxyPath)) ||
        !GetEnvironmentVariableW(L"QQ_TEST_TSF_PATH",tsfPath,_countof(tsfPath))) return false;
    if (GetModuleHandleW(L"QQPinyin.ime") || GetModuleHandleW(L"QQPinyinOriginal.dll")) return false;
    tsf=LoadLibraryW(tsfPath);
    if (!tsf || GetModuleHandleW(L"QQPinyin.ime") || GetModuleHandleW(L"QQPinyinOriginal.dll")) return false;
    const auto base=reinterpret_cast<BYTE*>(tsf);
    const auto dos=reinterpret_cast<IMAGE_DOS_HEADER*>(base);
    const auto nt=reinterpret_cast<IMAGE_NT_HEADERS64*>(base+dos->e_lfanew);
    const auto dir=nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT];
    for (auto d=reinterpret_cast<IMAGE_IMPORT_DESCRIPTOR*>(base+dir.VirtualAddress);d->Name;++d) {
        if (!d->OriginalFirstThunk) continue;
        auto names=reinterpret_cast<IMAGE_THUNK_DATA64*>(base+d->OriginalFirstThunk);
        auto slots=reinterpret_cast<IMAGE_THUNK_DATA64*>(base+d->FirstThunk);
        for (;names->u1.AddressOfData;++names,++slots) {
            if (IMAGE_SNAP_BY_ORDINAL64(names->u1.Ordinal)) continue;
            const auto name=reinterpret_cast<IMAGE_IMPORT_BY_NAME*>(base+names->u1.AddressOfData)->Name;
            if (strcmp(name,"LoadLibraryW")) continue;
            if (slot) return false;
            slot=&slots->u1.Function;
            original=reinterpret_cast<ULONG_PTR>(GetProcAddress(GetModuleHandleW(L"kernel32.dll"),"LoadLibraryW"));
            if (*slot!=original) return false;
        }
    }
    if (!slot) return false;
    DWORD protection,ignored;
    if (!VirtualProtect(slot,sizeof(*slot),PAGE_READWRITE,&protection)) return false;
    const bool replaced=reinterpret_cast<ULONG_PTR>(InterlockedCompareExchangePointer(
        reinterpret_cast<PVOID volatile*>(slot),reinterpret_cast<PVOID>(&load),reinterpret_cast<PVOID>(original)))==original;
    const bool protectedAgain=VirtualProtect(slot,sizeof(*slot),protection,&ignored)!=FALSE;
    return replaced && protectedAgain;
}
inline bool restore() {
    if (!slot) return false;
    DWORD protection, ignored;
    if (!VirtualProtect(slot,sizeof(*slot),PAGE_READWRITE,&protection)) return false;
    const bool restored=reinterpret_cast<ULONG_PTR>(InterlockedCompareExchangePointer(
        reinterpret_cast<PVOID volatile*>(slot),reinterpret_cast<PVOID>(original),
        reinterpret_cast<PVOID>(&load)))==reinterpret_cast<ULONG_PTR>(&load);
    return VirtualProtect(slot,sizeof(*slot),protection,&ignored)!=FALSE && restored;
}
struct Scope {
    bool valid = true;
    bool installed = false;
    Scope() {
        // Without an explicit per-process override, retain the installed IME.
        if (!GetEnvironmentVariableW(L"QQ_TEST_PROXY_PATH",nullptr,0) &&
            !GetEnvironmentVariableW(L"QQ_TEST_TSF_PATH",nullptr,0)) return;
        StartupTraceScope trace(L"inputServices.qqProxy.install");
        valid=installed=install();
        startupTrace("inputServices.qqProxy.installed",valid?L"1":L"0");
    }
    ~Scope() {
        if (installed) {
            wchar_t count[32]={};
            swprintf_s(count,L"%u",redirects);
            startupTrace("inputServices.qqProxy.redirectCount",count);
            startupTrace("inputServices.qqProxy.restored",restore()?L"1":L"0");
        }
    }
};
}
