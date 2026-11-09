-- Built after the load. Each index answers one kind of question fast.

-- HNSW (Hierarchical Navigable Small World): a graph where each vector links to ~m neighbours; a search walks the
-- graph toward the query instead of comparing all 9,266 vectors. Approximate — it can miss a true neighbour, which
-- is why we measure recall against exact NumPy. vector_cosine_ops = the index understands the <=> (cosine distance) operator.
--   m = links per node (more = better recall, bigger index); ef_construction = how hard it looks while building.
CREATE INDEX jobs_embedding_hnsw ON jobs USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);

-- GIN (Generalized Inverted Index): word → list of rows containing it. The same idea as a search engine's inverted index.
CREATE INDEX jobs_tsv_gin ON jobs USING gin (tsv);

-- Plain B-tree for the country filter (q35–q41).
CREATE INDEX jobs_country ON jobs (country);

ANALYZE jobs;  -- refresh planner statistics so Postgres picks the right index
