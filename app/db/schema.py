"""Database schema definitions, table creation DDL, and index specifications."""

CREATE_USERS_TABLE = """
CREATE TABLE IF NOT EXISTS users (
    id VARCHAR(64) PRIMARY KEY,
    email VARCHAR(255) UNIQUE NOT NULL,
    hashed_password VARCHAR(255) NOT NULL,
    full_name VARCHAR(255) NOT NULL,
    role VARCHAR(32) NOT NULL DEFAULT 'candidate',
    is_active INTEGER NOT NULL DEFAULT 1,
    email_verified INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);
"""

CREATE_JOBS_TABLE = """
CREATE TABLE IF NOT EXISTS jobs (
    id VARCHAR(64) PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    company VARCHAR(255) NOT NULL,
    location VARCHAR(255) NOT NULL DEFAULT 'Remote',
    description TEXT NOT NULL,
    required_skills TEXT NOT NULL DEFAULT '[]',
    preferred_skills TEXT NOT NULL DEFAULT '[]',
    experience_level VARCHAR(64),
    source VARCHAR(64) NOT NULL DEFAULT 'firecrawl',
    source_url TEXT,
    apply_url TEXT,
    content_hash VARCHAR(64) UNIQUE NOT NULL,
    collected_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_content_hash ON jobs(content_hash);
CREATE INDEX IF NOT EXISTS idx_jobs_active_last_seen ON jobs(is_active, last_seen_at);
"""

CREATE_CANDIDATE_PROFILES_TABLE = """
CREATE TABLE IF NOT EXISTS candidate_profiles (
    id VARCHAR(64) PRIMARY KEY,
    user_id VARCHAR(64) NOT NULL,
    raw_hash VARCHAR(64) NOT NULL,
    full_name VARCHAR(255),
    email VARCHAR(255),
    phone VARCHAR(64),
    summary TEXT,
    skills TEXT NOT NULL DEFAULT '[]',
    experience TEXT NOT NULL DEFAULT '[]',
    education TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    UNIQUE (user_id, raw_hash)
);
CREATE INDEX IF NOT EXISTS idx_profiles_user_id ON candidate_profiles(user_id);
"""

CREATE_MATCH_RECORDS_TABLE = """
CREATE TABLE IF NOT EXISTS match_records (
    id VARCHAR(64) PRIMARY KEY,
    candidate_id VARCHAR(64) NOT NULL,
    job_id VARCHAR(64) NOT NULL,
    overall_score REAL NOT NULL,
    score_breakdown TEXT NOT NULL,
    matched_skills TEXT NOT NULL DEFAULT '[]',
    missing_skills TEXT NOT NULL DEFAULT '[]',
    caveat TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (candidate_id) REFERENCES candidate_profiles(id) ON DELETE CASCADE,
    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_matches_cand_job ON match_records(candidate_id, job_id);
CREATE INDEX IF NOT EXISTS idx_matches_cand_score ON match_records(candidate_id, overall_score DESC);
CREATE INDEX IF NOT EXISTS idx_matches_cand_created ON match_records(candidate_id, created_at DESC);
"""

CREATE_SCRAPING_RUNS_TABLE = """
CREATE TABLE IF NOT EXISTS scraping_runs (
    id VARCHAR(64) PRIMARY KEY,
    platform VARCHAR(32) NOT NULL,
    status VARCHAR(32) NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    jobs_discovered INTEGER NOT NULL DEFAULT 0,
    jobs_inserted INTEGER NOT NULL DEFAULT 0,
    jobs_updated INTEGER NOT NULL DEFAULT 0,
    jobs_skipped INTEGER NOT NULL DEFAULT 0,
    jobs_marked_inactive INTEGER NOT NULL DEFAULT 0,
    retry_count INTEGER NOT NULL DEFAULT 0,
    error_category VARCHAR(64),
    error_details TEXT
);
CREATE INDEX IF NOT EXISTS idx_scraping_runs_platform ON scraping_runs(platform);
CREATE INDEX IF NOT EXISTS idx_scraping_runs_started ON scraping_runs(started_at);
"""

CREATE_SCRAPING_SOURCES_TABLE = """
CREATE TABLE IF NOT EXISTS scraping_sources (
    id VARCHAR(64) PRIMARY KEY,
    platform VARCHAR(32) NOT NULL,
    company_name VARCHAR(255) NOT NULL,
    board_url TEXT NOT NULL,
    board_token VARCHAR(255) NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    last_scraped_at TEXT,
    UNIQUE (platform, board_token)
);
CREATE INDEX IF NOT EXISTS idx_scraping_sources_platform ON scraping_sources(platform, is_active);
"""

CREATE_SCRAPING_LOCKS_TABLE = """
CREATE TABLE IF NOT EXISTS scraping_locks (
    lock_name VARCHAR(64) PRIMARY KEY,
    locked_by VARCHAR(64) NOT NULL,
    locked_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
"""

CREATE_RESUMES_TABLE = """
CREATE TABLE IF NOT EXISTS resumes (
    id VARCHAR(64) PRIMARY KEY,
    user_id VARCHAR(64) NOT NULL,
    filename VARCHAR(255) NOT NULL,
    storage_key VARCHAR(512) NOT NULL,
    file_size INTEGER NOT NULL,
    mime_type VARCHAR(128) NOT NULL,
    raw_hash VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'completed',
    is_active INTEGER NOT NULL DEFAULT 1,
    version INTEGER NOT NULL DEFAULT 1,
    profile_id VARCHAR(64),
    extracted_text TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (profile_id) REFERENCES candidate_profiles(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_resumes_user_id ON resumes(user_id);
CREATE INDEX IF NOT EXISTS idx_resumes_user_active ON resumes(user_id, is_active);
CREATE INDEX IF NOT EXISTS idx_resumes_user_hash ON resumes(user_id, raw_hash);
"""

CREATE_USER_SAVED_JOBS_TABLE = """
CREATE TABLE IF NOT EXISTS user_saved_jobs (
    id VARCHAR(64) PRIMARY KEY,
    user_id VARCHAR(64) NOT NULL,
    job_id VARCHAR(64) NOT NULL,
    saved_at TEXT NOT NULL,
    notes TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE,
    UNIQUE (user_id, job_id)
);
CREATE INDEX IF NOT EXISTS idx_saved_jobs_user ON user_saved_jobs(user_id, saved_at DESC);
CREATE INDEX IF NOT EXISTS idx_saved_jobs_job ON user_saved_jobs(job_id);
"""

