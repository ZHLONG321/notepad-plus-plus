import hashlib
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent


def limit_build_cpu():
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    kernel.GetProcessAffinityMask.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t), ctypes.POINTER(ctypes.c_size_t)]
    kernel.SetProcessAffinityMask.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    kernel.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    kernel.GetPriorityClass.argtypes = [ctypes.c_void_p]
    process = kernel.GetCurrentProcess()
    available, system = ctypes.c_size_t(), ctypes.c_size_t()
    if not kernel.GetProcessAffinityMask(process, ctypes.byref(available), ctypes.byref(system)):
        raise ctypes.WinError(ctypes.get_last_error())
    selected = available.value & -available.value
    remaining = available.value & ~selected
    selected |= remaining & -remaining
    if not kernel.SetProcessAffinityMask(process, selected):
        raise ctypes.WinError(ctypes.get_last_error())
    if not kernel.SetPriorityClass(process, 0x4000):
        raise ctypes.WinError(ctypes.get_last_error())
    if not kernel.GetProcessAffinityMask(process, ctypes.byref(available), ctypes.byref(system)):
        raise ctypes.WinError(ctypes.get_last_error())
    result = {'priority_class': kernel.GetPriorityClass(process),
              'logical_processors': available.value.bit_count(),
              'affinity_mask': available.value, 'msbuild_nodes': 1,
              'compiler_parallelism': False}
    print('Build CPU limits', json.dumps(result), flush=True)
    return result


def find_visual_studio() -> Path:
    """Find an installed C++ toolchain without using another machine's inventory."""
    vswhere = Path(os.environ['ProgramFiles(x86)']) / 'Microsoft Visual Studio/Installer/vswhere.exe'
    installations = json.loads(subprocess.check_output([
        str(vswhere), '-latest', '-products', '*', '-requires',
        'Microsoft.VisualStudio.Component.VC.Tools.x86.x64', '-format', 'json'], text=True, encoding='utf-8'))
    if not installations:
        raise SystemExit('Install Visual Studio C++ build tools before building.')
    return Path(installations[0]['installationPath'])


def main():
    target = sys.argv[1]
    if target != 'npp':
        raise SystemExit('Plugin source builds are disabled by user instruction; use published DLLs.')
    msbuild = find_visual_studio() / 'MSBuild/Current/Bin/MSBuild.exe'
    project, binary = 'PowerEditor/visual.net/notepadPlus.sln', 'PowerEditor/bin64/notepad++.exe'
    cwd = ROOT / 'src/notepad-plus-plus'
    command = [str(msbuild), project, '/t:Build', '/m:1', '/nr:false', '/nologo', '/v:minimal', '/p:Configuration=Release', '/p:Platform=x64', '/p:MultiProcessorCompilation=false', '/p:CL_MPCount=1']
    command += ['/p:PlatformToolset=v143', '/p:WindowsTargetPlatformVersion=10.0.26100.0', '/p:TreatWarningAsError=false']
    command.extend(sys.argv[2:])
    print('Build started', target, time.strftime('%Y-%m-%d %H:%M:%S'), flush=True)
    print(subprocess.list2cmdline(command), flush=True)
    cpu_limits = limit_build_cpu()
    start = time.perf_counter()
    env = dict(os.environ, _CL_='/utf-8 /WX-')
    result = subprocess.run(command, cwd=cwd, env=env, creationflags=subprocess.BELOW_NORMAL_PRIORITY_CLASS)
    report: dict[str, Any] = {'target': target, 'exit': result.returncode, 'seconds': time.perf_counter() - start, 'command': [os.path.relpath(msbuild, ROOT), *command[1:]], 'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=cwd, text=True).strip(), 'diff': subprocess.check_output(['git', 'diff', '--stat'], cwd=cwd, text=True)}
    if binary and (cwd / binary).exists():
        path = cwd / binary
        report.update(binary=path.relative_to(ROOT).as_posix(), sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    stamp = time.strftime('%Y%m%d-%H%M%S')
    report['cpu_limits'] = cpu_limits
    (OUT / f'build-{target}-{stamp}.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print('Build finished', json.dumps(report), flush=True)
    raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
