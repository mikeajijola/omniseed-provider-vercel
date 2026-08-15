# OmniSeed Vercel Provider

This narrow Provider observes the `omniseed_os` connector resource selected for the OmniSeed Ecosystem's human operating interface. It does not make Vercel a Capability and does not add a primitive family.

The Provider reads Vercel deployment metadata using `VERCEL_TOKEN`, then performs unauthenticated HTTP reachability and company-binding checks. The token is read only from the process environment and is never included in messages or evidence.

`provider.apply` intentionally returns an error. OmniSeed does not yet give this Provider an approved immutable build artifact or source revision deployment contract, so it cannot truthfully create a deployment. Existing deployments can still be observed without claiming they were applied by OmniSeed.

Run with one JSON-RPC 2.0 message per line:

```sh
python provider/vercel_provider.py
```
