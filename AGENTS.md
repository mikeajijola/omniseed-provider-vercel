# Working on the Vercel Eve agent implementation

- Provider organisation and canonical Provider ID: Vercel / `vercel`.
- This package implements the `agents` primitive-family contract using Vercel's Eve framework.
- Eve is not Lily, Company Stewardship, company state, or a Provider.
- Bootstrap inputs are company reference, organisational agent identity, and authenticated access to OmniSeed.
- Agent turns must use governed OmniSeed operations. Never add direct GitHub or deployment-provider mutation.
- Never print credentials or include them in observations/evidence.
- Requested, configured, connected, healthy, and semantically operational are separate facts.
- Stdout is reserved for JSON-RPC responses; diagnostics go to stderr.
- Run `npm test` before proposing a change.
