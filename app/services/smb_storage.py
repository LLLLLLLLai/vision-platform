"""Safe staging and result publishing for SMB-backed production images."""

from __future__ import annotations

import hashlib
import ntpath
import re
import shutil
import threading
from pathlib import Path

from app.core.config import settings


class SmbStorageError(RuntimeError):
    """Raised when an SMB transfer cannot be completed safely."""


def _is_unc_path(value: str) -> bool:
    return value.strip().startswith(("\\\\", "//"))


def _normalise_unc_path(value: str) -> str:
    compact = value.strip().replace("/", "\\")
    compact = compact.lstrip("\\")
    return "\\\\" + compact


class SmbStorage:
    """Stage source files locally and publish result images beside their sources."""

    _session_registration_lock = threading.Lock()

    def __init__(
        self,
        *,
        enabled: bool | None = None,
        server_root: str | None = None,
        username: str | None = None,
        password: str | None = None,
        connection_timeout_seconds: float | None = None,
    ) -> None:
        self.enabled = settings.smb_enabled if enabled is None else enabled
        self.server_root = _normalise_unc_path(server_root or settings.smb_server_root)
        self.username = username if username is not None else settings.smb_username
        self.password = password if password is not None else settings.smb_password
        self.connection_timeout_seconds = (
            settings.smb_connection_timeout_seconds
            if connection_timeout_seconds is None
            else connection_timeout_seconds
        )

    @staticmethod
    def is_remote_path(value: str) -> bool:
        return _is_unc_path(value)

    @staticmethod
    def staging_filename_for_source(source_path: str, *, position: int) -> str:
        """Build an isolated staged filename for one image in a detect request.

        Camera software can place equally named files in different SMB folders.
        A source-path hash and the input position prevent concurrent downloads
        from overwriting one another while retaining a readable original stem.
        """

        filename = ntpath.basename(source_path.strip().replace("/", "\\"))
        stem, suffix = ntpath.splitext(filename)
        safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._") or "image"
        source_key = (
            _normalise_unc_path(source_path).casefold()
            if _is_unc_path(source_path)
            else source_path.strip().replace("\\", "/")
        )
        digest = hashlib.sha256(source_key.encode("utf-8")).hexdigest()[:12]
        return f"{max(1, int(position)):02d}_{safe_stem[:96]}_{digest}{suffix or '.jpg'}"

    def stage_input(self, source_path: str, destination: Path) -> Path:
        """Copy a source image to its recipe/date workspace when necessary."""

        source_path = source_path.strip()
        if not source_path:
            raise SmbStorageError("图片路径不能为空。")
        if not self.is_remote_path(source_path):
            source = Path(source_path).expanduser()
            if not source.is_file():
                # Preserve the downstream FileNotFoundError for non-SMB integrations.
                return source
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(source, destination)
            except Exception:
                destination.unlink(missing_ok=True)
                raise
            return destination

        remote_path = self._validated_remote_path(source_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        smbclient = self._smbclient()
        try:
            with smbclient.open_file(remote_path, mode="rb") as source, destination.open(
                "wb"
            ) as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)
        except Exception as exc:
            destination.unlink(missing_ok=True)
            raise SmbStorageError(f"无法从 SMB 读取图片：{source_path}") from exc
        return destination

    def publish_result(self, local_result_path: str | Path, source_path: str) -> str:
        """Write ``*_result`` beside the source file and return that destination."""

        result_file = Path(local_result_path)
        if not result_file.is_file():
            raise SmbStorageError(f"待上传的结果图不存在：{result_file}")
        result_path = self.result_path_for_source(source_path)
        if not self.is_remote_path(source_path):
            destination = Path(result_path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(result_file, destination)
            return str(destination)

        remote_path = self._validated_remote_path(result_path)
        smbclient = self._smbclient()
        try:
            parent = ntpath.dirname(remote_path)
            smbclient.makedirs(parent, exist_ok=True)
            with result_file.open("rb") as source, smbclient.open_file(
                remote_path, mode="wb"
            ) as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)
        except Exception as exc:
            raise SmbStorageError(f"无法将结果图上传到 SMB：{result_path}") from exc
        return result_path

    @staticmethod
    def result_path_for_source(source_path: str) -> str:
        if _is_unc_path(source_path):
            normalised = _normalise_unc_path(source_path)
            directory, filename = ntpath.split(normalised)
            stem, suffix = ntpath.splitext(filename)
            return ntpath.join(directory, f"{stem}_result{suffix or '.jpg'}")
        source = Path(source_path)
        return str(source.with_name(f"{source.stem}_result{source.suffix or '.jpg'}"))

    def _validated_remote_path(self, value: str) -> str:
        normalised = _normalise_unc_path(value)
        root = self.server_root.rstrip("\\")
        lower_value = normalised.lower()
        lower_root = root.lower()
        if lower_value != lower_root and not lower_value.startswith(f"{lower_root}\\"):
            raise SmbStorageError("SMB 路径不在允许的共享目录内。")
        relative = normalised[len(root) :].lstrip("\\")
        if any(part == ".." for part in relative.split("\\")):
            raise SmbStorageError("SMB 路径不能包含上级目录。")
        return normalised

    def _smbclient(self):
        if not self.enabled:
            raise SmbStorageError("SMB 未启用，请在 .env 中配置 SMB_ENABLED=true。")
        if not self.username or not self.password:
            raise SmbStorageError("SMB 用户名或密码未配置。")
        try:
            import smbclient
        except ImportError as exc:
            raise SmbStorageError("缺少 smbprotocol 依赖，无法访问 SMB 文件服务器。") from exc
        server = self.server_root.lstrip("\\").split("\\", 1)[0]
        try:
            # smbclient maintains a process-wide connection cache. Session
            # registration mutates that cache, so protect it while parallel
            # image transfers are starting. The per-file handles remain
            # independent and can then transfer concurrently.
            with self._session_registration_lock:
                smbclient.register_session(
                    server,
                    username=self.username,
                    password=self.password,
                    connection_timeout=self.connection_timeout_seconds,
                )
        except Exception as exc:
            raise SmbStorageError("无法建立 SMB 登录会话。") from exc
        return smbclient
