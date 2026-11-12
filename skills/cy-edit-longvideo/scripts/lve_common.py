"""Shared local storage and process helpers. Python 3.10+."""
from __future__ import annotations
import contextlib
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

VERSION = '2.5.0'

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

LOCK_STALE_SECONDS = 6 * 3600
LOCK_OWNER = 'owner.json'

def _pid_alive(pid):
    """True/False when known; None when this platform cannot tell (fall back to age)."""
    if not isinstance(pid, int) or pid <= 0:
        return None
    if os.name == 'nt':
        try:
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            kernel.GetExitCodeProcess.restype = wintypes.BOOL
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel.CloseHandle.restype = wintypes.BOOL
            handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if not handle:
                error = ctypes.get_last_error()
                return True if error == 5 else False if error == 87 else None
            try:
                code = wintypes.DWORD()
                ok = kernel.GetExitCodeProcess(handle, ctypes.byref(code))
                return None if not ok else code.value == 259  # STILL_ACTIVE
            finally:
                kernel.CloseHandle(handle)
        except Exception:
            return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None
    return True

def _lock_snapshot(directory):
    try:
        return (directory / LOCK_OWNER).read_text(encoding='utf-8')
    except OSError:
        return None

def _stale_reason(directory, snapshot):
    """Why an existing lock may be taken over, or None while its holder may still be running."""
    try:
        owner = json.loads(snapshot)
        created = float(owner['created'])
    except (ValueError, KeyError, TypeError):
        owner = None
        try:
            created = directory.stat().st_mtime
        except OSError:
            return None
    age = datetime.now(timezone.utc).timestamp() - created
    if owner and owner.get('host') == platform.node():
        alive = _pid_alive(owner.get('pid'))
        if alive is True:
            return None  # A long-running job keeps its lock regardless of age.
        if alive is False:
            return f"持有进程 {owner.get('pid')} 已不存在"
    if age > LOCK_STALE_SECONDS:
        return f'锁已存在约 {age / 3600:.1f} 小时，超过 {LOCK_STALE_SECONDS // 3600} 小时上限' + ('' if owner else '（旧版锁，无持有者记录）')
    return None

@contextlib.contextmanager
def lock(directory):
    """Recover dead/unknown stale holders, never a known active local process."""
    directory = Path(directory)
    directory.parent.mkdir(parents=True, exist_ok=True)
    token = os.urandom(8).hex()
    for attempt in range(2):
        try:
            directory.mkdir()
            break
        except FileExistsError as exc:
            snapshot = _lock_snapshot(directory)
            reason = _stale_reason(directory, snapshot) if attempt == 0 else None
            if not reason:
                raise UserError(f'存在写入锁：{directory}。等待其他任务结束；已退出进程的锁会自动接管，无法识别持有者的旧锁在 {LOCK_STALE_SECONDS // 3600} 小时后接管。') from exc
            # Rename first so that only one waiting process can win the takeover.
            grave = directory.with_name(directory.name + '.stale-' + token)
            try:
                os.rename(directory, grave)
            except OSError as exc2:
                raise UserError(f'存在写入锁：{directory}。另一任务正在接管，请稍后重试。') from exc2
            if _lock_snapshot(grave) != snapshot:
                # Another process took over between our check and rename: give its fresh lock back.
                try:
                    os.rename(grave, directory)
                except OSError:
                    pass
                raise UserError(f'存在写入锁：{directory}。另一任务刚接管，请稍后重试。') from exc
            shutil.rmtree(grave, ignore_errors=True)
            print(f'已接管残留写入锁 {directory}：{reason}。', file=sys.stderr, flush=True)
    owner = {'pid': os.getpid(), 'host': platform.node(), 'created': datetime.now(timezone.utc).timestamp(), 'created_at': now(), 'token': token}
    try:
        (directory / LOCK_OWNER).write_text(json.dumps(owner, ensure_ascii=False), encoding='utf-8')
    except OSError:
        directory.rmdir()
        raise
    try:
        yield
    finally:
        # Never delete a lock another run has since taken over.
        try:
            current = json.loads((directory / LOCK_OWNER).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            current = None
        if current and current.get('token') == token:
            (directory / LOCK_OWNER).unlink(missing_ok=True)
            try:
                directory.rmdir()
            except OSError:
                pass

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
