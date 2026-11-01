"""Recheck retained native screen frames without launching or controlling an app.

A settled frame must match the final client image in every sampled tile, and
all subsequent frames must also match. This catches late toolbar drawing and
temporary white frames that a single early match can miss. Input is not tested.
"""
import argparse
import hashlib
import json
from pathlib import Path
import time
import zlib

import win32_probe as win

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]


def compare(data, final, width, height, region=None):
    differences, white_tiles = [], []
    x0, y0, x1, y1 = region or (8, 34, width-8, height-8)
    for ty in range(6):
        for tx in range(8):
            left, right = x0+(x1-x0)*tx//8, x0+(x1-x0)*(tx+1)//8
            top, bottom = y0+(y1-y0)*ty//6, y0+(y1-y0)*(ty+1)//6
            changed = white = count = 0
            for y in range(top, bottom, 3):
                for x in range(left, right, 3):
                    p = (y*width+x)*4
                    changed += (abs(data[p]-final[p]) > 12
                                or abs(data[p+1]-final[p+1]) > 12
                                or abs(data[p+2]-final[p+2]) > 12)
                    white += min(data[p], data[p+1], data[p+2]) >= 240
                    count += 1
            differences.append(changed/max(1, count))
            white_tiles.append(white/max(1, count))
    return max(differences), max(white_tiles)


def frames_from_file(path, descriptions):
    decoder = zlib.decompressobj()
    buffer = b''
    index = 0
    with path.open('rb') as stream:
        while chunk := stream.read(65536):
            buffer += decoder.decompress(chunk)
            while index < len(descriptions):
                width, height = descriptions[index]['area'][2:]
                size = width*height*4
                if len(buffer) < size:
                    break
                yield descriptions[index], buffer[:size]
                buffer = buffer[size:]
                index += 1
    assert decoder.eof and index == len(descriptions) and not buffer, 'Incomplete raw frame archive'


def review(result_path):
    print('REVIEW', result_path.relative_to(ROOT), flush=True)
    report = json.loads(result_path.read_text(encoding='utf-8'))
    archive = result_path.with_name('frames.bgra.zlib')
    final = None
    for description, data in frames_from_file(archive, report['frames']):
        final = data
        final_area = description['area']
    assert final is not None
    rows, stable, stable_data = [], None, None
    last_unsettled_data, whitest_data, whitest = None, None, -1
    width, height = final_area[2:]
    for description, data in frames_from_file(archive, report['frames']):
        difference, white = compare(data, final, width, height) if description['area'] == final_area else (1., 1.)
        matches = difference < .001
        if matches and stable is None:
            stable, stable_data = description['ms'], data
        elif not matches:
            stable, stable_data = None, None
        if not matches:
            last_unsettled_data = data
        if white > whitest:
            whitest, whitest_data = white, data
        if not rows:
            win.png(result_path.with_name('review-first-frame.png'), data, width, height)
        rows.append({'ms': description['ms'], 'max_tile_difference': difference,
                     'max_tile_near_white_fraction': white, 'matches_final': matches})
    output = {'source': result_path.relative_to(ROOT).as_posix(),
              'source_result_sha256': hashlib.sha256(result_path.read_bytes()).hexdigest(),
              'reviewer_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'method': '8x6 client tiles; 3px sampling; RGB tolerance 12; each tile <0.001 changed; continuous match through final capture',
              'legacy_first_match_ms': report.get('first_complete_frame_ms'),
              'last_unsettled_frame_ms': max((r['ms'] for r in rows if not r['matches_final']), default=None),
              'continuously_settled_frame_ms': stable,
              'max_near_white_tile_fraction': max(r['max_tile_near_white_fraction'] for r in rows),
              'rows': rows, 'input_test': 'not_performed',
              'limits': 'Sampled visible client pixels only; excludes first 34px title. Does not establish plugin command correctness or input responsiveness.'}
    result_path.with_name('frame-review.json').write_text(json.dumps(output, indent=2), encoding='utf-8')
    if stable_data:
        win.png(result_path.with_name('continuously-settled.png'), stable_data, width, height)
    if last_unsettled_data:
        win.png(result_path.with_name('review-last-unsettled.png'), last_unsettled_data, width, height)
    if whitest_data:
        win.png(result_path.with_name('review-most-white.png'), whitest_data, width, height)
    print('SETTLED', round(stable, 2) if stable else None, 'ms; near-white tile peak', round(output['max_near_white_tile_fraction'], 4), flush=True)
    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('result', nargs='+')
    args = parser.parse_args()
    started = time.perf_counter()
    for name in args.result:
        path = (ROOT/name).resolve()
        assert path.is_relative_to(OUT/'measurements')
        review(path)
    print('FINISHED', round(time.perf_counter()-started, 2), 'seconds', flush=True)


if __name__ == '__main__':
    main()
