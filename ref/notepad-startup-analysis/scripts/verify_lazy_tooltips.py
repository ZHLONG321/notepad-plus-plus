"""Exercise lazy tooltip lookup in a fresh owned background process."""
import ctypes as C
from ctypes import wintypes as W
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any

from background_zorder import inspect
from foreground_events import Observer
from print_owned_window import capture
import win32_probe as win
from window_surface import uncloaked

out = Path(__file__).resolve().parent
runtime = out/'runtime/startup-idle-default44'
folder = out/'measurements'/('lazy-tooltip-'+time.strftime('%Y%m%d-%H%M%S'))
folder.mkdir()
observer = Observer()
report: dict[str, Any] = dict(status='incomplete', actions=[], exe_sha256=hashlib.sha256((runtime/'notepad++.exe').read_bytes()).hexdigest())
app = hwnd = original_rect = None

def wait(seconds):
    deadline = time.perf_counter()+seconds
    while time.perf_counter()<deadline:
        observer.pump()
        assert app is None or app.poll() is None
        time.sleep(.01)

print('START', folder.name, flush=True)
try:
    app = subprocess.Popen([str(runtime/'notepad++.exe'), '-multiInst', '-noActivate', '-settingsDir=settings'],
        cwd=runtime, env=dict(os.environ, APPDATA=str(runtime/'appdata'), LOCALAPPDATA=str(runtime/'localappdata'),
            TEMP=str(runtime/'temp'), TMP=str(runtime/'temp'), NPP_STARTUP_TRACE=str(folder/'startup.tsv')))
    report['pid'] = app.pid
    deadline = time.perf_counter()+6
    while time.perf_counter()<deadline:
        observer.pump()
        mains = [w for w in win.windows(app.pid) if w['class']=='Notepad++' and w['visible'] and uncloaked(w['hwnd'])]
        if mains:
            hwnd = mains[0]['hwnd']
            break
        time.sleep(.01)
    assert hwnd
    wait(2)
    report['background'] = inspect(app.pid)
    assert report['background']['passed']
    original_rect = win.rect(hwnd)
    assert win.U.SetWindowPos(hwnd, W.HWND(1), -3000, -3000, 0, 0, 0x1|0x10|0x200)
    children = win.windows(app.pid, hwnd)
    owners = [w for w in children if w['class'] in ('ToolbarWindow32', 'SysTreeView32') and w['visible']]
    assert len(owners)>=3
    for owner in owners:
        control = owner['hwnd']
        begin = time.perf_counter()
        assert win.message(control, 0x200, 0, (10<<16)|10, timeout=2000) is not None
        elapsed = (time.perf_counter()-begin)*1000
        message = 0x423 if owner['class']=='ToolbarWindow32' else 0x1119
        tooltip = win.message(control, message, timeout=2000)
        assert tooltip and win.text(tooltip, True)=='tooltips_class32'
        tool_count = win.message(tooltip, 0x40d, timeout=1000)
        win.message(tooltip, 0x422, timeout=1000)
        wait(.15)
        item: dict[str, Any] = dict(owner=owner, tooltip=tooltip, first_mouse_message_ms=elapsed,
            tool_count=tool_count, visible=bool(win.U.IsWindowVisible(tooltip)), rect=win.rect(tooltip), text=win.text(tooltip))
        if item['visible'] and item['rect'][2]>0 and item['rect'][3]>0:
            item['native_image'] = capture(tooltip, app.pid, folder/('tooltip-'+str(control)+'.png'))
        report['actions'].append(item)
        win.message(tooltip, 0x41c, timeout=1000)
        win.message(control, 0x2a3, timeout=1000)
        print('TOOLTIP', owner['class'], json.dumps(item), flush=True)
    report['status'] = 'lookup_and_targeted_mouse_messages_returned'
finally:
    if hwnd and original_rect:
        assert win.U.SetWindowPos(hwnd, W.HWND(1), *original_rect, 0x10|0x200)
    if hwnd:
        win.U.PostMessageW(hwnd, 0x10, 0, 0)
    if app:
        report['exit'] = app.wait(timeout=7)
    observer.pump()
    report['foreground_events'] = [e for e in observer.events if app and e['pid']==app.pid]
    observer.close()
    (folder/'result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print('RESULT', report['status'], report.get('exit'), report['foreground_events'], flush=True)
assert report['exit']==0 and not report['foreground_events']
