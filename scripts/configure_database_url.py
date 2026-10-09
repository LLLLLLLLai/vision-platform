"""Safely update the local database connection settings from a URL file.

The URL file is intended for short-lived cutover automation so secrets do not
need to be pasted into shell commands or committed to the repository.
"""

from __future__ import annotations

import argparse
from pathlib import Path


MANAGED_KEYS = (
    "DATABASE_URL",
    "DATABASE_POOL_SIZE",
    "DATABASE_MAX_OVERFLOW",
    "DATABASE_POOL_RECYCLE_SECONDS",
    "DATABASE_CONNECT_TIMEOUT_SECONDS",
)


def update_env_file(env_path: Path, database_url: str) -> None:
    existing_lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    retained_lines = [
        line
        for line in existing_lines
        if not any(line.startswith(f"{key}=") for key in MANAGED_KEYS)
    ]
    managed_lines = [
        f"DATABASE_URL={database_url}",
        "DATABASE_POOL_SIZE=10",
        "DATABASE_MAX_OVERFLOW=20",
        "DATABASE_POOL_RECYCLE_SECONDS=1800",
        "DATABASE_CONNECT_TIMEOUT_SECONDS=10",
    ]
    env_path.write_text("\n".join([*retained_lines, "", *managed_lines, ""]), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Update a local .env database URL from a file.")
    parser.add_argument("--url-file", type=Path, required=True, help="UTF-8 file containing a database URL.")
    parser.add_argument("--env-file", type=Path, default=Path(".env"), help="Target dotenv file.")
    args = parser.parse_args()

    database_url = args.url_file.read_text(encoding="utf-8").strip()
    if not database_url.startswith("mysql+pymysql://"):
        raise ValueError("仅允许写入 MySQL PyMySQL 连接地址。")

    update_env_file(args.env_file, database_url)
    print("已更新本机数据库连接配置。")


if __name__ == "__main__":
    main()
