"""Verified official releases, stored in immutable per-version runtime folders."""
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import tempfile
import time
from urllib.request import Request, urlopen
import zipfile

SOURCES = (
 ('https://gitee.com/api/v5/repos/chenyipier/cy-edit-skills/releases?per_page=20', 'https://gitee.com/chenyipier/cy-edit-skills'),
 ('https://api.github.com/repos/chenyipier-ui/cy-edit-skills/releases?per_page=20', 'https://github.com/chenyipier-ui/cy-edit-skills'),
)

def version(value):
    if not re.fullmatch(r'v?\d+\.\d+\.\d+', value):
        raise ValueError('Unsupported release version')
    return tuple(map(int, value.lstrip('v').split('.')))

def get(url):
    with urlopen(Request(url, headers={'User-Agent': 'cy-edit-auto-update'}), timeout=4) as r:
        data = r.read(32 * 1024 * 1024 + 1)
    if len(data) > 32 * 1024 * 1024:
        raise ValueError('Download too large')
    return data

def unpack(data, checksum, destination, name, tag):
    if hashlib.sha256(data).hexdigest() != checksum:
        raise ValueError('Checksum mismatch')
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        items = archive.infolist()
        if len(items) > 2000 or sum(x.file_size for x in items) > 64 * 1024 * 1024:
            raise ValueError('Archive too large')
        seen = set()
        for item in items:
            path = PurePosixPath(item.filename)
            if (path.is_absolute() or '..' in path.parts or '\\' in item.filename
                    or ':' in item.filename or not path.parts or path.parts[0] != name
                    or stat.S_ISLNK(item.external_attr >> 16) or item.filename in seen):
                raise ValueError('Unsafe archive')
            seen.add(item.filename)
        for required in ('SKILL.md', 'VERSION', 'LICENSE', 'scripts/editor.py', 'scripts/cy_auto_update.py'):
            if f'{name}/{required}' not in seen:
                raise ValueError('Incomplete skill package')
        if archive.read(f'{name}/VERSION').decode().strip() != tag.lstrip('v'):
            raise ValueError('Version mismatch')
        archive.extractall(destination)
        for item in items:
            if item.filename.endswith(('.sh', '.command')):
                (destination / item.filename).chmod(0o755)


def resolve(root, force=False):
    root = Path(root).resolve()
    if os.environ.get('CY_EDIT_AUTO_UPDATE') == '0':
        return root
    name = root.name
    if name not in ('cy-edit-shortvideo', 'cy-edit-longvideo'):
        return root
    base = Path(os.environ.get('CY_EDIT_UPDATE_DIR', str(Path.home() / '.cy-edit' / 'updates'))) / name
    best = root
    lock = base / '.lock'
    acquired = False
    try:
        current = version((root / 'VERSION').read_text().strip())
        base.mkdir(parents=True, exist_ok=True)
        for ready in base.glob('v*/.ready'):
            candidate = ready.parent / name
            try:
                v = version((candidate / 'VERSION').read_text().strip())
                if v > current and (candidate / 'scripts/editor.py').is_file():
                    best, current = candidate, v
            except (OSError, ValueError):
                continue
        stamp = base / '.checked'
        if not force and stamp.exists() and time.time() - stamp.stat().st_mtime < 3600:
            return best
        # Exclusive lock; a crash cannot disable updates permanently.
        if lock.exists() and time.time() - lock.stat().st_mtime > 600:
            lock.rmdir()
        lock.mkdir()
        acquired = True
        for api, source in SOURCES:
            try:
                releases = json.loads(get(api))
                valid = [r for r in releases if not r.get('draft') and re.fullmatch(r'v\d+\.\d+\.\d+', r.get('tag_name', ''))]
                latest = max(valid, key=lambda r: version(r['tag_name']))
                tag = latest['tag_name']
                if version(tag) <= current:
                    continue
                filename = f'{name}-{tag}.zip'
                names = {a['name'] for a in latest.get('assets', [])}
                if not {filename, 'SHA256SUMS'} <= names:
                    continue
                prefix = f'{source}/releases/download/{tag}/'
                manifest = get(prefix + 'SHA256SUMS').decode()
                matches = re.findall(r'^([a-f0-9]{64})  ' + re.escape(filename) + r'$', manifest, re.M)
                if len(matches) != 1:
                    raise ValueError('Invalid checksum manifest')
                with tempfile.TemporaryDirectory(prefix='.stage-', dir=base) as stage:
                    staging = Path(stage)
                    unpack(get(prefix + filename), matches[0], staging, name, tag)
                    (staging / '.ready').write_text(matches[0])
                    destination = base / tag
                    if destination.exists():
                        raise ValueError('Unverified existing cache')
                    staging.rename(destination)
                    best = destination / name
                print(f'[CY] 自动升级到 {tag}：{best}', file=sys.stderr)
                break
            except Exception:
                continue
        stamp.touch()
    except Exception:
        pass  # Offline, permission errors and concurrent updates keep a usable version.
    finally:
        if acquired:
            try:
                lock.rmdir()
            except OSError:
                pass
    return best


def start(root):
    root = Path(root).resolve()
    if len(sys.argv) == 2 and sys.argv[1] == 'update-root':
        print(resolve(root, force=True))
        raise SystemExit(0)
    if ('--offline' in sys.argv or '--help' in sys.argv or '-h' in sys.argv
            or os.environ.get('CY_EDIT_UPDATE_ACTIVE') == '1'):
        return
    target = resolve(root)
    if target != root:
        env = dict(os.environ, CY_EDIT_UPDATE_ACTIVE='1')
        argv = [sys.executable, str(target / 'scripts/editor.py'), *sys.argv[1:]]
        if os.name == 'nt':  # Windows exec does not replace the process; wait and pass the exit code back.
            import subprocess
            raise SystemExit(subprocess.call(argv, env=env))
        os.execve(sys.executable, argv, env)
