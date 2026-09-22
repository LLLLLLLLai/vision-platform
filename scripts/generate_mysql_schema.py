"""Export the current SQLAlchemy metadata as MySQL 8 DDL and field documentation."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import UniqueConstraint
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateIndex, CreateTable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.db.base import Base


TABLE_SUMMARIES = {
    "algorithm_configs": "算法能力与服务端点配置。",
    "automation_jobs": "场景评测、提示词优化、模型训练和异步复核任务。",
    "dataset_items": "数据集素材、真值、标注和自动切分信息。",
    "datasets": "场景评测集和 YOLO 训练集定义。",
    "detection_api_calls": "外部 detect 接口的请求、响应与调用方审计记录。",
    "detection_item_results": "检测任务中每张图片、每个 ROI、每个规则的结果。",
    "detection_tasks": "一次工艺配方检测任务的总记录。",
    "inspection_items": "ROI 下的旧规则引擎检测项，兼容既有检测接口。",
    "inspection_scenario_versions": "检测场景的版本化定义、提示词、输入输出 Schema 和发布状态。",
    "inspection_scenarios": "检测场景主对象及当前已发布版本。",
    "model_registry": "基础模型和能力插件注册信息。",
    "object_relations": "产品世界模型中对象之间的期望关系。",
    "product_scenes": "产品二维空间世界模型及图像对齐配置。",
    "products": "产品主数据。",
    "recipe_feature_anchors": "工艺配方的图像定位特征点。",
    "recipes": "按拉线、物料、工序、相机、拍照次数管理的工艺配方版本。",
    "reference_candidates": "历史候选基准记录；当前自动采集关闭时不再新增。",
    "reference_groups": "DINOv2 相似度参考组与合并向量矩阵信息。",
    "reference_images": "参考组中的标准图及其向量索引。",
    "reference_object_types": "统一的视觉物体类型字典。",
    "regions_of_interest": "配方中的 ROI 检测区域和归一化坐标。",
    "roi_scenario_bindings": "ROI 到已发布检测场景版本的绑定与变量映射。",
    "scenario_edges": "流程场景节点之间的数据连线和映射。",
    "scenario_execution_reviews": "执行结果的 VLM 复核与人工复判记录。",
    "scenario_executions": "场景在生产、测试或评测中的一次执行记录。",
    "scenario_nodes": "流程场景的节点配置、坐标与排序。",
    "scene_objects": "产品世界模型中的对象、位置和期望状态。",
    "stations": "工位主数据。",
    "vision_model_versions": "YOLO 训练权重、指标、训练产物及发布状态。",
    "vision_models": "可训练视觉模型的逻辑定义和类别集合。",
    "vlm_model_configs": "OpenAI 兼容 VLM 的连接、模型与推理参数配置。",
}


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.rstrip() + "\n", encoding="utf-8")


def _column_default(column) -> str:
    if column.server_default is not None:
        return str(column.server_default.arg)
    if column.default is not None:
        return "应用默认值"
    return ""


def _column_keys(table, column) -> str:
    keys: list[str] = []
    if column.primary_key:
        keys.append("PK")
    if column.unique:
        keys.append("UNIQUE")
    for constraint in table.constraints:
        if isinstance(constraint, UniqueConstraint) and column.name in constraint.columns:
            keys.append("UNIQUE")
    if column.index:
        keys.append("INDEX")
    for index in table.indexes:
        if column.name in index.columns:
            keys.append("UNIQUE INDEX" if index.unique else "INDEX")
    for foreign_key in column.foreign_keys:
        keys.append(f"FK → {foreign_key.target_fullname}")
    return "；".join(dict.fromkeys(keys))


def _mysql_table_ddl(table, dialect) -> str:
    statement = str(CreateTable(table).compile(dialect=dialect)).strip().rstrip(";")
    statement = "\n".join(line.rstrip() for line in statement.splitlines())
    if statement.endswith(")"):
        statement = f"{statement} ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci"
    return statement + ";"


def build_sql() -> str:
    dialect = mysql.dialect()
    statements = [
        "-- Vision Platform MySQL 8 schema generated from SQLAlchemy metadata.",
        "-- Apply to a new MySQL 8.0.17+ database after reviewing deployment-specific settings.",
        "SET NAMES utf8mb4;",
        "",
    ]
    for table in Base.metadata.sorted_tables:
        statements.append(f"-- {table.name}: {TABLE_SUMMARIES.get(table.name, '')}")
        statements.append(_mysql_table_ddl(table, dialect))
        for index in sorted(table.indexes, key=lambda item: item.name or ""):
            index_statement = str(CreateIndex(index).compile(dialect=dialect)).strip().rstrip(";")
            statements.append(index_statement + ";")
        statements.append("")
    return "\n".join(statements)


def build_markdown() -> str:
    dialect = mysql.dialect()
    sections = [
        "# Vision Platform MySQL 8 表结构与字段说明",
        "",
        "本文件由 `scripts/generate_mysql_schema.py` 根据当前 SQLAlchemy ORM 自动生成。",
        "",
        "## 使用范围",
        "",
        "- 推荐 MySQL `8.0.17+`、`InnoDB`、`utf8mb4` 与 `utf8mb4_0900_ai_ci`。",
        "- 平台当前本地开发默认使用 SQLite；迁移 MySQL 前应使用 Alembic 或受控迁移脚本，不应直接对已有生产库重复执行完整建表 SQL。",
        "- JSON 字段保存流程定义、规则配置、模型输出和快照；查询高频 JSON 属性时建议再建立生成列和索引。",
        "- `created_at`、`updated_at` 等显示“应用默认值”的字段由应用写入 UTC 时间。",
        "",
        "## 表概览",
        "",
        "| 表名 | 用途 |",
        "| --- | --- |",
        *[
            f"| `{table.name}` | {TABLE_SUMMARIES.get(table.name, '未分类表')} |"
            for table in Base.metadata.sorted_tables
        ],
    ]
    for table in Base.metadata.sorted_tables:
        sections.extend(
            [
                "",
                f"## `{table.name}`",
                "",
                TABLE_SUMMARIES.get(table.name, ""),
                "",
                "| 字段 | MySQL 类型 | 可空 | 键 / 关联 | 默认值 |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        for column in table.columns:
            sections.append(
                "| `{}` | `{}` | {} | {} | {} |".format(
                    column.name,
                    column.type.compile(dialect=dialect),
                    "是" if column.nullable else "否",
                    _column_keys(table, column) or "-",
                    _column_default(column) or "-",
                )
            )
        if table.indexes:
            sections.extend(
                [
                    "",
                    "索引：" + "；".join(
                        f"`{index.name}` ({', '.join(column.name for column in index.columns)})"
                        for index in sorted(table.indexes, key=lambda item: item.name or "")
                    ),
                ]
            )
    return "\n".join(sections)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Vision Platform MySQL 8 schema artifacts.")
    parser.add_argument(
        "--sql-output",
        type=Path,
        default=PROJECT_ROOT / "docs/mysql/vision_platform_mysql8.sql",
    )
    parser.add_argument(
        "--markdown-output",
        type=Path,
        default=PROJECT_ROOT / "docs/MySQL-Table-Structure.md",
    )
    args = parser.parse_args()
    _write(args.sql_output, build_sql())
    _write(args.markdown_output, build_markdown())
    print(f"Generated {args.sql_output}")
    print(f"Generated {args.markdown_output}")


if __name__ == "__main__":
    main()
