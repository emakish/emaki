#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Dither variant A strips to RGB8, retaining source hashes and fade/seam proofs."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess

import cv2
import numpy as np

cv2.setNumThreads(1)
NOTICE = b'Copyright (C) 2026 Artur Yakymenko; SPDX-License-Identifier: GPL-3.0-or-later'
FILTER = 'format=gbrp16le,zscale=dither=error_diffusion:rangein=full:range=full,format=gbrp,format=rgb24'


def digest(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def ffmpeg(source, target, filters, compression=False):
    command = ['ffmpeg', '-nostdin', '-v', 'error', '-threads', '1', '-i', str(source),
               '-frames:v', '1', '-vf', filters, '-filter_threads', '1', '-threads', '1']
    if compression:
        command += ['-compression_level', '9']
    subprocess.run(command + [str(target)], check=True)


def seam(a):
    """Same column MAE and local 200-column baseline as strips8k_runner."""
    height, width = a.shape[:2]
    steps = np.zeros(width, np.float64)
    divisor = 257 if a.dtype == np.uint16 else 1
    for y in range(0, height, 64):
        b = a[y:y + 64].astype(np.float32) / divisor
        steps[1:] += np.abs(b[:, 1:] - b[:, :-1]).sum((0, 2))
        steps[0] += np.abs(b[:, 0] - b[:, -1]).sum()
    steps /= height * 3
    local = np.concatenate((steps[-100:], steps[1:101])).mean()
    return {'mae_255': float(steps[0]), 'ordinary_mean': float(steps[1:].mean()),
            'ordinary_p95': float(np.percentile(steps[1:], 95)),
            'ordinary_percentile': float((steps[1:] <= steps[0]).mean() * 100),
            'local_200_mean': float(local), 'ratio_to_local': float(steps[0] / max(local, 1e-9))}


def save_png(path, a):
    if not cv2.imwrite(str(path), a, [cv2.IMWRITE_PNG_COMPRESSION, 6]):
        raise ValueError(f'Cannot write {path}')


def proof(before, after, directory, theme):
    h, w = before.shape[:2]
    x0, x1 = round(w * .38), round(w * .62)
    a, b = before[h // 2:, x0:x1], after[h // 2:, x0:x1]
    save_png(directory / f'{theme}-fade-before16.png', a)
    save_png(directory / f'{theme}-fade-after8.png', b)
    save_png(directory / f'{theme}-wrap-before16.png', np.concatenate((before[:, -600:], before[:, :600]), axis=1))
    save_png(directory / f'{theme}-wrap-after8.png', np.concatenate((after[:, -600:], after[:, :600]), axis=1))
    source_values = a.astype(np.float32) / 257
    error = b.astype(np.float32) - source_values
    block = 32
    bh, bw = error.shape[0] // block, error.shape[1] // block
    average = error[:bh * block, :bw * block].reshape(bh, block, bw, block, 3).mean((1, 3))
    naive = np.rint(source_values) - source_values
    naive_average = naive[:bh * block, :bw * block].reshape(bh, block, bw, block, 3).mean((1, 3))
    return {'crop': [x0, h // 2, x1, h], 'mae_255': float(np.abs(error).mean()),
            'max_error_255': float(np.abs(error).max()), 'mean_error_255': float(error.mean()),
            'different_from_rounding_fraction': float((b != np.rint(source_values)).mean()),
            'block32_rms_255': float(np.sqrt(np.mean(average ** 2))),
            'block32_max_255': float(np.abs(average).max()),
            'rounding_block32_rms_255': float(np.sqrt(np.mean(naive_average ** 2)))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--renders', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--proofs', type=Path, required=True)
    parser.add_argument('--previews', type=Path)
    parser.add_argument('--catalog', type=Path, default=Path(__file__).resolve().parents[2] / 'themes/catalog.json')
    args = parser.parse_args()
    rows = [row for row in json.loads(args.catalog.read_text())['styles'] if row['id'] != 'emaki']
    inputs = [(row['id'], args.renders / f"s8k-{row['id']}-2160.png") for row in rows]
    if len(inputs) != 18 or not all(source.is_file() for _, source in inputs):
        raise ValueError('All eighteen catalog variant A sources are required')
    # New destinations only; originals and previous deliveries are never overwritten.
    args.output.mkdir(parents=True, exist_ok=False)
    args.proofs.mkdir(parents=True, exist_ok=False)
    records = []
    for theme, source in inputs:
        source_hash = digest(source)
        before = cv2.imread(str(source), cv2.IMREAD_UNCHANGED)
        if before is None or before.dtype != np.uint16 or before.shape != (2160, 13512, 3):
            raise ValueError(f'Expected 13512x2160 RGB16: {source}')
        target = args.output / f'{theme}-2160.png'
        ffmpeg(source, target, FILTER, compression=True)
        after = cv2.imread(str(target), cv2.IMREAD_UNCHANGED)
        if after is None or after.dtype != np.uint8 or after.shape != before.shape:
            raise ValueError(f'Expected unchanged dimensions and RGB8: {target}')
        if after.shape[0] * after.shape[1] > 48_000_000 or target.stat().st_size > 32 * 1024 ** 2:
            raise ValueError(f'Publish limits exceeded: {target}')
        record = {'id': theme, 'source_sha256': source_hash, 'sha256': digest(target),
                  'width': 13512, 'height': 2160, 'bit_depth': 8, 'bytes': target.stat().st_size,
                  'seam_before': seam(before), 'seam_after': seam(after),
                  'fade': proof(before, after, args.proofs, theme)}
        # Quantization must not introduce a colour shift or a new wrap discontinuity.
        if record['fade']['max_error_255'] > 1.01 or record['fade']['block32_max_255'] > .05:
            raise ValueError(f'Fade quantization error exceeded one code value or block tolerance: {target}')
        if record['seam_after']['mae_255'] > record['seam_before']['mae_255'] + 1.01:
            raise ValueError(f'Wrap discontinuity exceeds quantization tolerance: {target}')
        if digest(source) != source_hash:
            raise ValueError(f'Source changed during conversion: {source}')
        if args.previews:
            preview = args.previews / f'{theme}.jpg'
            temporary = args.proofs / f'{theme}-thumbnail.jpg'
            ffmpeg(target, temporary, r'crop=min(iw\,ih*16/9):ih,scale=320:180')
            jpeg = temporary.read_bytes()
            if not jpeg.startswith(b'\xff\xd8'):
                raise ValueError('Expected JPEG preview')
            preview.write_bytes(jpeg[:2] + b'\xff\xfe' + struct.pack('>H', len(NOTICE) + 2) + NOTICE + jpeg[2:])
            temporary.unlink()
            record['preview_sha256'] = digest(preview)
        records.append(record)
        (args.proofs / 'measurements.json').write_text(json.dumps({
            'copyright': NOTICE.decode(), 'filter': FILTER,
            'ffmpeg': subprocess.check_output(['ffmpeg', '-version'], text=True).splitlines()[0],
            'strips': records}, indent=2) + '\n')
        print(json.dumps(record), flush=True)
        del before, after
    (args.output / 'SHA256SUMS').write_text('# ' + NOTICE.decode() + '\n' + ''.join(
        f"{row['sha256']}  {row['id']}-2160.png\n" for row in records))


if __name__ == '__main__':
    main()
