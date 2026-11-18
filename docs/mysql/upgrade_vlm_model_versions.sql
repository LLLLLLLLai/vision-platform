CREATE TABLE IF NOT EXISTS vlm_model_versions (
    id INTEGER NOT NULL AUTO_INCREMENT,
    vlm_model_id INTEGER NOT NULL,
    version INTEGER NOT NULL,
    config_json JSON NOT NULL,
    api_key_ciphertext VARCHAR(2000),
    api_key_env_name VARCHAR(200),
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    is_deleted BOOL NOT NULL,
    CONSTRAINT pk_vlm_model_versions PRIMARY KEY (id),
    CONSTRAINT uq_vlm_config_version UNIQUE (vlm_model_id, version),
    CONSTRAINT fk_vlm_model_versions_vlm_model_id_vlm_model_configs
        FOREIGN KEY (vlm_model_id) REFERENCES vlm_model_configs (id),
    INDEX ix_vlm_model_versions_vlm_model_id (vlm_model_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
