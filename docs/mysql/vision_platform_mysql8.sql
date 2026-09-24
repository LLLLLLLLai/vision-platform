-- Vision Platform MySQL 8 schema generated from SQLAlchemy metadata.
-- Apply to a new MySQL 8.0.17+ database after reviewing deployment-specific settings.
SET NAMES utf8mb4;

-- algorithm_configs: 算法能力与服务端点配置。
CREATE TABLE algorithm_configs (
	id INTEGER NOT NULL AUTO_INCREMENT,
	code VARCHAR(100) NOT NULL,
	name VARCHAR(200) NOT NULL,
	capability VARCHAR(100) NOT NULL,
	engine VARCHAR(100) NOT NULL,
	service_url VARCHAR(500),
	timeout_seconds FLOAT NOT NULL,
	config_json JSON NOT NULL,
	enabled BOOL NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	is_deleted BOOL NOT NULL,
	CONSTRAINT pk_algorithm_configs PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
CREATE INDEX ix_algorithm_configs_capability ON algorithm_configs (capability);
CREATE UNIQUE INDEX ix_algorithm_configs_code ON algorithm_configs (code);

-- detection_api_calls: 外部 detect 接口的请求、响应与调用方审计记录。
CREATE TABLE detection_api_calls (
	id INTEGER NOT NULL AUTO_INCREMENT,
	caller_ip VARCHAR(100) NOT NULL,
	called_at DATETIME NOT NULL,
	sn VARCHAR(200) NOT NULL,
	request_payload JSON NOT NULL,
	response_payload JSON NOT NULL,
	response_code INTEGER NOT NULL,
	call_status VARCHAR(30) NOT NULL,
	elapsed_ms FLOAT,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	is_deleted BOOL NOT NULL,
	CONSTRAINT pk_detection_api_calls PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
CREATE INDEX ix_detection_api_calls_call_status ON detection_api_calls (call_status);
CREATE INDEX ix_detection_api_calls_called_at ON detection_api_calls (called_at);
CREATE INDEX ix_detection_api_calls_caller_ip ON detection_api_calls (caller_ip);
CREATE INDEX ix_detection_api_calls_response_code ON detection_api_calls (response_code);
CREATE INDEX ix_detection_api_calls_sn ON detection_api_calls (sn);

-- inspection_scenarios: 检测场景主对象及当前已发布版本。
CREATE TABLE inspection_scenarios (
	id INTEGER NOT NULL AUTO_INCREMENT,
	code VARCHAR(100) NOT NULL,
	name VARCHAR(200) NOT NULL,
	category VARCHAR(100) NOT NULL,
	mode VARCHAR(30) NOT NULL,
	description VARCHAR(1000),
	published_version_id INTEGER,
	enabled BOOL NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	is_deleted BOOL NOT NULL,
	CONSTRAINT pk_inspection_scenarios PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
CREATE INDEX ix_inspection_scenarios_category ON inspection_scenarios (category);
CREATE UNIQUE INDEX ix_inspection_scenarios_code ON inspection_scenarios (code);
CREATE INDEX ix_inspection_scenarios_mode ON inspection_scenarios (mode);

-- model_registry: 基础模型和能力插件注册信息。
CREATE TABLE model_registry (
	id INTEGER NOT NULL AUTO_INCREMENT,
	code VARCHAR(100) NOT NULL,
	name VARCHAR(200) NOT NULL,
	capability VARCHAR(100) NOT NULL,
	provider VARCHAR(100) NOT NULL,
	runtime VARCHAR(100) NOT NULL,
	version VARCHAR(100),
	model_path VARCHAR(500),
	service_url VARCHAR(500),
	config_json JSON NOT NULL,
	enabled BOOL NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	is_deleted BOOL NOT NULL,
	CONSTRAINT pk_model_registry PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
CREATE INDEX ix_model_registry_capability ON model_registry (capability);
CREATE UNIQUE INDEX ix_model_registry_code ON model_registry (code);

-- products: 产品主数据。
CREATE TABLE products (
	id INTEGER NOT NULL AUTO_INCREMENT,
	code VARCHAR(100) NOT NULL,
	name VARCHAR(200) NOT NULL,
	description VARCHAR(500),
	enabled BOOL NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	is_deleted BOOL NOT NULL,
	CONSTRAINT pk_products PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
CREATE UNIQUE INDEX ix_products_code ON products (code);

-- reference_groups: 历史 DINOv2 相似度参考组与向量矩阵信息；当前仅保留追溯。
CREATE TABLE reference_groups (
	id INTEGER NOT NULL AUTO_INCREMENT,
	code VARCHAR(100) NOT NULL,
	name VARCHAR(200) NOT NULL,
	object_type VARCHAR(100) NOT NULL,
	class_code VARCHAR(100) NOT NULL,
	description VARCHAR(500),
	enabled BOOL NOT NULL,
	embedding_set_version INTEGER NOT NULL,
	embedding_matrix_path VARCHAR(500),
	embedding_manifest_path VARCHAR(500),
	embedding_count INTEGER NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	is_deleted BOOL NOT NULL,
	CONSTRAINT pk_reference_groups PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
CREATE INDEX ix_reference_groups_class_code ON reference_groups (class_code);
CREATE UNIQUE INDEX ix_reference_groups_code ON reference_groups (code);
CREATE INDEX ix_reference_groups_object_type ON reference_groups (object_type);

-- reference_object_types: 统一的视觉物体类型字典。
CREATE TABLE reference_object_types (
	id INTEGER NOT NULL AUTO_INCREMENT,
	code VARCHAR(100) NOT NULL,
	name VARCHAR(200) NOT NULL,
	description VARCHAR(500),
	enabled BOOL NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	is_deleted BOOL NOT NULL,
	CONSTRAINT pk_reference_object_types PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
CREATE UNIQUE INDEX ix_reference_object_types_code ON reference_object_types (code);

-- stations: 工位主数据。
CREATE TABLE stations (
	id INTEGER NOT NULL AUTO_INCREMENT,
	code VARCHAR(100) NOT NULL,
	name VARCHAR(200) NOT NULL,
	line_code VARCHAR(100),
	process_code VARCHAR(100),
	enabled BOOL NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	is_deleted BOOL NOT NULL,
	CONSTRAINT pk_stations PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
CREATE UNIQUE INDEX ix_stations_code ON stations (code);

-- vision_models: 可训练视觉模型的逻辑定义和类别集合。
CREATE TABLE vision_models (
	id INTEGER NOT NULL AUTO_INCREMENT,
	code VARCHAR(100) NOT NULL,
	name VARCHAR(200) NOT NULL,
	task_type VARCHAR(50) NOT NULL,
	description VARCHAR(1000),
	base_model VARCHAR(300),
	labels_json JSON NOT NULL,
	enabled BOOL NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	is_deleted BOOL NOT NULL,
	CONSTRAINT pk_vision_models PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
CREATE UNIQUE INDEX ix_vision_models_code ON vision_models (code);
CREATE INDEX ix_vision_models_task_type ON vision_models (task_type);

-- vlm_model_configs: OpenAI 兼容 VLM 的连接、模型与推理参数配置。
CREATE TABLE vlm_model_configs (
	id INTEGER NOT NULL AUTO_INCREMENT,
	code VARCHAR(100) NOT NULL,
	name VARCHAR(200) NOT NULL,
	base_url VARCHAR(500) NOT NULL,
	model_name VARCHAR(200) NOT NULL,
	api_key_ciphertext VARCHAR(2000),
	api_key_env_name VARCHAR(200),
	api_key_hint VARCHAR(30),
	thinking_enabled BOOL NOT NULL,
	extra_params_json JSON NOT NULL,
	temperature FLOAT NOT NULL,
	max_tokens INTEGER NOT NULL,
	timeout_seconds FLOAT NOT NULL,
	enabled BOOL NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	is_deleted BOOL NOT NULL,
	CONSTRAINT pk_vlm_model_configs PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
CREATE UNIQUE INDEX ix_vlm_model_configs_code ON vlm_model_configs (code);

-- inspection_scenario_versions: 检测场景的版本化定义、提示词、输入输出 Schema 和发布状态。
CREATE TABLE inspection_scenario_versions (
	id INTEGER NOT NULL AUTO_INCREMENT,
	scenario_id INTEGER NOT NULL,
	version VARCHAR(50) NOT NULL,
	status VARCHAR(30) NOT NULL,
	prompt_template VARCHAR(8000),
	input_schema_json JSON NOT NULL,
	output_schema_json JSON NOT NULL,
	definition_json JSON NOT NULL,
	primary_vlm_model_id INTEGER,
	review_vlm_model_id INTEGER,
	published_at DATETIME,
	published_by VARCHAR(100),
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	is_deleted BOOL NOT NULL,
	CONSTRAINT pk_inspection_scenario_versions PRIMARY KEY (id),
	CONSTRAINT uq_scene_version UNIQUE (scenario_id, version),
	CONSTRAINT fk_inspection_scenario_versions_scenario_id_inspection_scenarios FOREIGN KEY(scenario_id) REFERENCES inspection_scenarios (id),
	CONSTRAINT fk_inspection_scenario_versions_primary_vlm_model_id_vlm_d6b5 FOREIGN KEY(primary_vlm_model_id) REFERENCES vlm_model_configs (id),
	CONSTRAINT fk_inspection_scenario_versions_review_vlm_model_id_vlm__a833 FOREIGN KEY(review_vlm_model_id) REFERENCES vlm_model_configs (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
CREATE INDEX ix_inspection_scenario_versions_primary_vlm_model_id ON inspection_scenario_versions (primary_vlm_model_id);
CREATE INDEX ix_inspection_scenario_versions_review_vlm_model_id ON inspection_scenario_versions (review_vlm_model_id);
CREATE INDEX ix_inspection_scenario_versions_scenario_id ON inspection_scenario_versions (scenario_id);
CREATE INDEX ix_inspection_scenario_versions_status ON inspection_scenario_versions (status);

-- product_scenes: 产品二维空间世界模型及图像对齐配置。
CREATE TABLE product_scenes (
	id INTEGER NOT NULL AUTO_INCREMENT,
	product_id INTEGER NOT NULL,
	code VARCHAR(100) NOT NULL,
	name VARCHAR(200) NOT NULL,
	version VARCHAR(50) NOT NULL,
	status VARCHAR(30) NOT NULL,
	coordinate_system VARCHAR(30) NOT NULL,
	reference_image_path VARCHAR(500),
	reference_width INTEGER,
	reference_height INTEGER,
	alignment_config JSON NOT NULL,
	enabled BOOL NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	is_deleted BOOL NOT NULL,
	CONSTRAINT pk_product_scenes PRIMARY KEY (id),
	CONSTRAINT fk_product_scenes_product_id_products FOREIGN KEY(product_id) REFERENCES products (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
CREATE UNIQUE INDEX ix_product_scenes_code ON product_scenes (code);
CREATE INDEX ix_product_scenes_product_id ON product_scenes (product_id);
CREATE INDEX ix_product_scenes_status ON product_scenes (status);

-- recipes: 按拉线、物料、工序、相机、拍照次数管理的工艺配方版本。
CREATE TABLE recipes (
	id INTEGER NOT NULL AUTO_INCREMENT,
	code VARCHAR(100) NOT NULL,
	recipe_family_code VARCHAR(100) NOT NULL,
	version_no INTEGER NOT NULL,
	source_recipe_id INTEGER,
	name VARCHAR(200) NOT NULL,
	version VARCHAR(50) NOT NULL,
	status VARCHAR(30) NOT NULL,
	project_name VARCHAR(200),
	product_id INTEGER NOT NULL,
	station_id INTEGER NOT NULL,
	line_code VARCHAR(100),
	material_code VARCHAR(100),
	process_code VARCHAR(100),
	camera_code VARCHAR(100),
	capture_index INTEGER NOT NULL,
	base_image_path VARCHAR(500),
	reference_width INTEGER,
	reference_height INTEGER,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	is_deleted BOOL NOT NULL,
	CONSTRAINT pk_recipes PRIMARY KEY (id),
	CONSTRAINT fk_recipes_source_recipe_id_recipes FOREIGN KEY(source_recipe_id) REFERENCES recipes (id),
	CONSTRAINT fk_recipes_product_id_products FOREIGN KEY(product_id) REFERENCES products (id),
	CONSTRAINT fk_recipes_station_id_stations FOREIGN KEY(station_id) REFERENCES stations (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
CREATE INDEX ix_recipe_business_key ON recipes (line_code, material_code, process_code, camera_code, capture_index, status);
CREATE INDEX ix_recipes_code ON recipes (code);
CREATE INDEX ix_recipes_recipe_family_code ON recipes (recipe_family_code);
CREATE INDEX ix_recipes_source_recipe_id ON recipes (source_recipe_id);
CREATE INDEX ix_recipes_status ON recipes (status);

-- reference_images: 历史参考组中的标准图及其向量索引；当前仅保留追溯。
CREATE TABLE reference_images (
	id INTEGER NOT NULL AUTO_INCREMENT,
	group_id INTEGER NOT NULL,
	image_path VARCHAR(500) NOT NULL,
	embedding_path VARCHAR(500),
	embedding_dimension INTEGER,
	embedding_index INTEGER,
	model_code VARCHAR(100) NOT NULL,
	model_version VARCHAR(100),
	quality_status VARCHAR(30) NOT NULL,
	enabled BOOL NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	is_deleted BOOL NOT NULL,
	CONSTRAINT pk_reference_images PRIMARY KEY (id),
	CONSTRAINT fk_reference_images_group_id_reference_groups FOREIGN KEY(group_id) REFERENCES reference_groups (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
CREATE INDEX ix_reference_images_group_id ON reference_images (group_id);

-- datasets: 场景评测集和 YOLO 训练集定义。
CREATE TABLE datasets (
	id INTEGER NOT NULL AUTO_INCREMENT,
	code VARCHAR(100) NOT NULL,
	name VARCHAR(200) NOT NULL,
	description VARCHAR(1000),
	purpose VARCHAR(30) NOT NULL,
	media_type VARCHAR(30) NOT NULL,
	annotation_type VARCHAR(30) NOT NULL,
	label_schema_json JSON NOT NULL,
	collection_scenario_version_id INTEGER,
	auto_collect_enabled BOOL NOT NULL,
	auto_collect_limit INTEGER NOT NULL,
	revision INTEGER NOT NULL,
	enabled BOOL NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	is_deleted BOOL NOT NULL,
	CONSTRAINT pk_datasets PRIMARY KEY (id),
	CONSTRAINT fk_datasets_collection_scenario_version_id_inspection_sc_f0cf FOREIGN KEY(collection_scenario_version_id) REFERENCES inspection_scenario_versions (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
CREATE UNIQUE INDEX ix_datasets_code ON datasets (code);
CREATE INDEX ix_datasets_collection_scenario_version_id ON datasets (collection_scenario_version_id);
CREATE INDEX ix_datasets_purpose ON datasets (purpose);

-- detection_tasks: 一次工艺配方检测任务的总记录。
CREATE TABLE detection_tasks (
	id INTEGER NOT NULL AUTO_INCREMENT,
	request_id VARCHAR(100) NOT NULL,
	sn VARCHAR(200) NOT NULL,
	recipe_id INTEGER NOT NULL,
	recipe_version VARCHAR(50) NOT NULL,
	status VARCHAR(30) NOT NULL,
	original_image_paths JSON NOT NULL,
	result_image_paths JSON NOT NULL,
	elapsed_ms FLOAT,
	error_message VARCHAR(1000),
	completed_at DATETIME,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	is_deleted BOOL NOT NULL,
	CONSTRAINT pk_detection_tasks PRIMARY KEY (id),
	CONSTRAINT fk_detection_tasks_recipe_id_recipes FOREIGN KEY(recipe_id) REFERENCES recipes (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
CREATE INDEX ix_detection_tasks_recipe_id ON detection_tasks (recipe_id);
CREATE UNIQUE INDEX ix_detection_tasks_request_id ON detection_tasks (request_id);
CREATE INDEX ix_detection_tasks_sn ON detection_tasks (sn);
CREATE INDEX ix_detection_tasks_status ON detection_tasks (status);

-- recipe_feature_anchors: 工艺配方的图像定位特征点。
CREATE TABLE recipe_feature_anchors (
	id INTEGER NOT NULL AUTO_INCREMENT,
	recipe_id INTEGER NOT NULL,
	code VARCHAR(100) NOT NULL,
	name VARCHAR(200) NOT NULL,
	x_ratio FLOAT NOT NULL,
	y_ratio FLOAT NOT NULL,
	width_ratio FLOAT NOT NULL,
	height_ratio FLOAT NOT NULL,
	padding INTEGER NOT NULL,
	enabled BOOL NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	is_deleted BOOL NOT NULL,
	CONSTRAINT pk_recipe_feature_anchors PRIMARY KEY (id),
	CONSTRAINT uq_recipe_feature_anchor UNIQUE (recipe_id),
	CONSTRAINT fk_recipe_feature_anchors_recipe_id_recipes FOREIGN KEY(recipe_id) REFERENCES recipes (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
CREATE INDEX ix_recipe_feature_anchors_recipe_id ON recipe_feature_anchors (recipe_id);

-- scenario_edges: 流程场景节点之间的数据连线和映射。
CREATE TABLE scenario_edges (
	id INTEGER NOT NULL AUTO_INCREMENT,
	scenario_version_id INTEGER NOT NULL,
	source_node_key VARCHAR(100) NOT NULL,
	target_node_key VARCHAR(100) NOT NULL,
	mapping_json JSON NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	is_deleted BOOL NOT NULL,
	CONSTRAINT pk_scenario_edges PRIMARY KEY (id),
	CONSTRAINT uq_scene_edge UNIQUE (scenario_version_id, source_node_key, target_node_key),
	CONSTRAINT fk_scenario_edges_scenario_version_id_inspection_scenari_ceb2 FOREIGN KEY(scenario_version_id) REFERENCES inspection_scenario_versions (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
CREATE INDEX ix_scenario_edges_scenario_version_id ON scenario_edges (scenario_version_id);

-- scenario_nodes: 流程场景的节点配置、坐标与排序。
CREATE TABLE scenario_nodes (
	id INTEGER NOT NULL AUTO_INCREMENT,
	scenario_version_id INTEGER NOT NULL,
	node_key VARCHAR(100) NOT NULL,
	name VARCHAR(200) NOT NULL,
	node_type VARCHAR(50) NOT NULL,
	config_json JSON NOT NULL,
	sort_order INTEGER NOT NULL,
	canvas_x FLOAT NOT NULL,
	canvas_y FLOAT NOT NULL,
	enabled BOOL NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	is_deleted BOOL NOT NULL,
	CONSTRAINT pk_scenario_nodes PRIMARY KEY (id),
	CONSTRAINT uq_scene_node_key UNIQUE (scenario_version_id, node_key),
	CONSTRAINT fk_scenario_nodes_scenario_version_id_inspection_scenari_36e8 FOREIGN KEY(scenario_version_id) REFERENCES inspection_scenario_versions (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
CREATE INDEX ix_scenario_nodes_node_type ON scenario_nodes (node_type);
CREATE INDEX ix_scenario_nodes_scenario_version_id ON scenario_nodes (scenario_version_id);

-- scene_objects: 产品世界模型中的对象、位置和期望状态。
CREATE TABLE scene_objects (
	id INTEGER NOT NULL AUTO_INCREMENT,
	scene_id INTEGER NOT NULL,
	parent_object_id INTEGER,
	code VARCHAR(100) NOT NULL,
	name VARCHAR(200) NOT NULL,
	object_type VARCHAR(100) NOT NULL,
	location_mode VARCHAR(30) NOT NULL,
	geometry JSON NOT NULL,
	expected_state JSON NOT NULL,
	perception_config JSON NOT NULL,
	sort_order INTEGER NOT NULL,
	enabled BOOL NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	is_deleted BOOL NOT NULL,
	CONSTRAINT pk_scene_objects PRIMARY KEY (id),
	CONSTRAINT fk_scene_objects_scene_id_product_scenes FOREIGN KEY(scene_id) REFERENCES product_scenes (id),
	CONSTRAINT fk_scene_objects_parent_object_id_scene_objects FOREIGN KEY(parent_object_id) REFERENCES scene_objects (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
CREATE INDEX ix_scene_objects_code ON scene_objects (code);
CREATE INDEX ix_scene_objects_object_type ON scene_objects (object_type);
CREATE INDEX ix_scene_objects_scene_id ON scene_objects (scene_id);

-- automation_jobs: 场景评测、提示词优化、模型训练和异步复核任务。
CREATE TABLE automation_jobs (
	id INTEGER NOT NULL AUTO_INCREMENT,
	job_type VARCHAR(50) NOT NULL,
	status VARCHAR(30) NOT NULL,
	scenario_version_id INTEGER,
	dataset_id INTEGER,
	vision_model_id INTEGER,
	config_json JSON NOT NULL,
	input_snapshot_json JSON NOT NULL,
	result_json JSON NOT NULL,
	log_path VARCHAR(1000),
	error_message VARCHAR(2000),
	started_at DATETIME,
	completed_at DATETIME,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	is_deleted BOOL NOT NULL,
	CONSTRAINT pk_automation_jobs PRIMARY KEY (id),
	CONSTRAINT fk_automation_jobs_scenario_version_id_inspection_scenar_7292 FOREIGN KEY(scenario_version_id) REFERENCES inspection_scenario_versions (id),
	CONSTRAINT fk_automation_jobs_dataset_id_datasets FOREIGN KEY(dataset_id) REFERENCES datasets (id),
	CONSTRAINT fk_automation_jobs_vision_model_id_vision_models FOREIGN KEY(vision_model_id) REFERENCES vision_models (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
CREATE INDEX ix_automation_jobs_job_type ON automation_jobs (job_type);
CREATE INDEX ix_automation_jobs_scenario_version_id ON automation_jobs (scenario_version_id);
CREATE INDEX ix_automation_jobs_status ON automation_jobs (status);

-- dataset_items: 数据集素材、真值、标注和自动切分信息。
CREATE TABLE dataset_items (
	id INTEGER NOT NULL AUTO_INCREMENT,
	dataset_id INTEGER NOT NULL,
	media_path VARCHAR(1000) NOT NULL,
	original_name VARCHAR(500) NOT NULL,
	media_type VARCHAR(30) NOT NULL,
	ground_truth VARCHAR(30),
	annotation_status VARCHAR(30) NOT NULL,
	annotation_json JSON NOT NULL,
	split VARCHAR(30),
	source VARCHAR(30) NOT NULL,
	content_hash VARCHAR(128),
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	is_deleted BOOL NOT NULL,
	CONSTRAINT pk_dataset_items PRIMARY KEY (id),
	CONSTRAINT fk_dataset_items_dataset_id_datasets FOREIGN KEY(dataset_id) REFERENCES datasets (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
CREATE INDEX ix_dataset_items_content_hash ON dataset_items (content_hash);
CREATE INDEX ix_dataset_items_dataset_id ON dataset_items (dataset_id);
CREATE INDEX ix_dataset_items_ground_truth ON dataset_items (ground_truth);

-- object_relations: 产品世界模型中对象之间的期望关系。
CREATE TABLE object_relations (
	id INTEGER NOT NULL AUTO_INCREMENT,
	scene_id INTEGER NOT NULL,
	source_object_id INTEGER NOT NULL,
	target_object_id INTEGER NOT NULL,
	relation_type VARCHAR(100) NOT NULL,
	expected_relation JSON NOT NULL,
	enabled BOOL NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	is_deleted BOOL NOT NULL,
	CONSTRAINT pk_object_relations PRIMARY KEY (id),
	CONSTRAINT fk_object_relations_scene_id_product_scenes FOREIGN KEY(scene_id) REFERENCES product_scenes (id),
	CONSTRAINT fk_object_relations_source_object_id_scene_objects FOREIGN KEY(source_object_id) REFERENCES scene_objects (id),
	CONSTRAINT fk_object_relations_target_object_id_scene_objects FOREIGN KEY(target_object_id) REFERENCES scene_objects (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
CREATE INDEX ix_object_relations_relation_type ON object_relations (relation_type);
CREATE INDEX ix_object_relations_scene_id ON object_relations (scene_id);
CREATE INDEX ix_object_relations_source_object_id ON object_relations (source_object_id);
CREATE INDEX ix_object_relations_target_object_id ON object_relations (target_object_id);

-- regions_of_interest: 配方中的 ROI 检测区域和归一化坐标。
CREATE TABLE regions_of_interest (
	id INTEGER NOT NULL AUTO_INCREMENT,
	recipe_id INTEGER NOT NULL,
	scene_object_id INTEGER,
	code VARCHAR(100) NOT NULL,
	name VARCHAR(200) NOT NULL,
	object_type VARCHAR(100),
	shape_type VARCHAR(30) NOT NULL,
	x_ratio FLOAT NOT NULL,
	y_ratio FLOAT NOT NULL,
	width_ratio FLOAT NOT NULL,
	height_ratio FLOAT NOT NULL,
	pixel_coordinates JSON NOT NULL,
	padding INTEGER NOT NULL,
	sort_order INTEGER NOT NULL,
	alignment_anchor BOOL NOT NULL,
	enabled BOOL NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	is_deleted BOOL NOT NULL,
	CONSTRAINT pk_regions_of_interest PRIMARY KEY (id),
	CONSTRAINT fk_regions_of_interest_recipe_id_recipes FOREIGN KEY(recipe_id) REFERENCES recipes (id),
	CONSTRAINT fk_regions_of_interest_scene_object_id_scene_objects FOREIGN KEY(scene_object_id) REFERENCES scene_objects (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
CREATE INDEX ix_regions_of_interest_code ON regions_of_interest (code);
CREATE INDEX ix_regions_of_interest_recipe_id ON regions_of_interest (recipe_id);
CREATE INDEX ix_regions_of_interest_scene_object_id ON regions_of_interest (scene_object_id);

-- vision_model_versions: YOLO 训练权重、指标、训练产物及发布状态。
CREATE TABLE vision_model_versions (
	id INTEGER NOT NULL AUTO_INCREMENT,
	vision_model_id INTEGER NOT NULL,
	dataset_id INTEGER,
	version VARCHAR(50) NOT NULL,
	status VARCHAR(30) NOT NULL,
	weights_path VARCHAR(1000),
	metrics_json JSON NOT NULL,
	artifact_paths_json JSON NOT NULL,
	published_at DATETIME,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	is_deleted BOOL NOT NULL,
	CONSTRAINT pk_vision_model_versions PRIMARY KEY (id),
	CONSTRAINT uq_vision_model_version UNIQUE (vision_model_id, version),
	CONSTRAINT fk_vision_model_versions_vision_model_id_vision_models FOREIGN KEY(vision_model_id) REFERENCES vision_models (id),
	CONSTRAINT fk_vision_model_versions_dataset_id_datasets FOREIGN KEY(dataset_id) REFERENCES datasets (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
CREATE INDEX ix_vision_model_versions_status ON vision_model_versions (status);
CREATE INDEX ix_vision_model_versions_vision_model_id ON vision_model_versions (vision_model_id);

-- inspection_items: ROI 下的旧规则引擎检测项，兼容既有检测接口。
CREATE TABLE inspection_items (
	id INTEGER NOT NULL AUTO_INCREMENT,
	roi_id INTEGER NOT NULL,
	code VARCHAR(100) NOT NULL,
	name VARCHAR(200) NOT NULL,
	inspection_type VARCHAR(100) NOT NULL,
	capability VARCHAR(100) NOT NULL,
	algorithm_config_id INTEGER,
	reference_group_id INTEGER,
	expected_json JSON NOT NULL,
	rule_json JSON NOT NULL,
	execution_order INTEGER NOT NULL,
	required BOOL NOT NULL,
	enabled BOOL NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	is_deleted BOOL NOT NULL,
	CONSTRAINT pk_inspection_items PRIMARY KEY (id),
	CONSTRAINT fk_inspection_items_roi_id_regions_of_interest FOREIGN KEY(roi_id) REFERENCES regions_of_interest (id),
	CONSTRAINT fk_inspection_items_algorithm_config_id_algorithm_configs FOREIGN KEY(algorithm_config_id) REFERENCES algorithm_configs (id),
	CONSTRAINT fk_inspection_items_reference_group_id_reference_groups FOREIGN KEY(reference_group_id) REFERENCES reference_groups (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
CREATE INDEX ix_inspection_items_capability ON inspection_items (capability);
CREATE INDEX ix_inspection_items_code ON inspection_items (code);
CREATE INDEX ix_inspection_items_roi_id ON inspection_items (roi_id);

-- roi_scenario_bindings: ROI 到已发布检测场景版本的绑定与变量映射。
CREATE TABLE roi_scenario_bindings (
	id INTEGER NOT NULL AUTO_INCREMENT,
	roi_id INTEGER NOT NULL,
	scenario_version_id INTEGER NOT NULL,
	input_mapping_json JSON NOT NULL,
	enabled BOOL NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	is_deleted BOOL NOT NULL,
	CONSTRAINT pk_roi_scenario_bindings PRIMARY KEY (id),
	CONSTRAINT fk_roi_scenario_bindings_roi_id_regions_of_interest FOREIGN KEY(roi_id) REFERENCES regions_of_interest (id),
	CONSTRAINT fk_roi_scenario_bindings_scenario_version_id_inspection__5da4 FOREIGN KEY(scenario_version_id) REFERENCES inspection_scenario_versions (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
CREATE UNIQUE INDEX ix_roi_scenario_bindings_roi_id ON roi_scenario_bindings (roi_id);
CREATE INDEX ix_roi_scenario_bindings_scenario_version_id ON roi_scenario_bindings (scenario_version_id);

-- scenario_executions: 场景在生产、测试或评测中的一次执行记录。
CREATE TABLE scenario_executions (
	id INTEGER NOT NULL AUTO_INCREMENT,
	scenario_version_id INTEGER NOT NULL,
	detection_task_id INTEGER,
	roi_id INTEGER,
	dataset_item_id INTEGER,
	source VARCHAR(30) NOT NULL,
	status VARCHAR(30) NOT NULL,
	result VARCHAR(30),
	score FLOAT,
	input_json JSON NOT NULL,
	output_json JSON NOT NULL,
	elapsed_ms FLOAT,
	error_message VARCHAR(2000),
	completed_at DATETIME,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	is_deleted BOOL NOT NULL,
	CONSTRAINT pk_scenario_executions PRIMARY KEY (id),
	CONSTRAINT fk_scenario_executions_scenario_version_id_inspection_sc_41c8 FOREIGN KEY(scenario_version_id) REFERENCES inspection_scenario_versions (id),
	CONSTRAINT fk_scenario_executions_detection_task_id_detection_tasks FOREIGN KEY(detection_task_id) REFERENCES detection_tasks (id),
	CONSTRAINT fk_scenario_executions_roi_id_regions_of_interest FOREIGN KEY(roi_id) REFERENCES regions_of_interest (id),
	CONSTRAINT fk_scenario_executions_dataset_item_id_dataset_items FOREIGN KEY(dataset_item_id) REFERENCES dataset_items (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
CREATE INDEX ix_scenario_executions_dataset_item_id ON scenario_executions (dataset_item_id);
CREATE INDEX ix_scenario_executions_detection_task_id ON scenario_executions (detection_task_id);
CREATE INDEX ix_scenario_executions_result ON scenario_executions (result);
CREATE INDEX ix_scenario_executions_roi_id ON scenario_executions (roi_id);
CREATE INDEX ix_scenario_executions_scenario_version_id ON scenario_executions (scenario_version_id);
CREATE INDEX ix_scenario_executions_source ON scenario_executions (source);
CREATE INDEX ix_scenario_executions_status ON scenario_executions (status);

-- detection_item_results: 检测任务中每张图片、每个 ROI、每个规则的结果。
CREATE TABLE detection_item_results (
	id INTEGER NOT NULL AUTO_INCREMENT,
	task_id INTEGER NOT NULL,
	image_path VARCHAR(500) NOT NULL,
	roi_id INTEGER NOT NULL,
	inspection_item_id INTEGER NOT NULL,
	status VARCHAR(30) NOT NULL,
	expected_json JSON NOT NULL,
	actual_json JSON NOT NULL,
	score FLOAT,
	message VARCHAR(1000),
	roi_image_path VARCHAR(500),
	elapsed_ms FLOAT,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	is_deleted BOOL NOT NULL,
	CONSTRAINT pk_detection_item_results PRIMARY KEY (id),
	CONSTRAINT fk_detection_item_results_task_id_detection_tasks FOREIGN KEY(task_id) REFERENCES detection_tasks (id),
	CONSTRAINT fk_detection_item_results_roi_id_regions_of_interest FOREIGN KEY(roi_id) REFERENCES regions_of_interest (id),
	CONSTRAINT fk_detection_item_results_inspection_item_id_inspection_items FOREIGN KEY(inspection_item_id) REFERENCES inspection_items (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
CREATE INDEX ix_detection_item_results_status ON detection_item_results (status);
CREATE INDEX ix_detection_item_results_task_id ON detection_item_results (task_id);

-- scenario_execution_reviews: 执行结果的 VLM 复核与人工复判记录。
CREATE TABLE scenario_execution_reviews (
	id INTEGER NOT NULL AUTO_INCREMENT,
	execution_id INTEGER NOT NULL,
	review_vlm_model_id INTEGER,
	status VARCHAR(30) NOT NULL,
	verdict VARCHAR(30),
	confidence FLOAT,
	reason VARCHAR(2000),
	raw_output_json JSON NOT NULL,
	reviewed_at DATETIME,
	manual_verdict VARCHAR(30),
	manual_note VARCHAR(2000),
	manually_reviewed_at DATETIME,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	is_deleted BOOL NOT NULL,
	CONSTRAINT pk_scenario_execution_reviews PRIMARY KEY (id),
	CONSTRAINT fk_scenario_execution_reviews_execution_id_scenario_executions FOREIGN KEY(execution_id) REFERENCES scenario_executions (id),
	CONSTRAINT fk_scenario_execution_reviews_review_vlm_model_id_vlm_mo_6363 FOREIGN KEY(review_vlm_model_id) REFERENCES vlm_model_configs (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
CREATE UNIQUE INDEX ix_scenario_execution_reviews_execution_id ON scenario_execution_reviews (execution_id);
CREATE INDEX ix_scenario_execution_reviews_status ON scenario_execution_reviews (status);
CREATE INDEX ix_scenario_execution_reviews_verdict ON scenario_execution_reviews (verdict);

-- reference_candidates: 历史候选基准记录；当前功能已下线，不再新增。
CREATE TABLE reference_candidates (
	id INTEGER NOT NULL AUTO_INCREMENT,
	group_id INTEGER NOT NULL,
	recipe_id INTEGER NOT NULL,
	roi_id INTEGER NOT NULL,
	source_task_id INTEGER NOT NULL,
	source_item_result_id INTEGER,
	sn VARCHAR(200) NOT NULL,
	baseline_image_path VARCHAR(500) NOT NULL,
	candidate_image_path VARCHAR(500) NOT NULL,
	content_hash VARCHAR(100) NOT NULL,
	similarity_score FLOAT,
	quality_json JSON NOT NULL,
	rule_snapshot JSON NOT NULL,
	vlm_result_json JSON NOT NULL,
	vlm_confidence FLOAT,
	status VARCHAR(30) NOT NULL,
	reason VARCHAR(1000),
	promoted_reference_image_id INTEGER,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	is_deleted BOOL NOT NULL,
	CONSTRAINT pk_reference_candidates PRIMARY KEY (id),
	CONSTRAINT fk_reference_candidates_group_id_reference_groups FOREIGN KEY(group_id) REFERENCES reference_groups (id),
	CONSTRAINT fk_reference_candidates_recipe_id_recipes FOREIGN KEY(recipe_id) REFERENCES recipes (id),
	CONSTRAINT fk_reference_candidates_roi_id_regions_of_interest FOREIGN KEY(roi_id) REFERENCES regions_of_interest (id),
	CONSTRAINT fk_reference_candidates_source_task_id_detection_tasks FOREIGN KEY(source_task_id) REFERENCES detection_tasks (id),
	CONSTRAINT fk_reference_candidates_source_item_result_id_detection__4dd6 FOREIGN KEY(source_item_result_id) REFERENCES detection_item_results (id),
	CONSTRAINT fk_reference_candidates_promoted_reference_image_id_refe_6e7a FOREIGN KEY(promoted_reference_image_id) REFERENCES reference_images (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
CREATE INDEX ix_reference_candidates_content_hash ON reference_candidates (content_hash);
CREATE INDEX ix_reference_candidates_group_id ON reference_candidates (group_id);
CREATE INDEX ix_reference_candidates_recipe_id ON reference_candidates (recipe_id);
CREATE INDEX ix_reference_candidates_roi_id ON reference_candidates (roi_id);
CREATE INDEX ix_reference_candidates_sn ON reference_candidates (sn);
CREATE INDEX ix_reference_candidates_source_task_id ON reference_candidates (source_task_id);
CREATE INDEX ix_reference_candidates_status ON reference_candidates (status);
