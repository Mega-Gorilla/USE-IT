# Phase 3 TODOs – YAML Configuration Migration

This draft captures the remaining cleanup items after Phase 2 landed.
The list will be converted into issues / documentation updates as work progresses.

## Documentation polishing
- [x] Add a short migration guide for users coming from the legacy JSON/.env setup.
- [ ] Audit docs/cloud/* for lingering `.env` references and modernise wording. _(partially addressed: n8n guide updated; remaining cloud docs to review)_
- [ ] Confirm GUI docs mention writing updates back to `config.yaml`.

## Testing follow-up
- [x] Ensure tests fail fast when required API keys are missing (clear error messages).
- [x] Add smoke tests (or manual checklist) covering key examples with `config.yaml` only. _(config loader / env precedence verified in tests/ci/test_config.py)_

## Miscellaneous
- [ ] Evaluate whether generated config directories set secure permissions (`chmod 700`).
- [ ] Consider removing obsolete environment variables from telemetry / logging docs.
- [ ] Review MCP/cloud components for Phase 3 scope and note any blockers.
