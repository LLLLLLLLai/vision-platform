import unittest
from pathlib import Path

from app.db.base import Base
from scripts.generate_mysql_schema import build_markdown, build_sql


class MySqlSchemaGeneratorTests(unittest.TestCase):
    def test_schema_artifacts_cover_current_orm_tables(self) -> None:
        sql = build_sql()
        markdown = build_markdown()

        self.assertIn("ENGINE=InnoDB DEFAULT CHARSET=utf8mb4", sql)
        self.assertIn("# Vision Platform MySQL 8 表结构与字段说明", markdown)
        for table in Base.metadata.tables:
            self.assertIn(f"CREATE TABLE {table}", sql)
            self.assertIn(f"## `{table}`", markdown)
            for column in Base.metadata.tables[table].columns:
                self.assertIn(f"`{column.name}`", markdown.split(f"## `{table}`", 1)[1].split("\n## `", 1)[0])

    def test_committed_schema_matches_current_orm(self) -> None:
        root = Path(__file__).resolve().parents[1]
        self.assertEqual(
            (root / "docs/mysql/vision_platform_mysql8.sql").read_text(encoding="utf-8"),
            build_sql().rstrip() + "\n",
        )
        self.assertEqual(
            (root / "docs/MySQL-Table-Structure.md").read_text(encoding="utf-8"),
            build_markdown().rstrip() + "\n",
        )


if __name__ == "__main__":
    unittest.main()
