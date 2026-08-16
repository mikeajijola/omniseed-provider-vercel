# OmniSeed Vercel Provider

This narrow Provider realises and observes the `omniseed_os` connector resource selected for the OmniSeed Ecosystem's human operating interface. The connector is the governed human-to-company boundary. Deployment is implementation work and resulting runtime state beneath that connector; deployment itself is not redefined as a primitive. Vercel is neither a Capability nor a reason to add a primitive family.

The Provider reads Vercel deployment metadata using `VERCEL_TOKEN`, then performs unauthenticated HTTP reachability and company-binding checks. The token is read only from the process environment and is never included in messages or evidence.

`provider.apply` accepts only an approved immutable GitHub source contract: repository identity, numeric Vercel integration repository ID, and a full commit SHA. It asks Vercel to create that exact deployment and returns the resulting deployment identity. It never chooses a branch tip, rebuilds an approval, or treats deployment creation as proof of health; OmniSeed must observe afterward.

Run with one JSON-RPC 2.0 message per line:

```sh
python provider/vercel_provider.py
```
