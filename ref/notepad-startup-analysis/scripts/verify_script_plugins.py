"""Run published Python samples and a jN menu callback on isolated fixtures."""
import json
from pathlib import Path
import sys

import win32_probe as win
from verify_plugin_transforms import run

OUT = Path(__file__).resolve().parent


def inspect_virtual_space(_group, label, main, _editor, _pid, _evidence):
    # The published samples change both public editing views, leaving text intact.
    values = [{'hwnd': view['hwnd'], 'options': win.message(view['hwnd'], 2597, timeout=1000)}
        for view in win.windows(parent=main) if view['class'] == 'Scintilla']
    expected = 3 if label == 'Enable Virtual Space' else 1
    assert sum(view['options'] == expected for view in values) >= 2, values
    if expected == 1:
        assert all(view['options'] != 3 for view in values), values
    return values


def main():
    mode = sys.argv[1]
    assert mode in ('default', 'normal')
    runtime = OUT/'runtime'/('startup-idle-'+mode+'44')
    original = 'Owned script-plugin fixture.\r\n'
    reports = [run(runtime, 'python-published-samples', [
        ('Python Script', 'Enable Virtual Space', original),
        ('Python Script', 'Disable Virtual Space', original)], original,
        observe=inspect_virtual_space)]
    # This callback is provided by the original includes.js: Editor.open(this.path).
    script = runtime/'plugins/jN/jN/includes/includes.js'
    published = script.read_text(encoding='utf-8-sig').replace('\r\n', '\n')

    def unchanged_script(_label, value, _original):
        assert value.lstrip('\ufeff').replace('\r\n', '\n') == published

    reports.append(run(runtime, 'jn-open-published-include', [
        ('jNIncludes', 'includes.js', 'Original published include opened')],
        original, validator=unchanged_script, native_menu=True))
    (OUT/('script-plugins-'+mode+'-summary.json')).write_text(json.dumps(reports, indent=2), encoding='utf-8')
    raise SystemExit(0 if all(report['status'] == 'passed' and report.get('process_exit') == 0
        for report in reports) else 1)


if __name__ == '__main__':
    main()
