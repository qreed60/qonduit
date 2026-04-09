# Manual Steps (Outside Codex)

## Infrastructure and networking

- DNS/subdomain setup for project-specific endpoints.
- Cloudflare/proxy rules (if used).
- Reverse proxy routing updates for endpoint bindings.

## External service deployment

- Deploy and secure Qdrant.
- Deploy embedding backend and capacity-plan vector generation.
- Deploy/operate shared llama-compatible backend.

## GitHub automation

- Configure GitHub webhooks to call your webhook receiver.
- Wire webhook receiver to run `python -m app.ingest_repo ...` jobs.
- Add repo auth/secrets for private repository pull automation.

## Operator checklist

- [ ] Raw llama endpoint healthy (`/v1/models`, `/v1/chat/completions`).
- [ ] Gateway endpoint healthy (`/health`, `/v1/models`).
- [ ] Alias models visible in gateway `/v1/models`.
- [ ] Project alias routes resolve expected `project_id` and `default_mode`.
- [ ] Repo ingest CLI runs and indexes chunks for target project.
- [ ] Retrieval remains project-scoped (no cross-project results).
- [ ] Dyad can use alias model without custom request body fields.
- [ ] Android Studio can use alias model without custom request body fields.
