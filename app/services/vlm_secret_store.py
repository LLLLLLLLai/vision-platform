import os
from pathlib import Path
import tempfile

from app.core.config import PROJECT_ROOT, settings


def load_master_key(*, create: bool = False) -> bytes:
    if settings.vlm_secret_key.strip():
        return settings.vlm_secret_key.strip().encode("ascii")
    path = Path(settings.vlm_secret_key_file).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    if not path.exists() and create:
        from cryptography.fernet import Fernet

        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, temporary_name = tempfile.mkstemp(prefix=".vlm-key-", dir=path.parent)
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(Fernet.generate_key())
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary_path, path)
            except FileExistsError:
                pass
        finally:
            temporary_path.unlink(missing_ok=True)
    return path.read_bytes().strip()
