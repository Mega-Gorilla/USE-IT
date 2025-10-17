# Phase 3 TODOs – YAML Configuration Migration

This draft captures the remaining cleanup items after Phase 2 landed.
The list will be converted into issues / documentation updates as work progresses.

## Documentation polishing
- [ ] Audit docs/cloud/* for lingering `.env` references and modernise wording.
- [ ] Add a short migration guide for users coming from the legacy JSON/.env setup.
- [ ] Confirm GUI docs mention writing updates back to `config.yaml`.

## Testing follow-up
- [ ] Add smoke tests (or manual checklist) covering key examples with `config.yaml` only.
- [ ] Ensure tests fail fast when required API keys are missing (clear error messages).

## Miscellaneous
- [ ] Evaluate whether generated config directories set secure permissions (`chmod 700`).
- [ ] Consider removing obsolete environment variables from telemetry / logging docs.
- [ ] Review MCP/cloud components for Phase 3 scope and note any blockers.
