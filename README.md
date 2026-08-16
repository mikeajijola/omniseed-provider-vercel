# OmniSeed Vercel Provider

This is the single Provider package for the supplying organisation Vercel, with canonical Provider ID `vercel`.

- `agents` maps to Eve, Vercel Functions, and AI Gateway.
- `connectors` maps to Vercel Functions and deployment services.

Provider configuration contains only organisation-wide Vercel team/authentication settings. Approved actions contain the resource-specific project identity, numeric Vercel Git integration repository ID, full commit SHA, company and Agent identities, environment, expected endpoints, and secret-reference names.

`plan` distinguishes project creation from reuse and reports the exact immutable revision, environment binding names, deployment impact, and expected evidence. `apply` uses Vercel `/v11/projects` and `/v13/deployments`; it never invokes `eve deploy`, reads a local source tree, accepts a branch tip, or binds a pre-existing runtime URL. Secret values stay server-side.

`observe` starts from the deployment binding persisted by OmniSeed and independently verifies Vercel deployment/source identity. Connector observations verify company binding. Agent observations use authenticated Eve health and info endpoints and verify company, Agent, environment, and Lily source identity. `agent.semantic_turn` likewise requires that persisted Engine resource binding; a browser or caller cannot choose a runtime URL.

Run one JSON-RPC 2.0 message per line with `python provider/vercel_provider.py`. Run tests with `npm test`.
