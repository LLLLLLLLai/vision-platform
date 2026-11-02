# MySQL 8 迁移与切换

生产环境使用 MySQL 8；本地现有 SQLite 数据可通过本项目的受控迁移脚本一次性导入。迁移脚本仅复制数据库表数据，不会复制图片、向量、训练产物、模型文件或 `.env`。

## 迁移前检查

1. 停止平台写入，完整备份 SQLite、`uploads/`、`detection_results/`、`embeddings/` 和 `data/training_runs/`。
2. 使用 MySQL `8.0.17+`，创建空库并授予平台账号建表、读写权限。
3. 安装项目依赖中的 `PyMySQL`；不要把真实密码写进 Git 或命令历史。
4. 确认目标 MySQL 库为空。脚本发现目标表中已有数据会立即停止，不会合并或覆盖。

## 创建数据库

```sql
CREATE DATABASE vision_platform
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_0900_ai_ci;

CREATE USER 'vision_platform'@'%' IDENTIFIED BY '请替换为强密码';
GRANT ALL PRIVILEGES ON vision_platform.* TO 'vision_platform'@'%';
FLUSH PRIVILEGES;
```

## 执行迁移

先复制 `config/mysql.env.example` 中的数据库配置到本机 `.env`，但不要立即切换正在运行的平台。用环境变量或安全的凭据管理方式提供目标 URL：

```powershell
$env:VISION_MYSQL_URL = "mysql+pymysql://vision_platform:密码@mysql-host:3306/vision_platform?charset=utf8mb4"
.\.venv\Scripts\python.exe scripts\migrate_sqlite_to_mysql.py --target-url $env:VISION_MYSQL_URL
```

Linux：

```bash
export VISION_MYSQL_URL='mysql+pymysql://vision_platform:密码@mysql-host:3306/vision_platform?charset=utf8mb4'
.venv/bin/python scripts/migrate_sqlite_to_mysql.py --target-url "$VISION_MYSQL_URL"
```

可选参数 `--source-url` 指向其他 SQLite 文件；`--batch-size` 默认每批 500 行。迁移完成后脚本会列出每张表的复制行数。

## 切换平台

1. 复核迁移输出，并抽查配方、场景、数据集、检测记录及训练任务数量。
2. 将 `.env` 中的 `DATABASE_URL` 改为 MySQL URL，同时保留连接池参数：

```dotenv
DATABASE_URL=mysql+pymysql://vision_platform:CHANGE_ME@mysql-host:3306/vision_platform?charset=utf8mb4
DATABASE_POOL_SIZE=10
DATABASE_MAX_OVERFLOW=20
DATABASE_POOL_RECYCLE_SECONDS=1800
DATABASE_CONNECT_TIMEOUT_SECONDS=10
```

3. 启动平台；`init_database` 会只补齐缺失表，不会覆盖已有数据。
4. 确认 `/api/v1/health`、配方查询、场景调用、训练任务创建和检测记录写入正常后，再开放生产流量。

## 回退原则

在尚未向 MySQL 写入生产数据前，可以把 `DATABASE_URL` 改回 SQLite 并重启。MySQL 接管生产写入后，SQLite 与 MySQL 会产生分叉；此时只能通过备份或受控反向迁移恢复，不能直接复制单个数据库文件。

MySQL 建表基线见 `docs/mysql/vision_platform_mysql8.sql`，字段说明见 `docs/MySQL-Table-Structure.md`。
