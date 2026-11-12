"""Backend-specific public model names; no model download at import time."""
from pathlib import Path
import platform

def backend_name(value='auto'):
    if value != 'auto': return value
    return 'mlx' if platform.system() == 'Darwin' and platform.machine().lower() in ('arm64','aarch64') else 'faster'

def model_name(backend, value=None):
    value = value or 'large-v3-turbo'
    if Path(value).expanduser().is_dir(): return str(Path(value).expanduser().resolve())
    maps = {
        'faster': {'large-v3-turbo':'dropbox-dash/faster-whisper-large-v3-turbo','turbo':'dropbox-dash/faster-whisper-large-v3-turbo','small':'Systran/faster-whisper-small'},
        'mlx': {'large-v3-turbo':'mlx-community/whisper-large-v3-turbo','turbo':'mlx-community/whisper-large-v3-turbo','small':'mlx-community/whisper-small-mlx'},
    }
    return maps.get(backend,{}).get(value,value)
