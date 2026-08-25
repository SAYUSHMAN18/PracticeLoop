# Why semantic search, and how it works here

## The problem with keyword search

Keyword search (`ILIKE '%...%'`, full-text search) matches on the literal words in a
query. Ask "tree data structure for fast lookup" and a keyword search finds nothing in a
bank containing "What is a binary search tree?" — not one word overlaps except "tree", and
most keyword engines wouldn't even weight that match highly.

## Embeddings

An embedding is a fixed-length vector of numbers (here, 384 of them) produced by a model
trained so that semantically similar text produces vectors that are numerically close
together, regardless of shared words. "Tree data structure for fast lookup" and "What is a
binary search tree?" land near each other in that 384-dimensional space because they mean
similar things, even sharing almost no vocabulary.

This app uses `sentence-transformers/all-MiniLM-L6-v2`, run locally (`app/core/embedder.py`)
— no API key, no per-call cost, negligible quality loss at the scale a single student's
question bank reaches.

## Cosine distance and `<=>`

To compare two embeddings, pgvector's `<=>` operator computes cosine distance: 0 means
identical direction (as similar as it gets), larger means less similar. Cosine distance
(rather than raw Euclidean distance) is the standard choice for sentence embeddings because
what matters is the *direction* of the vector, not its magnitude.

```sql
SELECT question, embedding <=> $1 AS distance
FROM questions
WHERE user_id = $2 AND embedding <=> $1 < 0.65
ORDER BY embedding <=> $1
LIMIT 5
```

The `< 0.65` threshold is what turns "always return the 5 closest rows, however irrelevant"
into "return real matches, or honestly say there are none" — see `app/practice/service.py`.

## Why no ANN index

An IVFFlat or HNSW approximate-nearest-neighbor index speeds up similarity search at the
cost of exactness, and only pays off once a table has enough rows that a full sequential
scan is actually slow — typically tens of thousands of rows or more. A single student's
question bank realistically reaches dozens to low thousands. At that size, a sequential
scan computing cosine distance against every row is both exact and effectively instant, and
an ANN index adds real failure modes for no benefit: `IVFFlat` in particular partitions
vectors into `lists` clusters and only searches the nearest few by default — with few rows
per cluster, it can return *zero* matches for a perfectly good query, silently, with no
error. This is a real, previously-hit bug in a sibling project's `schema_chunks` table (see
[`nl2sql`](https://github.com/SAYUSHMAN18/NL2SQL)'s history) and exactly why `scripts/schema.sql`
here has no `CREATE INDEX ... USING ivfflat` line. Revisit only if this table's row count
genuinely grows into the tens of thousands.
