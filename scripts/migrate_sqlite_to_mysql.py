"""Copy a Vision Platform SQLite database into an empty MySQL 8 database.

This is a one-time cutover helper. It preserves primary keys so file paths and
foreign-key relationships remain valid, but it intentionally does not copy
uploads, embeddings, model weights, or other filesystem artifacts.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable
from pathlib import Path

from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.engine import Engine, make_url


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.db.base import Base


def _backend(url: str) -> str:
    return make_url(url).get_backend_name()


def _non_empty_tables(engine: Engine) -> list[str]:
    populated: list[str] = []
    with engine.connect() as connection:
        for table in Base.metadata.sorted_tables:
            count = connection.scalar(select(func.count()).select_from(table))
            if count:
                populated.append(table.name)
    return populated


def _chunks(rows: Iterable[object], size: int) -> Iterable[list[dict[str, object]]]:
    chunk: list[dict[str, object]] = []
    for row in rows:
        chunk.append(dict(row))
        if len(chunk) >= size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


def migrate(source_url: str, target_url: str, batch_size: int) -> dict[str, int]:
    if _backend(source_url) != "sqlite":
        raise ValueError("源数据库必须是 SQLite。")
    if _backend(target_url) != "mysql":
        raise ValueError("目标数据库必须是 MySQL（例如 mysql+pymysql://...）。")

    source_engine = create_engine(source_url)
    target_engine = create_engine(target_url, pool_pre_ping=True)
    try:
        source_tables = set(inspect(source_engine).get_table_names())
        if not source_tables:
            raise RuntimeError("源 SQLite 数据库没有可迁移的表。")

        Base.metadata.create_all(target_engine)
        populated = _non_empty_tables(target_engine)
        if populated:
            raise RuntimeError(
                "目标 MySQL 数据库必须为空；以下表已存在数据：" + "、".join(populated)
            )

        copied: dict[str, int] = {}
        with source_engine.connect() as source_connection, target_engine.begin() as target_connection:
            target_connection.execute(text("SET FOREIGN_KEY_CHECKS=0"))
            try:
                for table in Base.metadata.sorted_tables:
                    if table.name not in source_tables:
                        copied[table.name] = 0
                        continue
                    result = source_connection.execute(select(table)).mappings()
                    row_count = 0
                    for batch in _chunks(result, batch_size):
                        target_connection.execute(table.insert(), batch)
                        row_count += len(batch)
                    copied[table.name] = row_count
            finally:
                target_connection.execute(text("SET FOREIGN_KEY_CHECKS=1"))
        return copied
    finally:
        source_engine.dispose()
        target_engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate Vision Platform SQLite data to empty MySQL 8.")
    parser.add_argument(
        "--source-url",
        default="sqlite:///./data/vision_platform.db",
        help="SQLite URL; defaults to the local platform database.",
    )
    parser.add_argument(
        "--target-url",
        required=True,
        help="Empty MySQL URL, for example mysql+pymysql://user:password@host:3306/database?charset=utf8mb4",
    )
    parser.add_argument("--batch-size", type=int, default=500)
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error("--batch-size 必须大于 0。")

    copied = migrate(args.source_url, args.target_url, args.batch_size)
    total = sum(copied.values())
    print(f"迁移完成：{len(copied)} 张表，{total} 行数据。")
    for table_name, row_count in copied.items():
        print(f"  {table_name}: {row_count}")


if __name__ == "__main__":
    main()
