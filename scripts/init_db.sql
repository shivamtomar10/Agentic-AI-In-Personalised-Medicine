-- OPTIONAL. The prototype runs fully in memory; this file only lets `docker compose up` start a Postgres with pgvector
-- for the planned persistence/dense-retrieval work (not used by the current code).
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS patient_state_versions (
  patient_id TEXT NOT NULL, version INT NOT NULL, state_hash TEXT NOT NULL, previous_state_hash TEXT,
  payload JSONB NOT NULL, created_at TIMESTAMPTZ DEFAULT now(), PRIMARY KEY (patient_id, version));
