import unittest

from app.db.session import engine_options_for


class MySqlSessionConfigurationTests(unittest.TestCase):
    def test_sqlite_keeps_thread_safe_local_configuration(self) -> None:
        options = engine_options_for("sqlite:///./data/test.db")
        self.assertTrue(options["pool_pre_ping"])
        self.assertFalse(options["connect_args"]["check_same_thread"])

    def test_mysql_uses_bounded_pool_and_connect_timeout(self) -> None:
        options = engine_options_for(
            "mysql+pymysql://vision:password@mysql.example:3306/vision_platform?charset=utf8mb4"
        )
        self.assertTrue(options["pool_pre_ping"])
        self.assertGreaterEqual(options["pool_size"], 1)
        self.assertGreaterEqual(options["max_overflow"], 0)
        self.assertGreaterEqual(options["pool_recycle"], 60)
        self.assertGreaterEqual(options["connect_args"]["connect_timeout"], 1)


if __name__ == "__main__":
    unittest.main()
