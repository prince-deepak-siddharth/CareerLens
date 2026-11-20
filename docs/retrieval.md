# Retrieval and ranking

PostgreSQL indexes a weighted full-text document from the title, company and description. The keyword strategy also checks for exact matches against the indexed required-skill array. Dense candidates come from cosine distance over pgvector embeddings. Hybrid retrieval uses reciprocal rank fusion with a constant of 60.

Hard filters apply before candidate generation. These include expiration, employment, work setting, location, company category, experience, known salary and currency, required skills, and freshness. A personalized search also respects confirmed experience and the user's dismissed jobs. A missing salary is excluded when a salary filter is present. Preferred employment and work setting can inform the final ranking. The agent applies saved preferences to a new search and preserves the active filters on a refinement.

The default ranker's weights are 0.35 relevance, 0.25 skill overlap, 0.15 preference alignment, 0.10 experience fit, 0.10 freshness and 0.05 feedback. The returned rank version hashes the weights. Evidence excerpts and missing skills come from the posting and confirmed profile.

## Neural embeddings

Compose enables 384-dimensional MiniLM embeddings by default. For a host install, install the `embeddings` extra and set `CAREERLENS_EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2`. Set `CAREERLENS_CROSS_ENCODER=cross-encoder/ms-marco-MiniLM-L6-v2` for reranking, then rebuild the image and reindex. Existing profiles regenerate their vector for retrieval when the saved encoder differs from the active encoder. Benchmark the candidate model on the isolated evaluation corpus before using it for application traffic.

## Ingestion

An authorized import is JSON Lines with one `JobImport` object per line. Dates require offsets. Compensation requires currency. Each source has operator-supplied provenance.

```sh
docker compose cp permitted-jobs.jsonl worker:/imports/jobs.jsonl
docker compose exec worker python -m app.ingestion import /imports/jobs.jsonl \
  --source partner-feed --provenance "Licensed partner export 2026-11" --background
```

The worker retries failed queued imports and processes bounded batches. Source/external IDs and source URLs deduplicate records. Content hashes avoid unnecessary embedding writes. Postings with past expiration dates drop out of search automatically. Mark withdrawn entries inactive in a subsequent import. The CLI records counts for indexed, embedded, duplicate and invalid rows.

Tune the candidate pool with `CAREERLENS_RETRIEVAL_MIN_CANDIDATES`, `CAREERLENS_RETRIEVAL_OVERSAMPLE`, and `CAREERLENS_RETRIEVAL_RRF_K`. Set `CAREERLENS_RERANK_CANDIDATES` to control how many fused results the cross-encoder scores. Measure both quality and latency on your corpus when changing these settings.

## Employer job boards

The default Compose configuration imports public postings from Figma and Duolingo through the Greenhouse Job Board API. Discover jobs shows counts per source, and each employer posting links to its original page. CareerLens stores the feed snapshot in PostgreSQL and marks postings inactive when they disappear from a successfully fetched board. Fields the feed does not report remain empty.

```sh
docker compose exec api python -m app.greenhouse import figma,duolingo
docker compose exec api python -m app.ingestion reindex
```

`CAREERLENS_GREENHOUSE_BOARDS` controls the startup import. The CLI can refresh the configured boards between starts. The catalog scope **Employer postings** excludes demo listings. [Greenhouse Job Board API](https://developers.greenhouse.io/job-board.html)

Semantic, hybrid, and personalized searches recognize internship intent in the query. For machine learning, AI engineering, and data science internships, the retrieval engine checks embeddings for adjacent role titles while preserving source, location, and other filters. Distinct titles get priority over repeated listings. Compose includes three labeled example roles for this experience when demo seeding is enabled.

## Live job search in chat

In Compose, the Career Agent checks public job feeds when you ask it to find jobs or internships. It refreshes the configured Greenhouse employer boards and queries Himalayas for remote opportunities using recognized role terms and work type. Chat shows which sources were checked, which used recent results, and which were unavailable. The agent searches and ranks published postings in CareerLens and retains each listing's original link. Himalayas application links use the URL supplied by its API. [Himalayas API](https://himalayas.app/api)

Ask for InternDoor to get a direct search link. Ask to search only InternDoor to get the link without searching other sources. InternDoor allows sharing links and restricts bot access and bulk copying. [InternDoor terms](https://interndoor.com/terms)

Set `CAREERLENS_AGENT_LIVE_SEARCH=false` to use the stored catalog for ordinary chats. Explicit live requests still use configured sources. Disable a source with `CAREERLENS_HIMALAYAS_SEARCH_ENABLED=false` or an empty `CAREERLENS_GREENHOUSE_BOARDS`. The evaluation runner uses isolated fixtures.
