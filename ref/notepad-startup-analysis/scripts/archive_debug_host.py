"""Retain the exact debugger target and matching PDB before the next rebuild."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
repo = ROOT/'src/notepad-plus-plus'
binary = repo/'PowerEditor/bin64/notepad++.exe'
digest = hashlib.sha256(binary.read_bytes()).hexdigest()
destination = OUT/'artifacts'/digest
destination.mkdir(exist_ok=True)
items = {}
for name in ('notepad++.exe', 'npp.pdb'):
    source = binary.parent/name
    saved = destination/name
    value = hashlib.sha256(source.read_bytes()).hexdigest()
    if saved.exists():
        assert hashlib.sha256(saved.read_bytes()).hexdigest()==value
    else:
        shutil.copy2(source, saved)
    items[name] = dict(sha256=value, bytes=source.stat().st_size)
revision = subprocess.check_output(['git', 'rev-parse','HEAD'], cwd=repo, text=True).strip()
(destination/'debug-build.json').write_text(json.dumps(dict(source_revision=revision, files=items), indent=2), encoding='utf-8')
print('ARCHIVED', revision, digest, flush=True)
