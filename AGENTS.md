# Working on the OmniSeed Vercel Provider

- Provider organisation and canonical Provider ID: Vercel / `vercel`.
- Eve, Functions, AI Gateway, and deployment services are Vercel products/services/frameworks, never separate Providers.

- This Provider implements the canonical `agents` and `connectors` families.
- This Provider does not implement `inference`. AI Gateway or a model setting used by an Agent runtime does not establish an inference-family claim.
- The Capability is `operate_omniseed_ecosystem`; Vercel is not a Capability.
- Never infer health from desired configuration.
- Never expose `VERCEL_TOKEN` in protocol messages, diagnostics, or evidence.
- Observation must verify both Vercel deployment state and HTTP company binding.
- `apply` may deploy only an approved immutable source commit through the configured Vercel project integration.
- Hosting an Agent and binding a secret reference must not make Vercel the Provider of inference supplied by another organisation.
- Live tests are read-only unless a human explicitly authorises a sandbox mutation.

Run `npm test` before proposing a change.
