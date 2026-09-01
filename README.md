# OmniSeed Vercel Provider

This is the single Provider package for the supplying organisation Vercel, with canonical Provider ID `vercel`.

- `agents` maps to declared Agent products hosted with Vercel Functions. Eve is
  the compatibility product, not the agents-family contract.
- `connectors` maps to Vercel Functions and deployment services.

This package does not advertise the `inference` primitive family. A model or AI
Gateway setting beneath an Agent is not evidence of a separately provisioned
inference binding.

Agent declarations select an installed runtime adapter with
`runtime.interaction.protocol` and identify the implementation separately with
`implementation.product`. Adapters own environment mapping, Vercel build
settings, health/info parsing, invocation, and evidence types. The built-in
`eve.session/1` adapter preserves Lily's existing paths, audience, environment
names (including `LILY_MODEL`), and evidence types. The built-in
`omniseed.agent.json-turn/1` adapter demonstrates a non-Eve JSON interaction;
its declaration supplies `runtime.environmentMapping` and optional
`runtime.build` settings. Unknown protocols fail as `runtime_adapter_missing`.
Neither product is a Provider and neither implies an inference-family claim.

Provider configuration contains only organisation-wide Vercel team/authentication settings and optional mappings from desired-state secret-reference names to server environment names. Resource deployment intent is derived directly from the selected canonical Omniform resource: project identity, numeric Vercel Git integration repository ID, full commit SHA, company and Agent identities, product, protocol, implementation settings, environment, expected endpoints, and secret-reference names. Each adapter maps these neutral fields to its declared runtime environment; the Eve compatibility adapter alone maps its model to `LILY_MODEL`. There is no second flat or hidden deployment definition.

When `statusProjectId` is configured, Provider connection status is established
against that declared Vercel project within `teamId`. This supports credentials
whose project scope is valid even when Vercel does not expose the unrelated user
profile endpoint, and proves access to the boundary reconciliation actually
needs. Without it, the Provider retains the user endpoint as a compatibility
probe.

`plan` distinguishes project creation from reuse and reports the exact immutable revision, environment binding names, deployment impact, and expected evidence. `apply` uses Vercel `/v11/projects`, project environment endpoints, and `/v13/deployments`; it never invokes `eve deploy`, reads a local source tree, accepts a branch tip, or binds a pre-existing runtime URL. Declared secret references are resolved from the Provider process environment and written as sensitive project variables; values never enter Git, plans, runtime state, or evidence.

An Agent may declare `runtime.source` when its immutable hosting artifact
composes several company resources. The Provider deploys that exact source while
retaining `implementation.repository` and `implementation.revision` as the
Agent implementation identity used by runtime observation. This permits Lily
and OmniSeed OS to share one Vercel project without turning Lily into an OS
component or misreporting the OS repository as Lily's implementation.

OmniSeed supplies the Provider only the approved desired resources selected for
Vercel. When resources across supported primitive families declare the same
project, repository integration identity, full source commit, and target, the
Provider treats them as one shared immutable deployment. It combines their
non-secret environment bindings and secret-reference names, rejects conflicting
bindings, creates one deployment, and returns distinct resource bindings that
refer to that deployment. A restarted Provider independently finds a reusable
ready deployment through Vercel's deployment API and verifies the exact source
again before reuse. This is deployment coalescing beneath the Provider boundary;
it does not collapse the Agent and interface resources or their observations.

`apply` returns a resource binding only after Vercel reports the deployment
`READY` and the independently read deployment source still matches the approved
repository identity and full commit SHA. Failed, cancelled, timed-out, or
source-mismatched deployments fail closed. This makes reconciliation repeatable
from the declaration while keeping deployment readiness distinct from later
runtime observations.

`observe` starts from the deployment binding persisted by OmniSeed and independently verifies Vercel deployment/source identity. Connector observations verify company binding. Agent observations dispatch through the explicitly selected protocol adapter and verify company, Agent, environment, product, protocol, and implementation source identity. Evidence exposes the safe product/protocol identity and whitelisted runtime facts, never arbitrary runtime response fields. `agent.semantic_turn` likewise requires that persisted Engine resource binding; a browser or caller cannot choose a runtime URL.

For a declared Eve Agent, `runtime.session` supplies the credential-reference
name plus issuer and audience. The Provider binds those public settings and the
referenced secret to the Agent deployment. A connector realising OmniSeed OS
may declare the same secret reference so the OS can mint short-lived,
company-scoped session tokens. The value remains ordinary per-environment
configuration and never enters Omniform, a plan, runtime state, or evidence.
Secret values may be supplied either to the Provider process or pre-provisioned
directly in the declared Vercel project and target environment. Existing secret
bindings are preserved without reading, exporting, or rewriting their values;
missing bindings must be available to the Provider process and fail closed
otherwise. Explicit rotation uses the Provider configuration's
`rotateSecretReferences` allow-list and requires the replacement value in the
Provider process. Public bindings derived from the approved declaration are
still reconciled on every apply.

Existing flat actions and durable bindings migrate deliberately:
`runtimeModel`, `sessionCredentialReference`, `sessionIssuer`, and
`sessionAudience` are read as neutral Agent model and interaction fields, with
product `eve` and protocol `eve.session/1`. Newly normalized plans and bindings
persist `agentProduct`, `interactionProtocol`, `agentModel`, and
`interactionCredentialReference` instead.

Run one JSON-RPC 2.0 message per line with `python3 provider/vercel_provider.py`. Run tests with `npm test`.
