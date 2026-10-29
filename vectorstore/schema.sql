-- One table = the frozen eval corpus (corpus_v1) with its stored voyage-4-lite vectors.
-- Same rows and same vectors as the NumPy search in eval/, so results can be compared one to one.

CREATE EXTENSION IF NOT EXISTS vector;

DROP TABLE IF EXISTS jobs;
CREATE TABLE jobs (
    job_id         text PRIMARY KEY,
    title          text,
    company_name   text,
    country        text,
    source         text,
    snapshot_date  date,
    body           text NOT NULL,                 -- HTML-stripped description
    -- Postgres's own keyword index input: title + body, English stemming ("engineers" → "engin"), stop words dropped.
    -- GENERATED ... STORED = computed once on insert, like a materialised column.
    tsv            tsvector GENERATED ALWAYS AS (to_tsvector('english', coalesce(title, '') || ' ' || body)) STORED,
    embedding      vector(1024) NOT NULL          -- pgvector type; 1024 = voyage-4-lite dimension
);

-- Indexes are created AFTER the bulk load (vectorstore/load_pg.py): building once is faster than updating per row.
