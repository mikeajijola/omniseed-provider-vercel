# OmniSeed EVE Provider

This package is the narrow `agents` primitive-family adapter for an independently deployed EVE agent runtime. EVE supplies execution; a company definition supplies the organisational actor and its realisation. The Provider neither defines Lily nor contains company state.

The runtime is configured with only a canonical company reference, an organisational agent identity, and authenticated access to OmniSeed. Authenticated tools inside the EVE deployment must resolve context and invoke ordinary governed OmniSeed operations. This adapter deliberately has no GitHub or Vercel mutation path.

`provider.apply` binds an already deployed runtime; it does not pretend to deploy one. `provider.observe` checks EVE health and runtime identity. `provider.invoke` performs a real EVE session turn and returns the completed semantic response with evidence identifiers.

Production integration requires a separately owned agent application, a deployed EVE endpoint, and the authenticated OmniSeed operation API. This Provider remains reusable: `resourceId` and `offers` come from the requested primitive instance, so it does not claim that every EVE agent supplies stewardship.

The first-party Lily agent application belongs in `mikeajijola/omniseed-lily`, not in this Provider package. That application may select EVE through company state, but Lily's identity and stewardship behaviour must not become EVE Provider semantics.
