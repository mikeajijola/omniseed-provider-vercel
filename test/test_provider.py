import json
import subprocess
import sys
import unittest

from provider.vercel_provider import PROTOCOL, ProviderError, VercelProvider


CONFIG = {
    "projectId": "prj_os", "deploymentId": "dpl_os", "deploymentUrl": "https://os.example.test",
    "companyBindingUrl": "https://os.example.test/api/company", "expectedCompanyId": "omniseed_ecosystem",
    "expectedRepository": "mikeajijola/omniseed-ecosystem-company", "expectedEnvironment": "production"
}


class FakeClient:
    token = "secret-that-must-not-leak"

    def __init__(self, binding=None, state="READY"):
        self.binding = binding or {"companyId": "omniseed_ecosystem", "canonicalRepository": "mikeajijola/omniseed-ecosystem-company", "environment": "production"}
        self.state = state

    def json_request(self, url, authenticated=False, timeout=10):
        if "api.vercel.com" in url:
            return 200, {"id": "dpl_os", "readyState": self.state}
        if url.endswith("/api/company"):
            return 200, self.binding
        return 200, {"page": True}


def action(spec=None, family="connectors", resource_id="omniseed_os"):
    return {"id": "act-1", "family": family, "resourceId": resource_id, "desired": {"spec": spec or CONFIG}}


class ProviderTests(unittest.TestCase):
    def provider(self, client=None):
        provider = VercelProvider(CONFIG, client or FakeClient())
        provider.company_id = "omniseed_ecosystem"
        return provider

    def test_initialize_contract_matches_manifest(self):
        result = self.provider().initialize({"protocolVersion": PROTOCOL, "configuration": CONFIG, "context": {"companyId": "omniseed_ecosystem"}})
        self.assertEqual(result["primitiveFamilies"], ["connectors"])
        self.assertEqual(result["offerings"][0]["resource"]["id"], "omniseed_os")

    def test_validate_accepts_only_bound_connector(self):
        self.assertTrue(self.provider().validate(action())["valid"])
        self.assertFalse(self.provider().validate(action(family="agents"))["valid"])
        self.assertFalse(self.provider().validate(action(resource_id="other"))["valid"])

    def test_validate_rejects_company_boundary(self):
        changed = dict(CONFIG, expectedCompanyId="another_company")
        issues = self.provider().validate(action(changed))["issues"]
        self.assertIn("company_boundary", [item["code"] for item in issues])

    def test_plan_is_read_only_and_deterministic(self):
        result = self.provider().plan(action())
        self.assertTrue(result["deterministic"])
        self.assertFalse(result["mutationSupported"])
        self.assertEqual(result["mode"], "observe_existing")

    def test_apply_fails_honestly(self):
        with self.assertRaises(ProviderError) as raised:
            self.provider().apply(action())
        self.assertEqual(raised.exception.code, "mutation_unsupported")

    def test_observe_returns_two_evidence_records(self):
        result = self.provider().observe({"spec": CONFIG})
        self.assertEqual(result["status"], "healthy")
        self.assertEqual(len(result["evidence"]), 2)
        self.assertTrue(result["snapshot"]["companyBindingMatches"])

    def test_observe_degrades_on_wrong_company(self):
        client = FakeClient({"companyId": "acme", "canonicalRepository": "example/acme", "environment": "production"})
        result = self.provider(client).observe({"spec": CONFIG})
        self.assertEqual(result["status"], "degraded")
        self.assertFalse(result["snapshot"]["companyBindingMatches"])

    def test_observe_degrades_when_deployment_not_ready(self):
        result = self.provider(FakeClient(state="ERROR")).observe({"spec": CONFIG})
        self.assertEqual(result["status"], "degraded")

    def test_status_keeps_lifecycle_facts_separate(self):
        status = self.provider().status()
        self.assertEqual(status, {"implementation_available": True, "configured": True, "connected": True, "healthy": True})
        missing = VercelProvider({}, FakeClient()).status()
        self.assertTrue(missing["implementation_available"])
        self.assertFalse(missing["configured"])

    def test_evidence_never_contains_token(self):
        result = self.provider().observe({"spec": CONFIG})
        self.assertNotIn(FakeClient.token, json.dumps(result))

    def test_protocol_process_reports_apply_error(self):
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "provider.initialize", "params": {"protocolVersion": PROTOCOL, "configuration": CONFIG, "context": {"companyId": "omniseed_ecosystem"}}},
            {"jsonrpc": "2.0", "id": 2, "method": "provider.apply", "params": {"action": action()}},
            {"jsonrpc": "2.0", "id": 3, "method": "provider.shutdown", "params": {}}
        ]
        process = subprocess.run([sys.executable, "provider/vercel_provider.py"], input="\n".join(json.dumps(item) for item in messages) + "\n", text=True, capture_output=True, check=True)
        output = [json.loads(line) for line in process.stdout.splitlines()]
        self.assertEqual(output[1]["error"]["data"]["code"], "mutation_unsupported")
        self.assertEqual(output[2]["result"], {"shutdown": True})


if __name__ == "__main__":
    unittest.main()
