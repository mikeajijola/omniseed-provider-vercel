# Working on the OmniSeed Vercel Provider

- This Provider implements only the canonical `connectors` family.
- The Capability is `operate_omniseed_ecosystem`; Vercel is not a Capability.
- Never infer health from desired configuration.
- Never expose `VERCEL_TOKEN` in protocol messages, diagnostics, or evidence.
- Observation must verify both Vercel deployment state and HTTP company binding.
- This implementation is read-only. `apply` must fail honestly until an approved artifact/source deployment contract exists.
- Live tests are read-only unless a human explicitly authorises a sandbox mutation.

Run `npm test` before proposing a change.
