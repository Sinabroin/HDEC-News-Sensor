-- HDEC Executive Radar — Day-1 P0-A SQLite schema
-- 원칙 (rules.md §3, §5):
--   * 원문 전문을 저장하는 어떤 컬럼도 만들지 않는다. snippet은 저장 직전 500자로 절단된 값만 들어온다.
--   * source_metadata_json 허용 키: provider, query, source_url, collected_at, provider_response_id
--   * article_insights에는 alert_grade를 두지 않는다 (article_scores가 단일 소유).
--   * keyword_rules는 P0-A에서 seed/읽기 전용이다.
-- 이 파일은 멱등(idempotent)하다: 몇 번을 실행해도 동일한 결과.

CREATE TABLE IF NOT EXISTS articles (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  normalized_title TEXT,
  source TEXT,
  published_at TEXT,
  collected_at TEXT NOT NULL,
  url TEXT NOT NULL,
  url_hash TEXT UNIQUE,
  snippet TEXT,
  topic_candidates TEXT,
  signal_origin TEXT DEFAULT 'Mock',
  source_metadata_json TEXT,
  status TEXT DEFAULT 'collected'
);

CREATE TABLE IF NOT EXISTS article_scores (
  id TEXT PRIMARY KEY,
  article_id TEXT NOT NULL,
  hdec_relevance REAL,
  executive_importance REAL,
  business_opportunity REAL,
  risk_potential REAL,
  urgency REAL,
  source_reliability REAL,
  trend_repeat REAL,
  competitor_relevance REAL,
  macro_impact REAL,
  rule_bonus REAL DEFAULT 0,
  rule_penalty REAL DEFAULT 0,
  final_score REAL,
  alert_grade TEXT,
  confidence REAL,
  scoring_reason TEXT,
  evidence_basis TEXT,
  why_not_higher TEXT,
  why_not_lower TEXT,
  model_name TEXT,
  created_at TEXT,
  FOREIGN KEY(article_id) REFERENCES articles(id)
);

CREATE TABLE IF NOT EXISTS article_insights (
  id TEXT PRIMARY KEY,
  article_id TEXT NOT NULL,
  summary_3lines TEXT,
  hdec_implication TEXT,
  affected_units TEXT,
  opportunity_or_risk TEXT,
  executive_checkpoints TEXT,
  recommended_action TEXT,
  digest_message TEXT,
  created_at TEXT,
  FOREIGN KEY(article_id) REFERENCES articles(id)
);

CREATE TABLE IF NOT EXISTS feedback (
  id TEXT PRIMARY KEY,
  article_id TEXT NOT NULL,
  feedback_type TEXT NOT NULL,
  feedback_value TEXT,
  operator_id TEXT DEFAULT 'operator',
  created_at TEXT,
  applied_to_rules INTEGER DEFAULT 0,
  FOREIGN KEY(article_id) REFERENCES articles(id)
);

CREATE TABLE IF NOT EXISTS keyword_rules (
  id TEXT PRIMARY KEY,
  topic_id TEXT,
  topic_name TEXT,
  keyword TEXT,
  weight REAL DEFAULT 1.0,
  enabled INTEGER DEFAULT 1,
  exclude INTEGER DEFAULT 0,
  created_at TEXT,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS notification_logs (
  id TEXT PRIMARY KEY,
  article_id TEXT,
  channel TEXT,
  alert_grade TEXT,
  message_preview TEXT,
  send_status TEXT,
  error_message TEXT,
  sent_at TEXT,
  FOREIGN KEY(article_id) REFERENCES articles(id)
);

-- 조회/업서트 안정성용 인덱스 (테이블 정의는 PRD §17 그대로 유지)
CREATE UNIQUE INDEX IF NOT EXISTS idx_scores_article ON article_scores(article_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_insights_article ON article_insights(article_id);
CREATE INDEX IF NOT EXISTS idx_articles_normalized_title ON articles(normalized_title);
CREATE INDEX IF NOT EXISTS idx_feedback_article ON feedback(article_id);
CREATE INDEX IF NOT EXISTS idx_notification_logs_article ON notification_logs(article_id);

-- Day-2 예정 테이블 (Day-1 구현 금지 — 이름만 기록, rules.md §5):
--   x_signals            : 해외 조기 신호 후보 저장 (Day-2 Global Signal Layer)
--   signal_article_links : 조기 신호와 국내 검증 기사 연결
