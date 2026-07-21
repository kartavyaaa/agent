# ADR-001: Vector Store Choice

**Status:** Accepted  
**Date:** 2026-07-07

## Context

Semantic memory requires approximate nearest-neighbour (ANN) search over 1536-dimensional embeddings (text-embedding-3-small). Options evaluated: Postgres + pgvector, Chroma (embedded), Qdrant (separate service), Weaviate (separate service), Pinecone (hosted).

The platform runs on Oracle Cloud Free Tier — a single ARM VM with 4 OCPUs and 24 GB RAM. Every extra service competes for those resources and adds operational overhead.

## Decision

**Use PostgreSQL + pgvector extension.**

## Rationale

- We already need Postgres for relational data (users, tasks, reminders, projects). Adding pgvector costs zero extra infrastructure: one DB, one backup, one connection pool, one migration tool.
- pgvector 0.7+ supports HNSW indexing — adequate ANN recall for personal-scale workloads (<100 k memories).
- The `pgvector/pgvector:pg16` Docker image supports ARM64 and runs natively on the A1 Ampere instance.
- At personal scale, pgvector performance is more than sufficient. A dedicated ANN service becomes worthwhile only at 10 M+ vectors.

## Index Choice: HNSW (not ivfflat)

HNSW (`USING hnsw (embedding vector_cosine_ops) WITH (m=16, ef_construction=64)`) is used instead of ivfflat.

**Reason:** ivfflat requires a training pass over existing data to choose cluster centroids (`lists` parameter). On an empty or sparsely populated table it clusters poorly, producing bad recall until sufficient data accumulates. HNSW builds an incremental graph structure and achieves good recall from the very first insert. This is the correct choice for a growing personal dataset.

Default parameters: `m=16` (edges per node, controls graph connectivity), `ef_construction=64` (beam width during build, controls index quality). These are conservative starting values suitable for a single-user dataset; tune upward if recall degrades.

## Alternatives Rejected

| Option | Reason rejected |
|---|---|
| ivfflat (pgvector) | Requires pre-existing data for good clustering; poor recall on empty/sparse tables |
| Pinecone | Paid; external service dependency; adds latency |
| Qdrant | Separate Docker service; competes for free-tier RAM; adds operational complexity |
| Weaviate | Same as Qdrant; heavier memory footprint |
| Chroma (embedded) | Single-process only; no async support; not suitable for multi-worker setup |

## Consequences

- Initial Alembic migration: `CREATE EXTENSION IF NOT EXISTS vector`.
- Second migration: HNSW index on `memories.embedding`. Safe to run on an empty table.
- Embedding dimension is 1536 (text-embedding-3-small). If the embedding model changes, a column migration and full re-embed of existing rows is required.
- Backup: standard Postgres dump covers vectors — no separate backup target.
