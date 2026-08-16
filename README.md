# Superseded Eve adapter experiment

This repository contains a pre-clarification experiment that incorrectly gave Vercel's Eve framework its own Provider identity. It is not a valid Provider package and must not be registered or used as an architectural example.

The correct relationship is `Steward OmniSeed Ecosystem → Lily → Agent primitive → implementation/framework: Eve → Provider: Vercel → selected Vercel runtime/model/services`. Lily is the organisational actor. Eve is a Vercel framework. Vercel is the supplying Provider organisation.

The runtime is configured with only a canonical company reference, an organisational agent identity, and authenticated access to OmniSeed. Authenticated tools inside the EVE deployment must resolve context and invoke ordinary governed OmniSeed operations. This adapter deliberately has no GitHub or Vercel mutation path.

`provider.apply` binds an already deployed runtime; it does not pretend to deploy one. `provider.observe` checks EVE health and runtime identity. `provider.invoke` performs a real EVE session turn and returns the completed semantic response with evidence identifiers.

Production integration requires a separately owned agent application, a deployed Eve endpoint, and the authenticated OmniSeed operation API. Retained adapter logic must be migrated beneath the Vercel Provider boundary before use.

The first-party Lily agent application belongs in `mikeajijola/omniseed-lily`. Lily's identity and stewardship behaviour remain separate from Eve and Vercel. See the authoritative [Provider semantics](https://github.com/mikeajijola/omniseed-ecosystem/blob/main/docs/provider-semantics.md).
