"""Exercise original JSTool, XML Tools and URL Plugin through native host commands."""
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET
from verify_plugin_transforms import run

OUT = Path(__file__).resolve().parent


def json_minified(_label, value, original):
    assert json.loads(value) == json.loads(original)
    assert len(value.strip()) < len(original.strip())
    assert '\n' not in value.strip()


def xml_linearized(_label, value, original):
    before, after = ET.fromstring(original), ET.fromstring(value)
    assert before.tag == after.tag == 'root'
    assert [(child.tag, child.attrib, child.text) for child in before] == [
        (child.tag, child.attrib, child.text) for child in after]
    assert '\n' not in value.strip()


def main():
    runtime = (OUT/'runtime'/sys.argv[1]).resolve()
    assert runtime.is_relative_to(OUT/'runtime')
    selected = set(sys.argv[2:]) or {'jstool', 'xml', 'url'}
    assert selected <= {'jstool', 'xml', 'url'}
    results = []
    if 'jstool' in selected:
        results.append(run(runtime, 'jstool', [('JSTool', 'JSMin', 'Equivalent compact JSON')],
                           '{ "answer" : 42, "items" : [ 1, 2 ] }', json_minified))
    if 'xml' in selected:
        results.append(run(runtime, 'xml', [('XML Tools', 'Linearize', 'Equivalent single-line XML')],
                           '<root>\n  <item value="42">text</item>\n</root>', xml_linearized))
    if 'url' in selected:
        results.append(run(runtime, 'url', [('URL Plugin', 'Encode URL', 'Hello%20World'),
                                           ('URL Plugin', 'Decode URL', 'Hello World')], 'Hello World'))
    raise SystemExit(0 if all(r['status'] == 'passed' and r['process_exit'] == 0 for r in results) else 1)


if __name__ == '__main__':
    main()
