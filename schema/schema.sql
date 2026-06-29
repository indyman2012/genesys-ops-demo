CREATE TABLE IF NOT EXISTS schema_version (
  id INT PRIMARY KEY AUTO_INCREMENT,
  version VARCHAR(50) NOT NULL,
  applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS tool_runs (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  run_id VARCHAR(64) NOT NULL,
  tool_id VARCHAR(128) NOT NULL,
  mode VARCHAR(32) NOT NULL,
  started_at DATETIME NOT NULL,
  finished_at DATETIME NULL,
  status VARCHAR(32) NOT NULL,
  input_file VARCHAR(512) NULL,
  log_file VARCHAR(512) NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_tool_runs_run_id (run_id)
);
CREATE TABLE IF NOT EXISTS api_payloads (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  run_id VARCHAR(64) NULL,
  label VARCHAR(255) NOT NULL,
  payload_json JSON NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS gc_users (
  id VARCHAR(64) PRIMARY KEY,
  name VARCHAR(255), email VARCHAR(255), username VARCHAR(255), title VARCHAR(255),
  division_id VARCHAR(64), division_name VARCHAR(255), state VARCHAR(64),
  raw_json JSON, collected_at DATETIME NOT NULL,
  INDEX idx_gc_users_email (email), INDEX idx_gc_users_division (division_name)
);
CREATE TABLE IF NOT EXISTS gc_queues (
  id VARCHAR(64) PRIMARY KEY,
  name VARCHAR(255) NOT NULL, division_id VARCHAR(64), division_name VARCHAR(255),
  member_count INT, joined_member_count INT, raw_json JSON, collected_at DATETIME NOT NULL,
  INDEX idx_gc_queues_name (name), INDEX idx_gc_queues_division (division_name)
);
CREATE TABLE IF NOT EXISTS gc_queue_members (
  queue_id VARCHAR(64) NOT NULL,
  user_id VARCHAR(64) NOT NULL,
  queue_name VARCHAR(255), user_name VARCHAR(255), joined BOOLEAN,
  raw_json JSON, collected_at DATETIME NOT NULL,
  PRIMARY KEY (queue_id, user_id), INDEX idx_qm_user (user_id), INDEX idx_qm_joined (joined)
);
CREATE TABLE IF NOT EXISTS gc_locations (
  id VARCHAR(64) PRIMARY KEY,
  name VARCHAR(255) NOT NULL, emergency_number VARCHAR(64),
  address_json JSON, raw_json JSON, collected_at DATETIME NOT NULL,
  INDEX idx_locations_name (name)
);
CREATE TABLE IF NOT EXISTS gc_sites (
  id VARCHAR(64) PRIMARY KEY,
  name VARCHAR(255) NOT NULL, location_id VARCHAR(64), location_name VARCHAR(255), state VARCHAR(64),
  raw_json JSON, collected_at DATETIME NOT NULL,
  INDEX idx_sites_name (name), INDEX idx_sites_location (location_name)
);
CREATE TABLE IF NOT EXISTS gc_phones (
  id VARCHAR(64) PRIMARY KEY,
  name VARCHAR(255) NOT NULL, site_id VARCHAR(64), site_name VARCHAR(255), phone_base_id VARCHAR(64),
  raw_json JSON, collected_at DATETIME NOT NULL,
  INDEX idx_phones_name (name), INDEX idx_phones_site (site_name)
);
CREATE TABLE IF NOT EXISTS gc_conversations (
  conversation_id VARCHAR(64) PRIMARY KEY,
  conversation_start DATETIME, conversation_end DATETIME,
  media_type VARCHAR(64), direction VARCHAR(64), ani VARCHAR(128), dnis VARCHAR(128),
  queue_ids TEXT, agent_ids TEXT, wrapup_codes TEXT,
  raw_json JSON NOT NULL, collected_at DATETIME NOT NULL,
  INDEX idx_conv_start (conversation_start), INDEX idx_conv_media (media_type)
);
INSERT INTO schema_version (version)
SELECT '001_initial_mysql_reporting'
WHERE NOT EXISTS (SELECT 1 FROM schema_version WHERE version='001_initial_mysql_reporting');
