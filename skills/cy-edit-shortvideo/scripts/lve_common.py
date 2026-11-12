"""Shared local storage and process helpers. Python 3.10+."""
from __future__ import annotations
import contextlib
import hashlib
import json
import os
import platform
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

VERSION = '2.4.0'

class UserError(Exception):
    pass

def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')

def state_dir(value=None):
    if value:
        return Path(value).expanduser().resolve()
    configured = os.environ.get('CY_EDIT_LONGVIDEO_DATA') or os.environ.get('LONG_VIDEO_EDITOR_DATA')
    if configured:
        return Path(configured).expanduser().resolve()
    if platform.system() == 'Windows':
        base = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local'))
    elif platform.system() == 'Darwin':
        base = Path.home() / 'Library' / 'Application Support'
    else:
        base = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local' / 'share'))
    current = base / 'cy-edit-longvideo'
    legacy = base / 'long-video-editor'
    # Preserve existing user experience/runtime without moving a virtualenv.
    return legacy if not current.exists() and legacy.is_dir() else current

def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (OSError, ValueError) as exc:
        raise UserError(f'无法读取 JSON：{path}：{exc}') from exc

def atomic_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.' + path.name, suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

def write_json(path, data):
    atomic_text(path, json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + '\n')

def sha_file(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()

def digest(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()

def require(condition, message):
    if not condition:
        raise UserError(message)

def identifier(value):
    require(isinstance(value, str) and re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}', value),
            'ID 仅使用 1–64 位字母、数字、下划线或连字符，不能包含路径')
    reserved = {'CON','PRN','AUX','NUL'} | {f'COM{i}' for i in range(1,10)} | {f'LPT{i}' for i in range(1,10)}
    require(value.upper() not in reserved, 'ID 不能使用 Windows 保留名称')
    return value

def run(args, timeout=None):
    try:
        result = subprocess.run([str(x) for x in args], capture_output=True, text=True,
                                encoding='utf-8', errors='replace', timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UserError(f'运行失败：{args[0]}：{exc}') from exc
    if result.returncode:
        raise UserError(f'命令失败（{result.returncode}）：{args[0]}\n{result.stderr[-5000:]}')
    return result.stdout

@contextlib.contextmanager
def lock(directory):
    """Fail closed on concurrent writers; never delete a different run's lock."""
    directory = Path(directory)
    directory.parent.mkdir(parents=True, exist_ok=True)
    try:
        directory.mkdir()
    except FileExistsError as exc:
        raise UserError(f'存在写入锁：{directory}。等待其他任务结束；异常退出后先确认无进程再移除锁。') from exc
    try:
        yield
    finally:
        directory.rmdir()

def project_at(directory):
    root = Path(directory).expanduser().resolve()
    project = read_json(root / 'project.json')
    require(project.get('schema_version') in (1, 2, 3), '项目版本不支持')
    return root, project

def md_text(value):
    return str(value).replace('\r', '').replace('|', '\\|')


def shared_experience_data(legacy=None):
    configured=os.environ.get('CY_EDIT_EXPERIENCE_DIR')
    if configured:base=Path(configured).expanduser().resolve()
    elif os.name=='nt':base=Path(os.environ.get('APPDATA',Path.home()/'AppData/Roaming'))/'cy-edit'/'shared'
    else:base=Path.home()/'.cy-edit'/'shared'
    base.mkdir(parents=True,exist_ok=True)
    if legacy and Path(legacy).resolve()!=base:
        old=Path(legacy)/'experience/events.json';new=base/'experience/events.json'
        if old.exists():
            with lock(base/'.feedback-lock'):
                events=read_json(new) if new.exists() else [];byid={e['id']:e for e in events}
                for e in read_json(old):
                    if e['id'] in byid:require(digest(byid[e['id']])==digest(e),'旧经验同编号内容冲突，不能自动覆盖')
                    else:events.append(e);byid[e['id']]=e
                write_json(new,events)
    return base
