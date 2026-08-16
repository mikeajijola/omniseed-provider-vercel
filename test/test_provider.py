import json
import subprocess
import sys
import unittest

from provider.vercel_provider import PROTOCOL, ProviderError, VercelProvider


CONFIG = {
    "projectId": "prj_os", "deploymentId": "dpl_os", "deploymentUrl": "https://os.example.test",
    "sourceRepository": "mikeajijola/omniseedos", "sourceRepositoryId": 123456, "sourceCommitSha": "a" * 40,
    "companyBindingUrl": "https://os.example.test/api/company", "expectedCompanyId": "omniseed_ecosystem",
    "expectedRepository": "mikeajijola/omniseed-ecosystem-company", "expectedEnvironment": "production"
}


class FakeClient:
    token = "secret-that-must-not-leak"

    def __init__(self, binding=None, state="READY"):
        self.binding = binding or {"companyId": "omniseed_ecosystem", "canonicalRepository": "mikeajijola/omniseed-ecosystem-company", "environment": "production"}
        self.state = state
        self.requests = []

    def json_request(self, url, authenticated=False, timeout=10):
        if "api.vercel.com" in url:
            return 200, {"id": "dpl_os", "readyState": self.state, "gitSource": {"repo": "mikeajijola/omniseedos", "repoId": 123456, "ref": "a" * 40}}
        if url.endswith("/api/company"):
            return 200, self.binding
        return 200, {"page": True}

    def request(self, url, authenticated=False, timeout=10, method="GET", body=None):
        self.requests.append({"url": url, "authenticated": authenticated, "method": method, "body": body})
        if method == "POST":
            return 200, {"id": "dpl_created", "url": "new-os.example.test", "readyState": "QUEUED"}
        return self.json_request(url, authenticated, timeout)


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

    def test_plan_binds_immutable_source_and_is_deterministic(self):
        result = self.provider().plan(action())
        self.assertTrue(result["deterministic"])
        self.assertTrue(result["mutationSupported"])
        self.assertEqual(result["mode"], "deploy_immutable_source")
        self.assertEqual(result["source"]["commitSha"], "a" * 40)

    def test_apply_submits_exact_approved_commit(self):
        client = FakeClient()
        result = self.provider(client).apply(action())
        self.assertEqual(result["status"], "submitted")
        self.assertEqual(result["attributes"]["sourceCommitSha"], "a" * 40)
        self.assertEqual(result["attributes"]["spec"]["deploymentUrl"], "https://new-os.example.test")
        self.assertEqual(result["attributes"]["spec"]["companyBindingUrl"], "https://new-os.example.test/api/company")
        request = client.requests[0]
        self.assertEqual(request["method"], "POST")
        self.assertEqual(request["body"]["gitSource"], {"type": "github", "repoId": 123456, "ref": "a" * 40})

    def test_apply_rejects_non_immutable_source(self):
        changed = dict(CONFIG, sourceCommitSha="main")
        with self.assertRaises(ProviderError) as raised:
            self.provider().apply(action(changed))
        self.assertEqual(raised.exception.code, "invalid_action")

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

    def test_observe_degrades_when_deployed_source_differs(self):
        class Drifted(FakeClient):
            def json_request(self, url, authenticated=False, timeout=10):
                if "api.vercel.com" in url:
                    return 200, {"id": "dpl_os", "readyState": "READY", "gitSource": {"repo": "mikeajijola/omniseedos", "repoId": 123456, "ref": "b" * 40}}
                return super().json_request(url, authenticated, timeout)
        result = self.provider(Drifted()).observe({"spec": CONFIG})
        self.assertEqual(result["status"], "degraded")
        self.assertFalse(result["snapshot"]["sourceMatches"])

    def test_observe_requires_real_target_and_exact_repository_id(self):
        without_target = {key: value for key, value in CONFIG.items() if key not in {"deploymentUrl", "companyBindingUrl"}}
        with self.assertRaises(ProviderError) as raised:
            self.provider().observe({"spec": without_target})
        self.assertEqual(raised.exception.code, "observation_target_missing")
        class WrongRepository(FakeClient):
            def json_request(self, url, authenticated=False, timeout=10):
                if "api.vercel.com" in url:
                    return 200, {"id": "dpl_os", "readyState": "READY", "gitSource": {"repo": "mikeajijola/omniseedos", "repoId": 999, "ref": "a" * 40}}
                return super().json_request(url, authenticated, timeout)
        self.assertEqual(self.provider(WrongRepository()).observe({"spec": CONFIG})["status"], "degraded")

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
            {"jsonrpc": "2.0", "id": 2, "method": "provider.apply", "params": {"action": action(dict(CONFIG, sourceCommitSha="main"))}},
            {"jsonrpc": "2.0", "id": 3, "method": "provider.shutdown", "params": {}}
        ]
        process = subprocess.run([sys.executable, "provider/vercel_provider.py"], input="\n".join(json.dumps(item) for item in messages) + "\n", text=True, capture_output=True, check=True)
        output = [json.loads(line) for line in process.stdout.splitlines()]
        self.assertEqual(output[1]["error"]["data"]["code"], "invalid_action")
        self.assertEqual(output[2]["result"], {"shutdown": True})


if __name__ == "__main__":
    unittest.main()
