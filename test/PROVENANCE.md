# Eve Provider test migration provenance

The Agent-family behavior originated in `mikeajijola/omniseed-vercel-eve-agent` at commit `095396263414177c69518cae8d5ef01944ece9e6`.

The valid semantic-turn, identity-boundary, runtime-health, and evidence assertions were migrated into `test_provider.py` and expanded for immutable Vercel project/deployment provisioning, source drift, company/environment identity, secret non-disclosure, API failure, and project idempotency. The obsolete `bind_existing_eve_runtime` and caller-supplied `runtimeUrl` assertions were intentionally not retained.
