#!/usr/bin/env python3
"""Optimize oversized raster images in-place before publishing."""
from __future__ import annotations

import argparse
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

try:
    from PIL import Image, ImageOps, UnidentifiedImageError
except ImportError as exc:
    raise SystemExit(
        'Pillow is required. Run with: '
        'uv run --with pillow python3 scripts/optimize_images.py <path> --max-kb 500'
    ) from exc


IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp'}
DEFAULT_MAX_KB = 500
DEFAULT_MAX_DIMENSION = 1800
MIN_DIMENSION = 640
QUALITY_VALUES = range(85, 40, -5)
RESAMPLE = getattr(getattr(Image, 'Resampling', Image), 'LANCZOS')


@dataclass
class OptimizeResult:
    status: str
    original_size: int
    final_size: int
    reason: str = ''


def iter_images(root: Path):
    paths = [root] if root.is_file() else root.rglob('*')
    for path in sorted(paths):
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS:
            yield path


def output_format(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in {'.jpg', '.jpeg'}:
        return 'JPEG'
    if ext == '.png':
        return 'PNG'
    return 'WEBP'


def dimension_steps(width: int, height: int, max_dimension: int):
    largest = max(width, height)
    start = min(largest, max_dimension)
    if start <= MIN_DIMENSION:
        return [start]

    steps = []
    current = start
    while current >= MIN_DIMENSION:
        steps.append(current)
        current = int(current * 0.85)
    if steps[-1] != MIN_DIMENSION:
        steps.append(MIN_DIMENSION)
    return steps


def flatten_to_rgb(image: Image.Image) -> Image.Image:
    if image.mode in {'RGBA', 'LA'} or (image.mode == 'P' and 'transparency' in image.info):
        rgba = image.convert('RGBA')
        background = Image.new('RGB', rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.getchannel('A'))
        return background
    return image.convert('RGB')


def save_candidate(source: Image.Image, target: Path, fmt: str, max_dimension: int, quality: int | None):
    image = source.copy()
    if max(image.size) > max_dimension:
        image.thumbnail((max_dimension, max_dimension), RESAMPLE)

    if fmt == 'JPEG':
        flatten_to_rgb(image).save(
            target,
            format='JPEG',
            quality=quality,
            optimize=True,
            progressive=True,
        )
    elif fmt == 'WEBP':
        image.save(target, format='WEBP', quality=quality, method=6)
    else:
        image.save(target, format='PNG', optimize=True, compress_level=9)


def optimize_one(path: Path, max_bytes: int, max_dimension: int) -> OptimizeResult:
    original_size = path.stat().st_size
    if original_size <= max_bytes:
        return OptimizeResult('already_small', original_size, original_size)

    tmp = path.with_name(f'.{path.name}.optimize-tmp{path.suffix}')
    best = path.with_name(f'.{path.name}.optimize-best{path.suffix}')

    try:
        with Image.open(path) as opened:
            opened.load()
            if getattr(opened, 'is_animated', False):
                return OptimizeResult('skipped', original_size, original_size, 'animated image')

            fmt = output_format(path)
            source = ImageOps.exif_transpose(opened)
            qualities: list[int | None] = [None] if fmt == 'PNG' else list(QUALITY_VALUES)
            best_size = original_size

            for max_dim in dimension_steps(*source.size, max_dimension):
                for quality in qualities:
                    save_candidate(source, tmp, fmt, max_dim, quality)
                    candidate_size = tmp.stat().st_size

                    if candidate_size < best_size:
                        shutil.copy2(tmp, best)
                        best_size = candidate_size

                    if candidate_size <= max_bytes:
                        tmp.replace(path)
                        if best.exists():
                            best.unlink()
                        return OptimizeResult('optimized', original_size, candidate_size)

            if best.exists() and best_size < original_size:
                best.replace(path)
                status = 'optimized_over_target' if best_size > max_bytes else 'optimized'
                return OptimizeResult(status, original_size, best_size)

            return OptimizeResult('kept_original', original_size, original_size, 'no smaller candidate')
    except UnidentifiedImageError:
        return OptimizeResult('skipped', original_size, original_size, 'unidentified image')
    except OSError as exc:
        return OptimizeResult('skipped', original_size, original_size, str(exc))
    finally:
        if tmp.exists():
            tmp.unlink()
        if best.exists():
            best.unlink()


def human_size(size: int) -> str:
    return f'{size / 1024:.1f}KB'


def relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def main():
    parser = argparse.ArgumentParser(description='Optimize oversized web images in-place')
    parser.add_argument('path', help='Image file or directory to scan')
    parser.add_argument('--max-kb', type=int, default=DEFAULT_MAX_KB, help='Target max size per image')
    parser.add_argument(
        '--max-dimension',
        type=int,
        default=DEFAULT_MAX_DIMENSION,
        help='Initial long-edge resize limit for oversized images',
    )
    args = parser.parse_args()

    root = Path(args.path).resolve()
    if not root.exists():
        raise SystemExit(f'path not found: {root}')
    if args.max_kb <= 0:
        print('IMAGE_OPTIMIZATION_DISABLED')
        return

    max_bytes = args.max_kb * 1024
    optimized = 0
    over_target = 0
    skipped = 0

    for image_path in iter_images(root):
        result = optimize_one(image_path, max_bytes, args.max_dimension)
        rel = relative(image_path, root if root.is_dir() else root.parent)

        if result.status == 'optimized':
            optimized += 1
            print(f'IMAGE_OPTIMIZED {rel} {human_size(result.original_size)} -> {human_size(result.final_size)}')
        elif result.status == 'optimized_over_target':
            optimized += 1
            over_target += 1
            print(
                f'IMAGE_OPTIMIZED_OVER_TARGET {rel} '
                f'{human_size(result.original_size)} -> {human_size(result.final_size)}',
                file=sys.stderr,
            )
        elif result.status == 'skipped':
            skipped += 1
            print(f'IMAGE_OPTIMIZATION_SKIPPED {rel}: {result.reason}', file=sys.stderr)

    if optimized or over_target or skipped:
        print(f'IMAGE_OPTIMIZATION_DONE optimized={optimized} over_target={over_target} skipped={skipped}')


if __name__ == '__main__':
    main()
