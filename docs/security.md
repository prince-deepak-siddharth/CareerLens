# Security and privacy

- Argon2 hashes passwords. Signed bearer tokens expire after eight hours. The frontend holds the token in tab-scoped session storage. Use TLS and serve the frontend and proxied API from the same origin in production.
- API routes and tool implementations scope private data to the authenticated account. Reserved operator addresses require CLI provisioning and a database role. Removing an address from the allowlist revokes admin access.
- Upload limits cap body size, PDF page count and extracted text length. CareerLens stores the extracted draft without retaining the original PDF. PDF extraction requires readable selectable text.
- A resume draft can be deleted on the workspace page. A confirmed profile is an independently user-edited record and can be cleared through the profile API or workspace. Account deletion cascades through candidate data, interactions and agent memory.
- Run `python -m app.manage prune` daily to remove unconfirmed drafts older than seven days and search and conversation history older than 180 days. Set a separate Langfuse retention policy for pseudonymous trace records.
- Redis provides IP rate limits for authentication, resume upload, and chat. Configure trusted proxy boundaries in deployment before using forwarded client IPs. Keep Redis, PostgreSQL and metric endpoints on private networks.
- In the default local mode, resume text and messages stay on the application stack. If OpenAI is enabled, inform users about generation processing before accepting private content. Generation uses `store=False`. Evaluations use synthetic fixtures and a separate database.
- Job source text is treated as evidence. The planner never receives it as instructions. The system renders explanations from validated tool output.
