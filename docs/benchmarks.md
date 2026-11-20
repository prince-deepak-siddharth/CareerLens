# Measured benchmarks

The following runs used PostgreSQL 17 with pgvector and the versioned CareerLens datasets. The application stack indexed 5,000 synthetic jobs. The benchmark used a separate 120-job corpus, 105 queries, and synthetic candidate profiles for the personalized strategy. Relevance grades come from fixture expectations. Reported latency is local search time on this development machine.

| Encoder and strategy | Precision@5 | Recall@5 | Recall@10 | MRR | NDCG@5 | NDCG@10 | p50 ms | p95 ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Hash run, PostgreSQL keyword | 0.476 | 0.341 | 0.473 | 0.542 | 0.542 | 0.542 | 1.8 | 2.9 |
| Hash dense | 0.750 | 0.539 | 0.777 | 0.868 | 0.834 | 0.852 | 3.7 | 5.8 |
| Hash hybrid | 0.770 | 0.560 | 0.792 | 0.894 | 0.868 | 0.878 | 4.6 | 7.1 |
| Hash personalized | 0.880 | 0.628 | 0.869 | 1.000 | 1.000 | 0.988 | 7.0 | 10.5 |
| Neural run, PostgreSQL keyword | 0.478 | 0.342 | 0.474 | 0.550 | 0.546 | 0.545 | 1.8 | 3.8 |
| Neural dense | 0.838 | 0.598 | 0.852 | 0.953 | 0.951 | 0.956 | 10.3 | 16.6 |
| Neural hybrid | 0.848 | 0.609 | 0.858 | 0.965 | 0.963 | 0.965 | 11.2 | 16.9 |
| Neural hybrid with cross-encoder | 0.590 | 0.407 | 0.631 | 0.667 | 0.661 | 0.691 | 122.8 | 149.3 |
| Neural personalized | 0.878 | 0.627 | 0.879 | 1.000 | 0.999 | 0.998 | 13.8 | 18.7 |

The hash run was `b2a95f17-1103-4e94-aff3-53cd39c0d4bf`. The neural run was `99772207-0f7a-4809-81a1-9e1fd6e0c3b9` and used `sentence-transformers/all-MiniLM-L6-v2` with `cross-encoder/ms-marco-MiniLM-L6-v2` on CPU. Re-seeding separate databases can change keyword tie ordering because job identifiers differ. The tested cross-encoder lowered relevance metrics on these fixtures and increased latency. It remains opt-in.

| Agent dataset | Cases | Completed | p95 per turn |
| --- | ---: | ---: | ---: |
| Single turn | 34 | 34 | 20.5 ms |
| Multiturn | 16 | 16 | 15.1 ms |
| Prompt handling | 5 | 5 | 43.4 ms |

The deterministic agent runs had zero unsupported job records in checked outputs. Variations in tool execution success reflect expected forced errors. The four synthetic resume extraction fixtures and five job explanation fixtures also met their deterministic checks. These measurements cover the bundled fixtures. Human judgment, calibration, live model performance, hosted trace delivery, and load-test latency remain separate verification work.

## Reproduce

```sh
docker compose exec api python -m app.evaluations.runner retrieval
docker compose exec api python -m app.evaluations.runner agent-single-turn
docker compose exec api python -m app.evaluations.runner agent-multiturn
docker compose exec api python -m app.evaluations.runner prompt-injection
```

Configure the neural encoder and cross-encoder in [the retrieval guide](retrieval.md) and reindex the evaluation corpus before repeating the neural experiment. The Evaluations screen plots and compares the recorded strategy metrics.

## Application HTTP benchmark

The running Compose stack served 105 hybrid searches through the Next.js proxy with 5,000 indexed postings and four concurrent requests. Measured latency was 50.37 ms at p50 and 91.51 ms at p95, at 73.61 requests per second on this machine.

```sh
cd backend
uv run --locked python ../scripts/benchmark_search_api.py --concurrency 4
```
