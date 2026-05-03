#!/usr/bin/env python3
import argparse, os, re, shutil, subprocess, sys, tempfile
from datetime import datetime
from pathlib import Path

DEFAULT_IMAGE_MAX_KB = 500


def slugify(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r'[^a-z0-9]+', '-', s)
    s = re.sub(r'-+', '-', s).strip('-')
    return s or 'page'


def run(cmd, cwd=None):
    subprocess.run(cmd, cwd=cwd, check=True)


def pillow_available() -> bool:
    return subprocess.run(
        [sys.executable, '-c', 'import PIL'],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode == 0


def optimize_images(target: Path, max_kb: int, keep_large_images: bool):
    if keep_large_images:
        print('IMAGE_OPTIMIZATION_SKIPPED keep-large-images')
        return
    if max_kb <= 0:
        print('IMAGE_OPTIMIZATION_DISABLED')
        return

    script = Path(__file__).resolve().with_name('optimize_images.py')
    if not script.exists():
        print(f'IMAGE_OPTIMIZATION_SKIPPED optimizer not found: {script}', file=sys.stderr)
        return

    cmd = None
    if pillow_available():
        cmd = [sys.executable, str(script), str(target), '--max-kb', str(max_kb)]
    else:
        uv = shutil.which('uv')
        if uv:
            cmd = [uv, 'run', '--quiet', '--with', 'pillow', 'python3', str(script), str(target), '--max-kb', str(max_kb)]

    if cmd:
        status = subprocess.run(cmd)
        if status.returncode != 0:
            print('IMAGE_OPTIMIZATION_SKIPPED optimizer failed; publishing originals', file=sys.stderr)
        return

    print('IMAGE_OPTIMIZATION_SKIPPED Pillow unavailable; publishing originals', file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--source', required=True, help='Path to built static site folder or single html file')
    ap.add_argument('--slug', required=True)
    ap.add_argument('--date', help='ISO date, default today')
    ap.add_argument('--message', help='Commit message')
    ap.add_argument('--image-max-kb', type=int, default=DEFAULT_IMAGE_MAX_KB, help='Target max size per raster image')
    ap.add_argument('--keep-large-images', action='store_true', help='Publish original images without optimization')
    args = ap.parse_args()

    token = os.environ['GHPAGES_TOKEN']
    repo = os.environ['GHPAGES_REPO']
    branch = os.environ.get('GHPAGES_BRANCH', 'main')
    base_url = os.environ['GHPAGES_PAGES_BASE_URL'].rstrip('/')

    dt = datetime.fromisoformat(args.date) if args.date else datetime.utcnow()
    year = dt.strftime('%Y')
    year_month = dt.strftime('%Y-%m')
    slug = slugify(args.slug)
    rel = Path(year) / year_month / slug

    src = Path(args.source).resolve()
    if not src.exists():
        raise SystemExit(f'source not found: {src}')

    with tempfile.TemporaryDirectory() as td:
        repo_dir = Path(td) / 'repo'
        remote = f'https://x-access-token:{token}@github.com/{repo}.git'
        run(['git', 'clone', '--branch', branch, '--depth', '1', remote, str(repo_dir)])
        target = repo_dir / rel
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True, exist_ok=True)

        if src.is_dir():
            for item in src.iterdir():
                dest = target / item.name
                if item.is_dir():
                    shutil.copytree(item, dest)
                else:
                    shutil.copy2(item, dest)
        else:
            shutil.copy2(src, target / 'index.html')

        optimize_images(target, args.image_max_kb, args.keep_large_images)

        if not (target / 'index.html').exists():
            raise SystemExit('index.html not found in published artifact')

        run(['git', 'config', 'user.name', 'OpenClaw Publisher'], cwd=repo_dir)
        run(['git', 'config', 'user.email', 'publisher@openclaw.local'], cwd=repo_dir)
        run(['git', 'add', str(rel)], cwd=repo_dir)
        msg = args.message or f'publish: {slug} -> {rel.as_posix()}'
        status = subprocess.run(['git', 'diff', '--cached', '--quiet'], cwd=repo_dir)
        if status.returncode == 0:
            print(f'NO_CHANGES {base_url}/{rel.as_posix()}/')
            return
        run(['git', 'commit', '-m', msg], cwd=repo_dir)
        run(['git', 'push', 'origin', branch], cwd=repo_dir)
        print(f'{base_url}/{rel.as_posix()}/')

if __name__ == '__main__':
    main()
