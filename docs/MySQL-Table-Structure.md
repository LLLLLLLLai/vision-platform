# Vision Platform MySQL 8 表结构与字段说明

本文件由 `scripts/generate_mysql_schema.py` 根据当前 SQLAlchemy ORM 自动生成。

## 使用范围

- 推荐 MySQL `8.0.17+`、`InnoDB`、`utf8mb4` 与 `utf8mb4_0900_ai_ci`。
- 平台当前本地开发可兼容 SQLite；迁移到 MySQL 前应使用 `scripts/migrate_sqlite_to_mysql.py` 等受控迁移脚本，不应直接对已有生产库重复执行完整建表 SQL。
- JSON 字段保存流程定义、规则配置、模型输出和快照；查询高频 JSON 属性时建议再建立生成列和索引。
- `created_at`、`updated_at` 等显示“应用默认值”的字段由应用写入 UTC 时间。

## 表概览

| 表名 | 用途 |
| --- | --- |
| `algorithm_configs` | 算法能力与服务端点配置。 |
| `detection_api_calls` | 外部 detect 接口的请求、响应与调用方审计记录。 |
| `inspection_scenarios` | 检测场景主对象及当前已发布版本。 |
| `model_registry` | 基础模型和能力插件注册信息。 |
| `products` | 产品主数据。 |
| `reference_groups` | 历史 DINOv2 相似度参考组与向量矩阵信息；当前仅保留追溯。 |
| `reference_object_types` | 统一的视觉物体类型字典。 |
| `stations` | 工位主数据。 |
| `vision_models` | 可训练视觉模型的逻辑定义和类别集合。 |
| `vlm_model_configs` | OpenAI 兼容 VLM 的连接、模型与推理参数配置。 |
| `inspection_scenario_versions` | 检测场景的版本化定义、提示词、输入输出 Schema 和发布状态。 |
| `product_scenes` | 产品二维空间世界模型及图像对齐配置。 |
| `recipes` | 按拉线、物料、工序、相机、拍照次数管理的工艺配方版本。 |
| `reference_images` | 历史参考组中的标准图及其向量索引；当前仅保留追溯。 |
| `datasets` | 场景评测集和 YOLO 训练集定义。 |
| `detection_tasks` | 一次工艺配方检测任务的总记录。 |
| `recipe_feature_anchors` | 工艺配方的图像定位特征点。 |
| `scenario_edges` | 流程场景节点之间的数据连线和映射。 |
| `scenario_nodes` | 流程场景的节点配置、坐标与排序。 |
| `scene_objects` | 产品世界模型中的对象、位置和期望状态。 |
| `automation_jobs` | 场景评测、提示词优化、模型训练和异步复核任务。 |
| `dataset_items` | 数据集素材、真值、标注和自动切分信息。 |
| `object_relations` | 产品世界模型中对象之间的期望关系。 |
| `regions_of_interest` | 配方中的 ROI 检测区域和归一化坐标。 |
| `vision_model_versions` | YOLO 训练权重、指标、训练产物及发布状态。 |
| `inspection_items` | ROI 下的旧规则引擎检测项，兼容既有检测接口。 |
| `roi_scenario_bindings` | ROI 到已发布检测场景版本的绑定与变量映射。 |
| `scenario_executions` | 场景在生产、测试或评测中的一次执行记录。 |
| `detection_item_results` | 检测任务中每张图片、每个 ROI、每个规则的结果。 |
| `scenario_execution_reviews` | 执行结果的 VLM 复核与人工复判记录。 |
| `reference_candidates` | 历史候选基准记录；当前功能已下线，不再新增。 |

## `algorithm_configs`

算法能力与服务端点配置。

| 字段 | MySQL 类型 | 可空 | 键 / 关联 | 默认值 |
| --- | --- | --- | --- | --- |
| `id` | `INTEGER` | 否 | PK | - |
| `code` | `VARCHAR(100)` | 否 | UNIQUE；INDEX；UNIQUE INDEX | - |
| `name` | `VARCHAR(200)` | 否 | - | - |
| `capability` | `VARCHAR(100)` | 否 | INDEX | - |
| `engine` | `VARCHAR(100)` | 否 | - | - |
| `service_url` | `VARCHAR(500)` | 是 | - | - |
| `timeout_seconds` | `FLOAT` | 否 | - | 应用默认值 |
| `config_json` | `JSON` | 否 | - | 应用默认值 |
| `enabled` | `BOOL` | 否 | - | 应用默认值 |
| `created_at` | `DATETIME` | 否 | - | 应用默认值 |
| `updated_at` | `DATETIME` | 否 | - | 应用默认值 |
| `is_deleted` | `BOOL` | 否 | - | 应用默认值 |

索引：`ix_algorithm_configs_capability` (capability)；`ix_algorithm_configs_code` (code)

## `detection_api_calls`

外部 detect 接口的请求、响应与调用方审计记录。

| 字段 | MySQL 类型 | 可空 | 键 / 关联 | 默认值 |
| --- | --- | --- | --- | --- |
| `id` | `INTEGER` | 否 | PK | - |
| `caller_ip` | `VARCHAR(100)` | 否 | INDEX | - |
| `called_at` | `DATETIME` | 否 | INDEX | 应用默认值 |
| `sn` | `VARCHAR(200)` | 否 | INDEX | - |
| `request_payload` | `JSON` | 否 | - | 应用默认值 |
| `response_payload` | `JSON` | 否 | - | 应用默认值 |
| `response_code` | `INTEGER` | 否 | INDEX | - |
| `call_status` | `VARCHAR(30)` | 否 | INDEX | - |
| `elapsed_ms` | `FLOAT` | 是 | - | - |
| `created_at` | `DATETIME` | 否 | - | 应用默认值 |
| `updated_at` | `DATETIME` | 否 | - | 应用默认值 |
| `is_deleted` | `BOOL` | 否 | - | 应用默认值 |

索引：`ix_detection_api_calls_call_status` (call_status)；`ix_detection_api_calls_called_at` (called_at)；`ix_detection_api_calls_caller_ip` (caller_ip)；`ix_detection_api_calls_response_code` (response_code)；`ix_detection_api_calls_sn` (sn)

## `inspection_scenarios`

检测场景主对象及当前已发布版本。

| 字段 | MySQL 类型 | 可空 | 键 / 关联 | 默认值 |
| --- | --- | --- | --- | --- |
| `id` | `INTEGER` | 否 | PK | - |
| `code` | `VARCHAR(100)` | 否 | UNIQUE；INDEX；UNIQUE INDEX | - |
| `name` | `VARCHAR(200)` | 否 | - | - |
| `category` | `VARCHAR(100)` | 否 | INDEX | 应用默认值 |
| `mode` | `VARCHAR(30)` | 否 | INDEX | 应用默认值 |
| `description` | `VARCHAR(1000)` | 是 | - | - |
| `published_version_id` | `INTEGER` | 是 | - | - |
| `enabled` | `BOOL` | 否 | - | 应用默认值 |
| `created_at` | `DATETIME` | 否 | - | 应用默认值 |
| `updated_at` | `DATETIME` | 否 | - | 应用默认值 |
| `is_deleted` | `BOOL` | 否 | - | 应用默认值 |

索引：`ix_inspection_scenarios_category` (category)；`ix_inspection_scenarios_code` (code)；`ix_inspection_scenarios_mode` (mode)

## `model_registry`

基础模型和能力插件注册信息。

| 字段 | MySQL 类型 | 可空 | 键 / 关联 | 默认值 |
| --- | --- | --- | --- | --- |
| `id` | `INTEGER` | 否 | PK | - |
| `code` | `VARCHAR(100)` | 否 | UNIQUE；INDEX；UNIQUE INDEX | - |
| `name` | `VARCHAR(200)` | 否 | - | - |
| `capability` | `VARCHAR(100)` | 否 | INDEX | - |
| `provider` | `VARCHAR(100)` | 否 | - | 应用默认值 |
| `runtime` | `VARCHAR(100)` | 否 | - | 应用默认值 |
| `version` | `VARCHAR(100)` | 是 | - | - |
| `model_path` | `VARCHAR(500)` | 是 | - | - |
| `service_url` | `VARCHAR(500)` | 是 | - | - |
| `config_json` | `JSON` | 否 | - | 应用默认值 |
| `enabled` | `BOOL` | 否 | - | 应用默认值 |
| `created_at` | `DATETIME` | 否 | - | 应用默认值 |
| `updated_at` | `DATETIME` | 否 | - | 应用默认值 |
| `is_deleted` | `BOOL` | 否 | - | 应用默认值 |

索引：`ix_model_registry_capability` (capability)；`ix_model_registry_code` (code)

## `products`

产品主数据。

| 字段 | MySQL 类型 | 可空 | 键 / 关联 | 默认值 |
| --- | --- | --- | --- | --- |
| `id` | `INTEGER` | 否 | PK | - |
| `code` | `VARCHAR(100)` | 否 | UNIQUE；INDEX；UNIQUE INDEX | - |
| `name` | `VARCHAR(200)` | 否 | - | - |
| `description` | `VARCHAR(500)` | 是 | - | - |
| `enabled` | `BOOL` | 否 | - | 应用默认值 |
| `created_at` | `DATETIME` | 否 | - | 应用默认值 |
| `updated_at` | `DATETIME` | 否 | - | 应用默认值 |
| `is_deleted` | `BOOL` | 否 | - | 应用默认值 |

索引：`ix_products_code` (code)

## `reference_groups`

历史 DINOv2 相似度参考组与向量矩阵信息；当前仅保留追溯。

| 字段 | MySQL 类型 | 可空 | 键 / 关联 | 默认值 |
| --- | --- | --- | --- | --- |
| `id` | `INTEGER` | 否 | PK | - |
| `code` | `VARCHAR(100)` | 否 | UNIQUE；INDEX；UNIQUE INDEX | - |
| `name` | `VARCHAR(200)` | 否 | - | - |
| `object_type` | `VARCHAR(100)` | 否 | INDEX | - |
| `class_code` | `VARCHAR(100)` | 否 | INDEX | - |
| `description` | `VARCHAR(500)` | 是 | - | - |
| `enabled` | `BOOL` | 否 | - | 应用默认值 |
| `embedding_set_version` | `INTEGER` | 否 | - | 应用默认值 |
| `embedding_matrix_path` | `VARCHAR(500)` | 是 | - | - |
| `embedding_manifest_path` | `VARCHAR(500)` | 是 | - | - |
| `embedding_count` | `INTEGER` | 否 | - | 应用默认值 |
| `created_at` | `DATETIME` | 否 | - | 应用默认值 |
| `updated_at` | `DATETIME` | 否 | - | 应用默认值 |
| `is_deleted` | `BOOL` | 否 | - | 应用默认值 |

索引：`ix_reference_groups_class_code` (class_code)；`ix_reference_groups_code` (code)；`ix_reference_groups_object_type` (object_type)

## `reference_object_types`

统一的视觉物体类型字典。

| 字段 | MySQL 类型 | 可空 | 键 / 关联 | 默认值 |
| --- | --- | --- | --- | --- |
| `id` | `INTEGER` | 否 | PK | - |
| `code` | `VARCHAR(100)` | 否 | UNIQUE；INDEX；UNIQUE INDEX | - |
| `name` | `VARCHAR(200)` | 否 | - | - |
| `description` | `VARCHAR(500)` | 是 | - | - |
| `enabled` | `BOOL` | 否 | - | 应用默认值 |
| `created_at` | `DATETIME` | 否 | - | 应用默认值 |
| `updated_at` | `DATETIME` | 否 | - | 应用默认值 |
| `is_deleted` | `BOOL` | 否 | - | 应用默认值 |

索引：`ix_reference_object_types_code` (code)

## `stations`

工位主数据。

| 字段 | MySQL 类型 | 可空 | 键 / 关联 | 默认值 |
| --- | --- | --- | --- | --- |
| `id` | `INTEGER` | 否 | PK | - |
| `code` | `VARCHAR(100)` | 否 | UNIQUE；INDEX；UNIQUE INDEX | - |
| `name` | `VARCHAR(200)` | 否 | - | - |
| `line_code` | `VARCHAR(100)` | 是 | - | - |
| `process_code` | `VARCHAR(100)` | 是 | - | - |
| `enabled` | `BOOL` | 否 | - | 应用默认值 |
| `created_at` | `DATETIME` | 否 | - | 应用默认值 |
| `updated_at` | `DATETIME` | 否 | - | 应用默认值 |
| `is_deleted` | `BOOL` | 否 | - | 应用默认值 |

索引：`ix_stations_code` (code)

## `vision_models`

可训练视觉模型的逻辑定义和类别集合。

| 字段 | MySQL 类型 | 可空 | 键 / 关联 | 默认值 |
| --- | --- | --- | --- | --- |
| `id` | `INTEGER` | 否 | PK | - |
| `code` | `VARCHAR(100)` | 否 | UNIQUE；INDEX；UNIQUE INDEX | - |
| `name` | `VARCHAR(200)` | 否 | - | - |
| `task_type` | `VARCHAR(50)` | 否 | INDEX | - |
| `description` | `VARCHAR(1000)` | 是 | - | - |
| `base_model` | `VARCHAR(300)` | 是 | - | - |
| `labels_json` | `JSON` | 否 | - | 应用默认值 |
| `enabled` | `BOOL` | 否 | - | 应用默认值 |
| `created_at` | `DATETIME` | 否 | - | 应用默认值 |
| `updated_at` | `DATETIME` | 否 | - | 应用默认值 |
| `is_deleted` | `BOOL` | 否 | - | 应用默认值 |

索引：`ix_vision_models_code` (code)；`ix_vision_models_task_type` (task_type)

## `vlm_model_configs`

OpenAI 兼容 VLM 的连接、模型与推理参数配置。

| 字段 | MySQL 类型 | 可空 | 键 / 关联 | 默认值 |
| --- | --- | --- | --- | --- |
| `id` | `INTEGER` | 否 | PK | - |
| `code` | `VARCHAR(100)` | 否 | UNIQUE；INDEX；UNIQUE INDEX | - |
| `name` | `VARCHAR(200)` | 否 | - | - |
| `base_url` | `VARCHAR(500)` | 否 | - | - |
| `model_name` | `VARCHAR(200)` | 否 | - | - |
| `api_key_ciphertext` | `VARCHAR(2000)` | 是 | - | - |
| `api_key_env_name` | `VARCHAR(200)` | 是 | - | - |
| `api_key_hint` | `VARCHAR(30)` | 是 | - | - |
| `thinking_enabled` | `BOOL` | 否 | - | 应用默认值 |
| `extra_params_json` | `JSON` | 否 | - | 应用默认值 |
| `temperature` | `FLOAT` | 否 | - | 应用默认值 |
| `max_tokens` | `INTEGER` | 否 | - | 应用默认值 |
| `timeout_seconds` | `FLOAT` | 否 | - | 应用默认值 |
| `enabled` | `BOOL` | 否 | - | 应用默认值 |
| `created_at` | `DATETIME` | 否 | - | 应用默认值 |
| `updated_at` | `DATETIME` | 否 | - | 应用默认值 |
| `is_deleted` | `BOOL` | 否 | - | 应用默认值 |

索引：`ix_vlm_model_configs_code` (code)

## `inspection_scenario_versions`

检测场景的版本化定义、提示词、输入输出 Schema 和发布状态。

| 字段 | MySQL 类型 | 可空 | 键 / 关联 | 默认值 |
| --- | --- | --- | --- | --- |
| `id` | `INTEGER` | 否 | PK | - |
| `scenario_id` | `INTEGER` | 否 | UNIQUE；INDEX；FK → inspection_scenarios.id | - |
| `version` | `VARCHAR(50)` | 否 | UNIQUE | 应用默认值 |
| `status` | `VARCHAR(30)` | 否 | INDEX | 应用默认值 |
| `prompt_template` | `VARCHAR(8000)` | 是 | - | - |
| `input_schema_json` | `JSON` | 否 | - | 应用默认值 |
| `output_schema_json` | `JSON` | 否 | - | 应用默认值 |
| `definition_json` | `JSON` | 否 | - | 应用默认值 |
| `primary_vlm_model_id` | `INTEGER` | 是 | INDEX；FK → vlm_model_configs.id | - |
| `review_vlm_model_id` | `INTEGER` | 是 | INDEX；FK → vlm_model_configs.id | - |
| `published_at` | `DATETIME` | 是 | - | - |
| `published_by` | `VARCHAR(100)` | 是 | - | - |
| `created_at` | `DATETIME` | 否 | - | 应用默认值 |
| `updated_at` | `DATETIME` | 否 | - | 应用默认值 |
| `is_deleted` | `BOOL` | 否 | - | 应用默认值 |

索引：`ix_inspection_scenario_versions_primary_vlm_model_id` (primary_vlm_model_id)；`ix_inspection_scenario_versions_review_vlm_model_id` (review_vlm_model_id)；`ix_inspection_scenario_versions_scenario_id` (scenario_id)；`ix_inspection_scenario_versions_status` (status)

## `product_scenes`

产品二维空间世界模型及图像对齐配置。

| 字段 | MySQL 类型 | 可空 | 键 / 关联 | 默认值 |
| --- | --- | --- | --- | --- |
| `id` | `INTEGER` | 否 | PK | - |
| `product_id` | `INTEGER` | 否 | INDEX；FK → products.id | - |
| `code` | `VARCHAR(100)` | 否 | UNIQUE；INDEX；UNIQUE INDEX | - |
| `name` | `VARCHAR(200)` | 否 | - | - |
| `version` | `VARCHAR(50)` | 否 | - | 应用默认值 |
| `status` | `VARCHAR(30)` | 否 | INDEX | 应用默认值 |
| `coordinate_system` | `VARCHAR(30)` | 否 | - | 应用默认值 |
| `reference_image_path` | `VARCHAR(500)` | 是 | - | - |
| `reference_width` | `INTEGER` | 是 | - | - |
| `reference_height` | `INTEGER` | 是 | - | - |
| `alignment_config` | `JSON` | 否 | - | 应用默认值 |
| `enabled` | `BOOL` | 否 | - | 应用默认值 |
| `created_at` | `DATETIME` | 否 | - | 应用默认值 |
| `updated_at` | `DATETIME` | 否 | - | 应用默认值 |
| `is_deleted` | `BOOL` | 否 | - | 应用默认值 |

索引：`ix_product_scenes_code` (code)；`ix_product_scenes_product_id` (product_id)；`ix_product_scenes_status` (status)

## `recipes`

按拉线、物料、工序、相机、拍照次数管理的工艺配方版本。

| 字段 | MySQL 类型 | 可空 | 键 / 关联 | 默认值 |
| --- | --- | --- | --- | --- |
| `id` | `INTEGER` | 否 | PK | - |
| `code` | `VARCHAR(100)` | 否 | INDEX | - |
| `recipe_family_code` | `VARCHAR(100)` | 否 | INDEX | 应用默认值 |
| `version_no` | `INTEGER` | 否 | - | 应用默认值 |
| `source_recipe_id` | `INTEGER` | 是 | INDEX；FK → recipes.id | - |
| `name` | `VARCHAR(200)` | 否 | - | - |
| `version` | `VARCHAR(50)` | 否 | - | 应用默认值 |
| `status` | `VARCHAR(30)` | 否 | INDEX | 应用默认值 |
| `project_name` | `VARCHAR(200)` | 是 | - | - |
| `product_id` | `INTEGER` | 否 | FK → products.id | - |
| `station_id` | `INTEGER` | 否 | FK → stations.id | - |
| `line_code` | `VARCHAR(100)` | 是 | INDEX | - |
| `material_code` | `VARCHAR(100)` | 是 | INDEX | - |
| `process_code` | `VARCHAR(100)` | 是 | INDEX | - |
| `camera_code` | `VARCHAR(100)` | 是 | INDEX | - |
| `capture_index` | `INTEGER` | 否 | INDEX | 应用默认值 |
| `base_image_path` | `VARCHAR(500)` | 是 | - | - |
| `reference_width` | `INTEGER` | 是 | - | - |
| `reference_height` | `INTEGER` | 是 | - | - |
| `created_at` | `DATETIME` | 否 | - | 应用默认值 |
| `updated_at` | `DATETIME` | 否 | - | 应用默认值 |
| `is_deleted` | `BOOL` | 否 | - | 应用默认值 |

索引：`ix_recipe_business_key` (line_code, material_code, process_code, camera_code, capture_index, status)；`ix_recipes_code` (code)；`ix_recipes_recipe_family_code` (recipe_family_code)；`ix_recipes_source_recipe_id` (source_recipe_id)；`ix_recipes_status` (status)

## `reference_images`

历史参考组中的标准图及其向量索引；当前仅保留追溯。

| 字段 | MySQL 类型 | 可空 | 键 / 关联 | 默认值 |
| --- | --- | --- | --- | --- |
| `id` | `INTEGER` | 否 | PK | - |
| `group_id` | `INTEGER` | 否 | INDEX；FK → reference_groups.id | - |
| `image_path` | `VARCHAR(500)` | 否 | - | - |
| `embedding_path` | `VARCHAR(500)` | 是 | - | - |
| `embedding_dimension` | `INTEGER` | 是 | - | - |
| `embedding_index` | `INTEGER` | 是 | - | - |
| `model_code` | `VARCHAR(100)` | 否 | - | 应用默认值 |
| `model_version` | `VARCHAR(100)` | 是 | - | - |
| `quality_status` | `VARCHAR(30)` | 否 | - | 应用默认值 |
| `enabled` | `BOOL` | 否 | - | 应用默认值 |
| `created_at` | `DATETIME` | 否 | - | 应用默认值 |
| `updated_at` | `DATETIME` | 否 | - | 应用默认值 |
| `is_deleted` | `BOOL` | 否 | - | 应用默认值 |

索引：`ix_reference_images_group_id` (group_id)

## `datasets`

场景评测集和 YOLO 训练集定义。

| 字段 | MySQL 类型 | 可空 | 键 / 关联 | 默认值 |
| --- | --- | --- | --- | --- |
| `id` | `INTEGER` | 否 | PK | - |
| `code` | `VARCHAR(100)` | 否 | UNIQUE；INDEX；UNIQUE INDEX | - |
| `name` | `VARCHAR(200)` | 否 | - | - |
| `description` | `VARCHAR(1000)` | 是 | - | - |
| `purpose` | `VARCHAR(30)` | 否 | INDEX | 应用默认值 |
| `media_type` | `VARCHAR(30)` | 否 | - | 应用默认值 |
| `annotation_type` | `VARCHAR(30)` | 否 | - | 应用默认值 |
| `label_schema_json` | `JSON` | 否 | - | 应用默认值 |
| `collection_scenario_version_id` | `INTEGER` | 是 | INDEX；FK → inspection_scenario_versions.id | - |
| `auto_collect_enabled` | `BOOL` | 否 | - | 应用默认值 |
| `auto_collect_limit` | `INTEGER` | 否 | - | 应用默认值 |
| `revision` | `INTEGER` | 否 | - | 应用默认值 |
| `enabled` | `BOOL` | 否 | - | 应用默认值 |
| `created_at` | `DATETIME` | 否 | - | 应用默认值 |
| `updated_at` | `DATETIME` | 否 | - | 应用默认值 |
| `is_deleted` | `BOOL` | 否 | - | 应用默认值 |

索引：`ix_datasets_code` (code)；`ix_datasets_collection_scenario_version_id` (collection_scenario_version_id)；`ix_datasets_purpose` (purpose)

## `detection_tasks`

一次工艺配方检测任务的总记录。

| 字段 | MySQL 类型 | 可空 | 键 / 关联 | 默认值 |
| --- | --- | --- | --- | --- |
| `id` | `INTEGER` | 否 | PK | - |
| `request_id` | `VARCHAR(100)` | 否 | UNIQUE；INDEX；UNIQUE INDEX | - |
| `sn` | `VARCHAR(200)` | 否 | INDEX | - |
| `recipe_id` | `INTEGER` | 否 | INDEX；FK → recipes.id | - |
| `recipe_version` | `VARCHAR(50)` | 否 | - | - |
| `status` | `VARCHAR(30)` | 否 | INDEX | 应用默认值 |
| `original_image_paths` | `JSON` | 否 | - | 应用默认值 |
| `result_image_paths` | `JSON` | 否 | - | 应用默认值 |
| `elapsed_ms` | `FLOAT` | 是 | - | - |
| `error_message` | `VARCHAR(1000)` | 是 | - | - |
| `completed_at` | `DATETIME` | 是 | - | - |
| `created_at` | `DATETIME` | 否 | - | 应用默认值 |
| `updated_at` | `DATETIME` | 否 | - | 应用默认值 |
| `is_deleted` | `BOOL` | 否 | - | 应用默认值 |

索引：`ix_detection_tasks_recipe_id` (recipe_id)；`ix_detection_tasks_request_id` (request_id)；`ix_detection_tasks_sn` (sn)；`ix_detection_tasks_status` (status)

## `recipe_feature_anchors`

工艺配方的图像定位特征点。

| 字段 | MySQL 类型 | 可空 | 键 / 关联 | 默认值 |
| --- | --- | --- | --- | --- |
| `id` | `INTEGER` | 否 | PK | - |
| `recipe_id` | `INTEGER` | 否 | UNIQUE；INDEX；FK → recipes.id | - |
| `code` | `VARCHAR(100)` | 否 | - | 应用默认值 |
| `name` | `VARCHAR(200)` | 否 | - | 应用默认值 |
| `x_ratio` | `FLOAT` | 否 | - | - |
| `y_ratio` | `FLOAT` | 否 | - | - |
| `width_ratio` | `FLOAT` | 否 | - | - |
| `height_ratio` | `FLOAT` | 否 | - | - |
| `padding` | `INTEGER` | 否 | - | 应用默认值 |
| `enabled` | `BOOL` | 否 | - | 应用默认值 |
| `created_at` | `DATETIME` | 否 | - | 应用默认值 |
| `updated_at` | `DATETIME` | 否 | - | 应用默认值 |
| `is_deleted` | `BOOL` | 否 | - | 应用默认值 |

索引：`ix_recipe_feature_anchors_recipe_id` (recipe_id)

## `scenario_edges`

流程场景节点之间的数据连线和映射。

| 字段 | MySQL 类型 | 可空 | 键 / 关联 | 默认值 |
| --- | --- | --- | --- | --- |
| `id` | `INTEGER` | 否 | PK | - |
| `scenario_version_id` | `INTEGER` | 否 | UNIQUE；INDEX；FK → inspection_scenario_versions.id | - |
| `source_node_key` | `VARCHAR(100)` | 否 | UNIQUE | - |
| `target_node_key` | `VARCHAR(100)` | 否 | UNIQUE | - |
| `mapping_json` | `JSON` | 否 | - | 应用默认值 |
| `created_at` | `DATETIME` | 否 | - | 应用默认值 |
| `updated_at` | `DATETIME` | 否 | - | 应用默认值 |
| `is_deleted` | `BOOL` | 否 | - | 应用默认值 |

索引：`ix_scenario_edges_scenario_version_id` (scenario_version_id)

## `scenario_nodes`

流程场景的节点配置、坐标与排序。

| 字段 | MySQL 类型 | 可空 | 键 / 关联 | 默认值 |
| --- | --- | --- | --- | --- |
| `id` | `INTEGER` | 否 | PK | - |
| `scenario_version_id` | `INTEGER` | 否 | UNIQUE；INDEX；FK → inspection_scenario_versions.id | - |
| `node_key` | `VARCHAR(100)` | 否 | UNIQUE | - |
| `name` | `VARCHAR(200)` | 否 | - | - |
| `node_type` | `VARCHAR(50)` | 否 | INDEX | - |
| `config_json` | `JSON` | 否 | - | 应用默认值 |
| `sort_order` | `INTEGER` | 否 | - | 应用默认值 |
| `canvas_x` | `FLOAT` | 否 | - | 应用默认值 |
| `canvas_y` | `FLOAT` | 否 | - | 应用默认值 |
| `enabled` | `BOOL` | 否 | - | 应用默认值 |
| `created_at` | `DATETIME` | 否 | - | 应用默认值 |
| `updated_at` | `DATETIME` | 否 | - | 应用默认值 |
| `is_deleted` | `BOOL` | 否 | - | 应用默认值 |

索引：`ix_scenario_nodes_node_type` (node_type)；`ix_scenario_nodes_scenario_version_id` (scenario_version_id)

## `scene_objects`

产品世界模型中的对象、位置和期望状态。

| 字段 | MySQL 类型 | 可空 | 键 / 关联 | 默认值 |
| --- | --- | --- | --- | --- |
| `id` | `INTEGER` | 否 | PK | - |
| `scene_id` | `INTEGER` | 否 | INDEX；FK → product_scenes.id | - |
| `parent_object_id` | `INTEGER` | 是 | FK → scene_objects.id | - |
| `code` | `VARCHAR(100)` | 否 | INDEX | - |
| `name` | `VARCHAR(200)` | 否 | - | - |
| `object_type` | `VARCHAR(100)` | 否 | INDEX | - |
| `location_mode` | `VARCHAR(30)` | 否 | - | 应用默认值 |
| `geometry` | `JSON` | 否 | - | 应用默认值 |
| `expected_state` | `JSON` | 否 | - | 应用默认值 |
| `perception_config` | `JSON` | 否 | - | 应用默认值 |
| `sort_order` | `INTEGER` | 否 | - | 应用默认值 |
| `enabled` | `BOOL` | 否 | - | 应用默认值 |
| `created_at` | `DATETIME` | 否 | - | 应用默认值 |
| `updated_at` | `DATETIME` | 否 | - | 应用默认值 |
| `is_deleted` | `BOOL` | 否 | - | 应用默认值 |

索引：`ix_scene_objects_code` (code)；`ix_scene_objects_object_type` (object_type)；`ix_scene_objects_scene_id` (scene_id)

## `automation_jobs`

场景评测、提示词优化、模型训练和异步复核任务。

| 字段 | MySQL 类型 | 可空 | 键 / 关联 | 默认值 |
| --- | --- | --- | --- | --- |
| `id` | `INTEGER` | 否 | PK | - |
| `job_type` | `VARCHAR(50)` | 否 | INDEX | - |
| `status` | `VARCHAR(30)` | 否 | INDEX | 应用默认值 |
| `scenario_version_id` | `INTEGER` | 是 | INDEX；FK → inspection_scenario_versions.id | - |
| `dataset_id` | `INTEGER` | 是 | FK → datasets.id | - |
| `vision_model_id` | `INTEGER` | 是 | FK → vision_models.id | - |
| `config_json` | `JSON` | 否 | - | 应用默认值 |
| `input_snapshot_json` | `JSON` | 否 | - | 应用默认值 |
| `result_json` | `JSON` | 否 | - | 应用默认值 |
| `log_path` | `VARCHAR(1000)` | 是 | - | - |
| `error_message` | `VARCHAR(2000)` | 是 | - | - |
| `started_at` | `DATETIME` | 是 | - | - |
| `completed_at` | `DATETIME` | 是 | - | - |
| `created_at` | `DATETIME` | 否 | - | 应用默认值 |
| `updated_at` | `DATETIME` | 否 | - | 应用默认值 |
| `is_deleted` | `BOOL` | 否 | - | 应用默认值 |

索引：`ix_automation_jobs_job_type` (job_type)；`ix_automation_jobs_scenario_version_id` (scenario_version_id)；`ix_automation_jobs_status` (status)

## `dataset_items`

数据集素材、真值、标注和自动切分信息。

| 字段 | MySQL 类型 | 可空 | 键 / 关联 | 默认值 |
| --- | --- | --- | --- | --- |
| `id` | `INTEGER` | 否 | PK | - |
| `dataset_id` | `INTEGER` | 否 | INDEX；FK → datasets.id | - |
| `media_path` | `VARCHAR(1000)` | 否 | - | - |
| `original_name` | `VARCHAR(500)` | 否 | - | - |
| `media_type` | `VARCHAR(30)` | 否 | - | 应用默认值 |
| `ground_truth` | `VARCHAR(30)` | 是 | INDEX | - |
| `annotation_status` | `VARCHAR(30)` | 否 | - | 应用默认值 |
| `annotation_json` | `JSON` | 否 | - | 应用默认值 |
| `split` | `VARCHAR(30)` | 是 | - | - |
| `source` | `VARCHAR(30)` | 否 | - | 应用默认值 |
| `content_hash` | `VARCHAR(128)` | 是 | INDEX | - |
| `created_at` | `DATETIME` | 否 | - | 应用默认值 |
| `updated_at` | `DATETIME` | 否 | - | 应用默认值 |
| `is_deleted` | `BOOL` | 否 | - | 应用默认值 |

索引：`ix_dataset_items_content_hash` (content_hash)；`ix_dataset_items_dataset_id` (dataset_id)；`ix_dataset_items_ground_truth` (ground_truth)

## `object_relations`

产品世界模型中对象之间的期望关系。

| 字段 | MySQL 类型 | 可空 | 键 / 关联 | 默认值 |
| --- | --- | --- | --- | --- |
| `id` | `INTEGER` | 否 | PK | - |
| `scene_id` | `INTEGER` | 否 | INDEX；FK → product_scenes.id | - |
| `source_object_id` | `INTEGER` | 否 | INDEX；FK → scene_objects.id | - |
| `target_object_id` | `INTEGER` | 否 | INDEX；FK → scene_objects.id | - |
| `relation_type` | `VARCHAR(100)` | 否 | INDEX | - |
| `expected_relation` | `JSON` | 否 | - | 应用默认值 |
| `enabled` | `BOOL` | 否 | - | 应用默认值 |
| `created_at` | `DATETIME` | 否 | - | 应用默认值 |
| `updated_at` | `DATETIME` | 否 | - | 应用默认值 |
| `is_deleted` | `BOOL` | 否 | - | 应用默认值 |

索引：`ix_object_relations_relation_type` (relation_type)；`ix_object_relations_scene_id` (scene_id)；`ix_object_relations_source_object_id` (source_object_id)；`ix_object_relations_target_object_id` (target_object_id)

## `regions_of_interest`

配方中的 ROI 检测区域和归一化坐标。

| 字段 | MySQL 类型 | 可空 | 键 / 关联 | 默认值 |
| --- | --- | --- | --- | --- |
| `id` | `INTEGER` | 否 | PK | - |
| `recipe_id` | `INTEGER` | 否 | INDEX；FK → recipes.id | - |
| `scene_object_id` | `INTEGER` | 是 | INDEX；FK → scene_objects.id | - |
| `code` | `VARCHAR(100)` | 否 | INDEX | - |
| `name` | `VARCHAR(200)` | 否 | - | - |
| `object_type` | `VARCHAR(100)` | 是 | - | - |
| `shape_type` | `VARCHAR(30)` | 否 | - | 应用默认值 |
| `x_ratio` | `FLOAT` | 否 | - | - |
| `y_ratio` | `FLOAT` | 否 | - | - |
| `width_ratio` | `FLOAT` | 否 | - | - |
| `height_ratio` | `FLOAT` | 否 | - | - |
| `pixel_coordinates` | `JSON` | 否 | - | 应用默认值 |
| `padding` | `INTEGER` | 否 | - | 应用默认值 |
| `sort_order` | `INTEGER` | 否 | - | 应用默认值 |
| `alignment_anchor` | `BOOL` | 否 | - | 应用默认值 |
| `enabled` | `BOOL` | 否 | - | 应用默认值 |
| `created_at` | `DATETIME` | 否 | - | 应用默认值 |
| `updated_at` | `DATETIME` | 否 | - | 应用默认值 |
| `is_deleted` | `BOOL` | 否 | - | 应用默认值 |

索引：`ix_regions_of_interest_code` (code)；`ix_regions_of_interest_recipe_id` (recipe_id)；`ix_regions_of_interest_scene_object_id` (scene_object_id)

## `vision_model_versions`

YOLO 训练权重、指标、训练产物及发布状态。

| 字段 | MySQL 类型 | 可空 | 键 / 关联 | 默认值 |
| --- | --- | --- | --- | --- |
| `id` | `INTEGER` | 否 | PK | - |
| `vision_model_id` | `INTEGER` | 否 | UNIQUE；INDEX；FK → vision_models.id | - |
| `dataset_id` | `INTEGER` | 是 | FK → datasets.id | - |
| `version` | `VARCHAR(50)` | 否 | UNIQUE | - |
| `status` | `VARCHAR(30)` | 否 | INDEX | 应用默认值 |
| `weights_path` | `VARCHAR(1000)` | 是 | - | - |
| `metrics_json` | `JSON` | 否 | - | 应用默认值 |
| `artifact_paths_json` | `JSON` | 否 | - | 应用默认值 |
| `published_at` | `DATETIME` | 是 | - | - |
| `created_at` | `DATETIME` | 否 | - | 应用默认值 |
| `updated_at` | `DATETIME` | 否 | - | 应用默认值 |
| `is_deleted` | `BOOL` | 否 | - | 应用默认值 |

索引：`ix_vision_model_versions_status` (status)；`ix_vision_model_versions_vision_model_id` (vision_model_id)

## `inspection_items`

ROI 下的旧规则引擎检测项，兼容既有检测接口。

| 字段 | MySQL 类型 | 可空 | 键 / 关联 | 默认值 |
| --- | --- | --- | --- | --- |
| `id` | `INTEGER` | 否 | PK | - |
| `roi_id` | `INTEGER` | 否 | INDEX；FK → regions_of_interest.id | - |
| `code` | `VARCHAR(100)` | 否 | INDEX | - |
| `name` | `VARCHAR(200)` | 否 | - | - |
| `inspection_type` | `VARCHAR(100)` | 否 | - | - |
| `capability` | `VARCHAR(100)` | 否 | INDEX | - |
| `algorithm_config_id` | `INTEGER` | 是 | FK → algorithm_configs.id | - |
| `reference_group_id` | `INTEGER` | 是 | FK → reference_groups.id | - |
| `expected_json` | `JSON` | 否 | - | 应用默认值 |
| `rule_json` | `JSON` | 否 | - | 应用默认值 |
| `execution_order` | `INTEGER` | 否 | - | 应用默认值 |
| `required` | `BOOL` | 否 | - | 应用默认值 |
| `enabled` | `BOOL` | 否 | - | 应用默认值 |
| `created_at` | `DATETIME` | 否 | - | 应用默认值 |
| `updated_at` | `DATETIME` | 否 | - | 应用默认值 |
| `is_deleted` | `BOOL` | 否 | - | 应用默认值 |

索引：`ix_inspection_items_capability` (capability)；`ix_inspection_items_code` (code)；`ix_inspection_items_roi_id` (roi_id)

## `roi_scenario_bindings`

ROI 到已发布检测场景版本的绑定与变量映射。

| 字段 | MySQL 类型 | 可空 | 键 / 关联 | 默认值 |
| --- | --- | --- | --- | --- |
| `id` | `INTEGER` | 否 | PK | - |
| `roi_id` | `INTEGER` | 否 | UNIQUE；INDEX；UNIQUE INDEX；FK → regions_of_interest.id | - |
| `scenario_version_id` | `INTEGER` | 否 | INDEX；FK → inspection_scenario_versions.id | - |
| `input_mapping_json` | `JSON` | 否 | - | 应用默认值 |
| `enabled` | `BOOL` | 否 | - | 应用默认值 |
| `created_at` | `DATETIME` | 否 | - | 应用默认值 |
| `updated_at` | `DATETIME` | 否 | - | 应用默认值 |
| `is_deleted` | `BOOL` | 否 | - | 应用默认值 |

索引：`ix_roi_scenario_bindings_roi_id` (roi_id)；`ix_roi_scenario_bindings_scenario_version_id` (scenario_version_id)

## `scenario_executions`

场景在生产、测试或评测中的一次执行记录。

| 字段 | MySQL 类型 | 可空 | 键 / 关联 | 默认值 |
| --- | --- | --- | --- | --- |
| `id` | `INTEGER` | 否 | PK | - |
| `scenario_version_id` | `INTEGER` | 否 | INDEX；FK → inspection_scenario_versions.id | - |
| `detection_task_id` | `INTEGER` | 是 | INDEX；FK → detection_tasks.id | - |
| `roi_id` | `INTEGER` | 是 | INDEX；FK → regions_of_interest.id | - |
| `dataset_item_id` | `INTEGER` | 是 | INDEX；FK → dataset_items.id | - |
| `source` | `VARCHAR(30)` | 否 | INDEX | 应用默认值 |
| `status` | `VARCHAR(30)` | 否 | INDEX | 应用默认值 |
| `result` | `VARCHAR(30)` | 是 | INDEX | - |
| `score` | `FLOAT` | 是 | - | - |
| `input_json` | `JSON` | 否 | - | 应用默认值 |
| `output_json` | `JSON` | 否 | - | 应用默认值 |
| `elapsed_ms` | `FLOAT` | 是 | - | - |
| `error_message` | `VARCHAR(2000)` | 是 | - | - |
| `completed_at` | `DATETIME` | 是 | - | - |
| `created_at` | `DATETIME` | 否 | - | 应用默认值 |
| `updated_at` | `DATETIME` | 否 | - | 应用默认值 |
| `is_deleted` | `BOOL` | 否 | - | 应用默认值 |

索引：`ix_scenario_executions_dataset_item_id` (dataset_item_id)；`ix_scenario_executions_detection_task_id` (detection_task_id)；`ix_scenario_executions_result` (result)；`ix_scenario_executions_roi_id` (roi_id)；`ix_scenario_executions_scenario_version_id` (scenario_version_id)；`ix_scenario_executions_source` (source)；`ix_scenario_executions_status` (status)

## `detection_item_results`

检测任务中每张图片、每个 ROI、每个规则的结果。

| 字段 | MySQL 类型 | 可空 | 键 / 关联 | 默认值 |
| --- | --- | --- | --- | --- |
| `id` | `INTEGER` | 否 | PK | - |
| `task_id` | `INTEGER` | 否 | INDEX；FK → detection_tasks.id | - |
| `image_path` | `VARCHAR(500)` | 否 | - | - |
| `roi_id` | `INTEGER` | 否 | FK → regions_of_interest.id | - |
| `inspection_item_id` | `INTEGER` | 否 | FK → inspection_items.id | - |
| `status` | `VARCHAR(30)` | 否 | INDEX | - |
| `expected_json` | `JSON` | 否 | - | 应用默认值 |
| `actual_json` | `JSON` | 否 | - | 应用默认值 |
| `score` | `FLOAT` | 是 | - | - |
| `message` | `VARCHAR(1000)` | 是 | - | - |
| `roi_image_path` | `VARCHAR(500)` | 是 | - | - |
| `elapsed_ms` | `FLOAT` | 是 | - | - |
| `created_at` | `DATETIME` | 否 | - | 应用默认值 |
| `updated_at` | `DATETIME` | 否 | - | 应用默认值 |
| `is_deleted` | `BOOL` | 否 | - | 应用默认值 |

索引：`ix_detection_item_results_status` (status)；`ix_detection_item_results_task_id` (task_id)

## `scenario_execution_reviews`

执行结果的 VLM 复核与人工复判记录。

| 字段 | MySQL 类型 | 可空 | 键 / 关联 | 默认值 |
| --- | --- | --- | --- | --- |
| `id` | `INTEGER` | 否 | PK | - |
| `execution_id` | `INTEGER` | 否 | UNIQUE；INDEX；UNIQUE INDEX；FK → scenario_executions.id | - |
| `review_vlm_model_id` | `INTEGER` | 是 | FK → vlm_model_configs.id | - |
| `status` | `VARCHAR(30)` | 否 | INDEX | 应用默认值 |
| `verdict` | `VARCHAR(30)` | 是 | INDEX | - |
| `confidence` | `FLOAT` | 是 | - | - |
| `reason` | `VARCHAR(2000)` | 是 | - | - |
| `raw_output_json` | `JSON` | 否 | - | 应用默认值 |
| `reviewed_at` | `DATETIME` | 是 | - | - |
| `manual_verdict` | `VARCHAR(30)` | 是 | - | - |
| `manual_note` | `VARCHAR(2000)` | 是 | - | - |
| `manually_reviewed_at` | `DATETIME` | 是 | - | - |
| `created_at` | `DATETIME` | 否 | - | 应用默认值 |
| `updated_at` | `DATETIME` | 否 | - | 应用默认值 |
| `is_deleted` | `BOOL` | 否 | - | 应用默认值 |

索引：`ix_scenario_execution_reviews_execution_id` (execution_id)；`ix_scenario_execution_reviews_status` (status)；`ix_scenario_execution_reviews_verdict` (verdict)

## `reference_candidates`

历史候选基准记录；当前功能已下线，不再新增。

| 字段 | MySQL 类型 | 可空 | 键 / 关联 | 默认值 |
| --- | --- | --- | --- | --- |
| `id` | `INTEGER` | 否 | PK | - |
| `group_id` | `INTEGER` | 否 | INDEX；FK → reference_groups.id | - |
| `recipe_id` | `INTEGER` | 否 | INDEX；FK → recipes.id | - |
| `roi_id` | `INTEGER` | 否 | INDEX；FK → regions_of_interest.id | - |
| `source_task_id` | `INTEGER` | 否 | INDEX；FK → detection_tasks.id | - |
| `source_item_result_id` | `INTEGER` | 是 | FK → detection_item_results.id | - |
| `sn` | `VARCHAR(200)` | 否 | INDEX | - |
| `baseline_image_path` | `VARCHAR(500)` | 否 | - | - |
| `candidate_image_path` | `VARCHAR(500)` | 否 | - | - |
| `content_hash` | `VARCHAR(100)` | 否 | INDEX | - |
| `similarity_score` | `FLOAT` | 是 | - | - |
| `quality_json` | `JSON` | 否 | - | 应用默认值 |
| `rule_snapshot` | `JSON` | 否 | - | 应用默认值 |
| `vlm_result_json` | `JSON` | 否 | - | 应用默认值 |
| `vlm_confidence` | `FLOAT` | 是 | - | - |
| `status` | `VARCHAR(30)` | 否 | INDEX | 应用默认值 |
| `reason` | `VARCHAR(1000)` | 是 | - | - |
| `promoted_reference_image_id` | `INTEGER` | 是 | FK → reference_images.id | - |
| `created_at` | `DATETIME` | 否 | - | 应用默认值 |
| `updated_at` | `DATETIME` | 否 | - | 应用默认值 |
| `is_deleted` | `BOOL` | 否 | - | 应用默认值 |

索引：`ix_reference_candidates_content_hash` (content_hash)；`ix_reference_candidates_group_id` (group_id)；`ix_reference_candidates_recipe_id` (recipe_id)；`ix_reference_candidates_roi_id` (roi_id)；`ix_reference_candidates_sn` (sn)；`ix_reference_candidates_source_task_id` (source_task_id)；`ix_reference_candidates_status` (status)
