import unittest

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


if __name__ == "__main__":
    unittest.main()
