CREATE TABLE IF NOT EXISTS scripts (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    domain TEXT,
    task_type TEXT,
    current_version_id TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS script_versions (
    id TEXT PRIMARY KEY,
    script_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    code TEXT NOT NULL,
    params_schema TEXT NOT NULL,
    output_schema TEXT NOT NULL,
    selector_manifest TEXT NOT NULL,
    validation_rules TEXT,
    changelog TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(script_id) REFERENCES scripts(id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_script_versions_script_version
ON script_versions(script_id, version);

CREATE INDEX IF NOT EXISTS idx_script_versions_script_status
ON script_versions(script_id, status);

CREATE TABLE IF NOT EXISTS script_embeddings (
    script_id TEXT PRIMARY KEY,
    embedding BLOB,
    embedding_text TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(script_id) REFERENCES scripts(id)
);

CREATE TABLE IF NOT EXISTS executions (
    id TEXT PRIMARY KEY,
    script_id TEXT NOT NULL,
    version_id TEXT NOT NULL,
    params TEXT NOT NULL,
    result TEXT,
    status TEXT NOT NULL,
    error_type TEXT,
    error_message TEXT,
    duration_ms INTEGER,
    dom_query_count INTEGER,
    page_count INTEGER,
    click_count INTEGER,
    output_item_count INTEGER,
    confidence REAL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(script_id) REFERENCES scripts(id),
    FOREIGN KEY(version_id) REFERENCES script_versions(id)
);

CREATE INDEX IF NOT EXISTS idx_executions_script_created
ON executions(script_id, created_at);

CREATE TABLE IF NOT EXISTS execution_traces (
    id TEXT PRIMARY KEY,
    execution_id TEXT NOT NULL,
    step_index INTEGER NOT NULL,
    action TEXT NOT NULL,
    selector_key TEXT,
    selector_used TEXT,
    url TEXT,
    status TEXT,
    error TEXT,
    snapshot_ref TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(execution_id) REFERENCES executions(id)
);

CREATE INDEX IF NOT EXISTS idx_execution_traces_execution_step
ON execution_traces(execution_id, step_index);

CREATE TABLE IF NOT EXISTS selector_stats (
    id TEXT PRIMARY KEY,
    script_id TEXT NOT NULL,
    selector_key TEXT NOT NULL,
    selector TEXT NOT NULL,
    success_count INTEGER DEFAULT 0,
    failure_count INTEGER DEFAULT 0,
    last_success_at TEXT,
    last_failure_at TEXT,
    FOREIGN KEY(script_id) REFERENCES scripts(id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_selector_stats_unique
ON selector_stats(script_id, selector_key, selector);
