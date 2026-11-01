"""Compare two own documents through the published plugin, then clear its result."""
import hashlib
import json
from pathlib import Path
import sys

from print_owned_window import capture
from verify_plugin_transforms import read_editor, run
import win32_probe as win

OUT = Path(__file__).resolve().parent
LEFT = 'shared\r\nred\r\nlast\r\n'
RIGHT = 'shared\r\nblue\r\nlast\r\n'


def inspect_comparison(_group, label, main, _editor, pid, evidence):
    views = [view for view in win.windows(parent=main) if view['class'] == 'Scintilla' and view['visible']]
    if label == 'Set as First to Compare':
        assert win.message(main, 2052, 0, 0, timeout=1000) is not None
        return {'switched_to_other_fixture': True}
    if label == 'Compare':
        assert len(views) == 2, views
        texts = [read_editor(view['hwnd']) for view in views]
        assert set(texts) == {LEFT, RIGHT}, texts
        markers = [{'line0': win.message(view['hwnd'], 2046, 0, timeout=1000),
                    'line1': win.message(view['hwnd'], 2046, 1, timeout=1000)} for view in views]
        assert all(row['line0'] == 0 and row['line1'] not in (None, 0) for row in markers), markers
        render = capture(main, pid, evidence/'actual-difference.png')
        return {'texts': texts, 'markers': markers, 'native_render': render}
    assert label == 'Clear All Compares'
    all_views = [view for view in win.windows(parent=main) if view['class'] == 'Scintilla']
    markers = [win.message(view['hwnd'], 2046, 1, timeout=1000) for view in all_views]
    assert all(marker == 0 for marker in markers), markers
    return {'visible_views': len(views), 'line1_markers_after_clear': markers}


def expected_text(label, value, _original):
    assert value in (LEFT, RIGHT)
    if label == 'Set as First to Compare':
        assert value == RIGHT


def main():
    mode = sys.argv[1]
    assert mode in ('default', 'normal')
    runtime = OUT/'runtime'/('startup-idle-'+mode+'44')
    left = runtime/'plugin-check-compare-left.txt'
    left.write_bytes(LEFT.encode('utf-8'))
    result = run(runtime, 'compare-two-files', [
        ('Compare', 'Set as First to Compare', 'Right fixture selected as first'),
        ('Compare', 'Compare', 'Both views mark only the changed line'),
        ('Compare', 'Clear All Compares', 'Difference markers cleared')], RIGHT,
        validator=expected_text, observe=inspect_comparison, additional_documents=[left])
    assert hashlib.sha256(left.read_bytes()).digest() == hashlib.sha256(LEFT.encode('utf-8')).digest()
    assert (runtime/'plugin-check-compare-two-files.txt').read_bytes() == RIGHT.encode('utf-8')
    (OUT/('compare-plugin-'+mode+'-summary.json')).write_text(json.dumps(result, indent=2), encoding='utf-8')
    raise SystemExit(0 if result['status'] == 'passed' and result.get('process_exit') == 0 else 1)


if __name__ == '__main__':
    main()
