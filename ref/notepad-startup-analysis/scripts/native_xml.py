"""Write isolated fixtures in the old wide TinyXML hexadecimal-entity format."""
from __future__ import annotations
from pathlib import Path
import re
import xml.etree.ElementTree as ET


def write_xml(tree: ET.ElementTree[ET.Element[str]], path: Path) -> None:
    root = tree.getroot()
    assert root is not None
    data = ET.tostring(root, encoding='us-ascii', xml_declaration=True)
    # TinyXML in 8.1.4 recognizes hexadecimal references, not decimal ones.
    data = re.sub(rb'&#([0-9]+);', lambda match: ('&#x%04X;' % int(match[1])).encode('ascii'), data)
    path.write_bytes(data)
