"""Build the small native window-frame observer with the installed SDK."""
import os
from pathlib import Path
import subprocess
import time
from build import find_visual_studio, limit_build_cpu

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
vs = find_visual_studio()
vc_version = (vs/'VC/Auxiliary/Build/Microsoft.VCToolsVersion.default.txt').read_text(encoding='utf-8').strip()
vc = vs/'VC/Tools/MSVC'/vc_version
sdk = Path(os.environ['ProgramFiles(x86)'])/'Windows Kits/10'
includes = [vc/'include', *[sdk/'Include/10.0.26100.0'/part for part in ['ucrt','shared','um','winrt','cppwinrt']]]
libs = [vc/'lib/x64', sdk/'Lib/10.0.26100.0/ucrt/x64', sdk/'Lib/10.0.26100.0/um/x64']
assert all(path.is_dir() for path in includes+libs)
command = [str(vc/'bin/Hostx64/x64/cl.exe'), '/nologo', '/std:c++17', '/EHsc', '/O2', '/MD', '/LD',
    '/utf-8', '/DWIN32_LEAN_AND_MEAN', '/DNOMINMAX', 'window_frames.cpp', '/Fowindow_frames.obj',
    '/Fewindow_frames.dll', *['/I'+str(path) for path in includes], '/link',
    *['/LIBPATH:'+str(path) for path in libs], 'd3d11.lib', 'dxgi.lib', 'windowsapp.lib', 'ole32.lib']
print('Compiling native HWND frame observer', flush=True)
limit_build_cpu()
started = time.perf_counter()
with (OUT/'build-window-frames.log').open('wb') as log:
    result = subprocess.run(command, cwd=OUT, stdout=log, stderr=log)
print((OUT/'build-window-frames.log').read_text(encoding='utf-8', errors='replace'), flush=True)
print('Build exit', result.returncode, 'seconds', round(time.perf_counter()-started, 3), flush=True)
raise SystemExit(result.returncode)
