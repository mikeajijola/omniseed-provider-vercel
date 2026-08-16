# Vercel Eve agent implementation

This package exposes Vercel's `agents` primitive-family implementation using the Eve framework. Its canonical Provider identity is `vercel`; Eve is the product/framework used beneath that boundary.

The correct relationship is `Steward OmniSeed Ecosystem → Lily → Agent primitive → implementation/framework: Eve → Provider: Vercel → selected Vercel runtime/model/services`. Lily is the organisational actor. Eve is a Vercel framework. Vercel is the supplying Provider organisation.

The runtime is configured with only a canonical company reference, an organisational agent identity, and authenticated access to OmniSeed. Authenticated tools inside the EVE deployment must resolve context and invoke ordinary governed OmniSeed operations. This adapter deliberately has no GitHub or Vercel mutation path.

`provider.apply` binds an already deployed runtime; it does not pretend to deploy one. `provider.observe` checks EVE health and runtime identity. `provider.invoke` performs a real EVE session turn and returns the completed semantic response with evidence identifiers.

Production integration requires a separately owned agent application, a deployed Eve endpoint, and the authenticated OmniSeed operation API.

The first-party Lily agent application belongs in `mikeajijola/omniseed-lily`. Lily's identity and stewardship behaviour remain separate from Eve and Vercel. See the authoritative [Provider semantics](https://github.com/mikeajijola/omniseed-ecosystem/blob/main/docs/provider-semantics.md).
