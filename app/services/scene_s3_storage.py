from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
import re

from PIL import Image, ImageOps

from app.core.config import settings


class SceneS3StorageError(RuntimeError):
    pass


class InvalidSceneS3KeyError(SceneS3StorageError):
    pass


def _relative_key(value: str, *, require_jpeg: bool) -> str:
    key = value.strip()
    segments = key.split("/")
    if (
        not key
        or key.startswith("/")
        or "\\" in key
        or any(segment in {"", ".", ".."} for segment in segments)
        or any(ord(character) < 32 for character in key)
        or "://" in key
    ):
        raise InvalidSceneS3KeyError("S3 路径必须是桶内相对路径，不能包含空段、反斜杠或目录跳转。")
    if require_jpeg and Path(key).suffix.lower() not in {".jpg", ".jpeg"}:
        raise InvalidSceneS3KeyError("结果图为 JPEG 格式，result_s3_key 必须以 .jpg 或 .jpeg 结尾。")
    if len(key.encode("utf-8")) > 1024:
        raise InvalidSceneS3KeyError("S3 对象键不能超过 1024 字节。")
    return key


class SceneS3Storage:
    def validate_request(self, requested_key: str | None) -> None:
        if not settings.scene_result_s3_enabled:
            raise SceneS3StorageError("场景结果图 S3 上传未启用。")
        if not settings.scene_result_s3_bucket.strip():
            raise SceneS3StorageError("未配置 SCENE_RESULT_S3_BUCKET。")
        if settings.scene_result_s3_prefix.strip("/"):
            try:
                _relative_key(settings.scene_result_s3_prefix.strip("/"), require_jpeg=False)
            except InvalidSceneS3KeyError as exc:
                raise SceneS3StorageError("SCENE_RESULT_S3_PREFIX 配置无效。") from exc
        if requested_key is not None:
            _relative_key(requested_key, require_jpeg=True)
        access_key = settings.scene_result_s3_access_key_id.strip()
        secret_key = settings.scene_result_s3_secret_access_key.strip()
        if bool(access_key) != bool(secret_key):
            raise SceneS3StorageError("S3 Access Key 和 Secret Key 必须同时配置，或都留空使用默认凭证链。")

    def destination(self, scene_code: str, execution_id: int, requested_key: str | None) -> tuple[str, str, str]:
        self.validate_request(requested_key)
        bucket = settings.scene_result_s3_bucket.strip()
        prefix = settings.scene_result_s3_prefix.strip("/")
        if requested_key is None:
            safe_scene_code = re.sub(r"[^A-Za-z0-9_-]", "_", scene_code)
            relative_key = f"{safe_scene_code}/{datetime.now(timezone.utc):%Y/%m/%d}/{execution_id}_result.jpg"
        else:
            relative_key = _relative_key(requested_key, require_jpeg=True)
        key = "/".join(part for part in (prefix, relative_key) if part)
        if len(key.encode("utf-8")) > 1024:
            raise InvalidSceneS3KeyError("S3 对象键与配置前缀合计不能超过 1024 字节。")
        return bucket, key, f"s3://{bucket}/{key}"

    def _client(self):
        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:
            raise SceneS3StorageError("缺少 boto3，请安装 requirements.txt 中的依赖。") from exc

        endpoint_url = settings.scene_result_s3_endpoint_url.strip() or None
        client_config = Config(
            connect_timeout=10,
            read_timeout=30,
            retries={"max_attempts": 2},
            s3={"addressing_style": "path"} if endpoint_url else {},
        )
        return boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=settings.scene_result_s3_region.strip() or None,
            aws_access_key_id=settings.scene_result_s3_access_key_id.strip() or None,
            aws_secret_access_key=settings.scene_result_s3_secret_access_key.strip() or None,
            verify=settings.scene_result_s3_verify_tls,
            config=client_config,
        )

    def publish_result(
        self,
        image_path: Path,
        *,
        scene_code: str,
        execution_id: int,
        requested_key: str | None,
    ) -> str:
        bucket, key, uri = self.destination(scene_code, execution_id, requested_key)
        if not image_path.is_file():
            raise SceneS3StorageError("待上传的场景结果图不存在。")
        try:
            with Image.open(image_path) as source:
                if source.format == "JPEG":
                    with image_path.open("rb") as image_file:
                        self._client().upload_fileobj(
                            image_file, bucket, key, ExtraArgs={"ContentType": "image/jpeg"}
                        )
                else:
                    image_file = BytesIO()
                    ImageOps.exif_transpose(source).convert("RGB").save(image_file, format="JPEG", quality=95)
                    image_file.seek(0)
                    self._client().upload_fileobj(
                        image_file, bucket, key, ExtraArgs={"ContentType": "image/jpeg"}
                    )
        except SceneS3StorageError:
            raise
        except Exception as exc:
            raise SceneS3StorageError(f"场景结果图上传 S3 失败：{exc}") from exc
        return uri
