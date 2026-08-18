# OmniSeed Vercel Provider

This is the single Provider package for the supplying organisation Vercel, with canonical Provider ID `vercel`.

- `agents` maps to Eve, Vercel Functions, and AI Gateway.
- `connectors` maps to Vercel Functions and deployment services.

Provider configuration contains only organisation-wide Vercel team/authentication settings and optional mappings from desired-state secret-reference names to server environment names. Resource deployment intent is derived directly from the selected canonical Omniform resource: project identity, numeric Vercel Git integration repository ID, full commit SHA, company and Agent identities, environment, expected endpoints, and secret-reference names. There is no second flat or hidden deployment definition.

`plan` distinguishes project creation from reuse and reports the exact immutable revision, environment binding names, deployment impact, and expected evidence. `apply` uses Vercel `/v11/projects`, project environment endpoints, and `/v13/deployments`; it never invokes `eve deploy`, reads a local source tree, accepts a branch tip, or binds a pre-existing runtime URL. Declared secret references are resolved from the Provider process environment and written as sensitive project variables; values never enter Git, plans, runtime state, or evidence.

`observe` starts from the deployment binding persisted by OmniSeed and independently verifies Vercel deployment/source identity. Connector observations verify company binding. Agent observations use authenticated Eve health and info endpoints and verify company, Agent, environment, and Lily source identity. `agent.semantic_turn` likewise requires that persisted Engine resource binding; a browser or caller cannot choose a runtime URL.

For a declared Eve Agent, `runtime.session` supplies the credential-reference
name plus issuer and audience. The Provider binds those public settings and the
referenced secret to the Agent deployment. A connector realising OmniSeed OS
may declare the same secret reference so the OS can mint short-lived,
company-scoped session tokens. The value remains ordinary per-environment
configuration and never enters Omniform, a plan, runtime state, or evidence.

Run one JSON-RPC 2.0 message per line with `python provider/vercel_provider.py`. Run tests with `npm test`.
