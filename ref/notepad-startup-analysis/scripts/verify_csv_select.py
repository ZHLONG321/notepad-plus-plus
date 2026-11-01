"""Execute a real CsvQuery SELECT and read its grid through Windows accessibility."""
import argparse
import ctypes as C
import json
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any
import xml.etree.ElementTree as ET

from print_owned_window import capture
from test_resources import limit_test_resources
from verify_plugin_transforms import run
import win32_probe as win

out = Path(__file__).resolve().parent
parser = argparse.ArgumentParser()
parser.add_argument('mode', choices=['default', 'normal'])
args = parser.parse_args()
limit_test_resources()
runtime = out/'runtime'/('csv-select-'+args.mode+'-'+time.strftime('%Y%m%d-%H%M%S'))
source = out/'runtime'/('startup-idle-default44' if args.mode=='default' else 'startup-idle-normal44')
print('COPY', source.name, runtime.name, flush=True)
shutil.copytree(source, runtime)
query = 'SELECT name, CAST(id AS INTEGER)*10 AS score FROM THIS WHERE CAST(id AS INTEGER)>=2 ORDER BY score DESC'
original = 'id,name\n1,Alice\n2,Bob\n3,Carol\n'

def read_accessibility(hwnd, pid, destination):
    with destination.open('wb') as stream:
        subprocess.run([str(out/'read_owned_accessibility.exe'), str(hwnd), str(pid)],
            stdout=stream, stderr=subprocess.STDOUT, timeout=10, check=True)
    return ET.fromstring(destination.read_text(encoding='utf-8-sig'))

def observe(group, label, main, editor, pid, evidence):
    controls = win.windows(pid, main)
    panel = next(item['hwnd'] for item in controls if item['visible'] and
        item['class'].startswith('WindowsForms10.Window') and win.text(item['hwnd'])=='QueryWindow')
    elements = win.windows(pid, panel)
    edit = next(item['hwnd'] for item in elements if item['visible'] and item['class'].startswith('WindowsForms10.EDIT'))
    button = next(item['hwnd'] for item in elements if item['visible'] and win.text(item['hwnd'])=='&Execute')
    before = read_accessibility(panel, pid, evidence/'before-query.xml')
    elements = win.windows(pid, panel)
    edit = next(item['hwnd'] for item in elements if item['visible'] and item['class'].startswith('WindowsForms10.EDIT'))
    button = next(item['hwnd'] for item in elements if item['visible'] and win.text(item['hwnd'])=='&Execute')
    assert win.U.IsWindow(edit), 'Query textbox handle no longer exists'
    text = C.create_unicode_buffer(query)
    text_address = C.cast(text, C.c_void_p).value
    assert text_address is not None
    C.set_last_error(0)
    set_result = win.message(edit, 0x0c, 0, text_address, timeout=2000)
    set_error = C.get_last_error()
    buffer = C.create_unicode_buffer(1024)
    buffer_address = C.cast(buffer, C.c_void_p).value
    assert buffer_address is not None
    C.set_last_error(0)
    length = win.message(edit, 0x0d, len(buffer), buffer_address, timeout=2000)
    print('QUERY_TEXT', json.dumps(dict(set_result=set_result, set_error=set_error,
        length=length, get_error=C.get_last_error(), actual=buffer.value, hwnd_exists=bool(win.U.IsWindow(edit)))), flush=True)
    assert length==len(query) and buffer.value==query, (set_result, length, buffer.value)
    started = time.perf_counter()
    assert win.message(button, 0xf5, timeout=3000) is not None
    elapsed = (time.perf_counter()-started)*1000
    after = read_accessibility(panel, pid, evidence/'after-query.xml')
    observation: dict[str, Any] = dict(query=query, execution_ms=elapsed, before=ET.tostring(before, encoding='unicode'),
        after=ET.tostring(after, encoding='unicode'))
    (evidence/'query-observation.json').write_text(json.dumps(observation, indent=2), encoding='utf-8')
    observation['native_image'] = capture(main, pid, evidence/'query-result.png')
    print('GRID', observation['after'], flush=True)
    result = next(node for node in after.iter('control') if node.get('id')=='dataGrid')
    rows = [node for node in result if len(node) and all(child.get('type')!='ControlType.Header' for child in node)]
    values = [[cell.get('value') for cell in row] for row in rows]
    assert values==[['Carol', '30'], ['Bob', '20']], values
    observation['grid_values'] = values
    return observation

report = run(runtime, 'csv-select', [('CsvQuery', 'Toggle query window', original)], original, observe=observe)
print('CSV_SELECT', args.mode, report['status'], flush=True)
raise SystemExit(0 if report['status']=='passed' and report.get('process_exit')==0 else 1)
