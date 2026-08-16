import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch
from pathlib import Path

from provider.vercel_provider import PROTOCOL, ProviderError, VercelProvider

SHA = "a" * 40
BASE = {"projectId": "lily-production", "sourceRepository": "mikeajijola/omniseed-lily", "sourceRepositoryId": 123456, "sourceCommitSha": SHA, "expectedCompanyId": "omniseed_ecosystem", "expectedEnvironment": "production", "target": "production"}
AGENT = {**BASE, "agentIdentity": "lily", "secretReferences": {"OMNISEED_OPERATION_TOKEN": "sec_operation_token", "EVE_MODEL_TOKEN": "sec_model_token"}, "healthPath": "/eve/v1/health", "infoPath": "/eve/v1/info"}
CONNECTOR = {**BASE, "sourceRepository": "mikeajijola/omniseedos", "expectedRepository": "mikeajijola/omniseed-ecosystem-company"}


def action(family="agents", spec=None, resource_id=None):
    return {"id": "act-1", "family": family, "resourceId": resource_id or ("lily" if family == "agents" else "omniseed_os"), "desired": {"spec": spec or (AGENT if family == "agents" else CONNECTOR)}}


class FakeClient:
    token = "vercel-secret-must-not-leak"
    def __init__(self, project_exists=True, state="READY", runtime=None, commit=SHA, fail_deploy=False):
        self.project_exists, self.state, self.commit, self.fail_deploy = project_exists, state, commit, fail_deploy
        self.runtime = runtime or {"companyRef": "omniseed_ecosystem", "agentIdentity": "lily", "environment": "production", "source": {"repository": "mikeajijola/omniseed-lily", "commitSha": SHA}, "agent": {"framework": "eve"}}
        self.requests = []

    def request(self, url, authenticated=False, timeout=10, method="GET", body=None, token=None):
        self.requests.append({"url": url, "authenticated": authenticated, "method": method, "body": body, "token": token})
        if "/v11/projects/" in url and method == "GET":
            if not self.project_exists: raise ProviderError("missing", "remote_http_error", {"status": 404})
            return 200, {"id": "prj_1", "name": "lily-production"}
        if url.endswith("/v11/projects") and method == "POST": return 200, {"id": "prj_created"}
        if "/v13/deployments" in url and method == "POST":
            if self.fail_deploy: raise ProviderError("api failed", "remote_http_error", {"status": 500})
            return 200, {"id": "dpl_1", "url": "lily.example.test"}
        if "/v13/deployments/" in url:
            repository = "mikeajijola/omniseed-lily" if "lily" in url or True else "mikeajijola/omniseedos"
            return 200, {"id": "dpl_1", "readyState": self.state, "gitSource": {"repo": repository, "repoId": 123456, "ref": self.commit}}
        if url.endswith("/health"): return 200, {"ok": self.state == "READY"}
        if url.endswith("/info"): return 200, self.runtime
        if url.endswith("/eve/v1/session") and method == "POST": return 200, {"ok": True, "sessionId": "ses_1"}
        if url.endswith("/stream"): return 200, {"events": [{"type": "message.appended", "turnId": "turn_1", "data": {"messageDelta": "hello"}}, {"type": "message.completed", "turnId": "turn_1"}]}
        if url.endswith("/api/company"): return 200, {"companyId": "omniseed_ecosystem", "canonicalRepository": "mikeajijola/omniseed-ecosystem-company", "environment": "production"}
        return 200, {}

    def json_request(self, url, authenticated=False, timeout=10, token=None):
        return self.request(url, authenticated, timeout, token=token)


def binding(spec=AGENT, family="agents"):
    return {"providerResourceId": "vercel://lily-production/deployments/dpl_1", "attributes": {"family": family, "resourceId": "lily", "spec": {**spec, "deploymentId": "dpl_1", "deploymentUrl": "https://lily.example.test", **({"companyBindingUrl": "https://lily.example.test/api/company"} if family == "connectors" else {})}}}


class ProviderTests(unittest.TestCase):
    def provider(self, client=None):
        provider = VercelProvider({"runtimeAuthTokenEnv": "EVE_RUNTIME_TOKEN"}, client or FakeClient())
        provider.company_id = "omniseed_ecosystem"
        return provider

    def test_manifest_and_runtime_advertise_one_provider_for_both_families(self):
        result = self.provider().initialize({"protocolVersion": PROTOCOL, "configuration": {}, "context": {"companyId": "omniseed_ecosystem"}})
        manifest = json.loads(Path("provider-package.json").read_text(encoding="utf-8"))
        self.assertEqual(result["provider"]["id"], "vercel")
        self.assertEqual(result["primitiveFamilies"], ["agents", "connectors"])
        self.assertEqual(result["primitiveFamilies"], manifest["primitiveFamilies"])
        self.assertEqual(result["operations"], manifest["operations"])

    def test_validate_dispatches_families_and_rejects_arbitrary_runtime(self):
        self.assertTrue(self.provider().validate(action("agents"))["valid"])
        self.assertTrue(self.provider().validate(action("connectors"))["valid"])
        self.assertFalse(self.provider().validate(action("workflows"))["valid"])
        self.assertFalse(self.provider().validate(action("agents", {**AGENT, "runtimeUrl": "https://caller.test"}))["valid"])

    def test_plan_reports_create_or_reuse_exact_revision_bindings_and_evidence(self):
        reused = self.provider().plan(action())
        created = self.provider(FakeClient(project_exists=False)).plan(action())
        self.assertEqual(reused["project"]["change"], "reuse")
        self.assertEqual(created["project"]["change"], "create")
        self.assertEqual(reused["source"]["commitSha"], SHA)
        self.assertEqual(reused["environmentBindings"], ["EVE_MODEL_TOKEN", "OMNISEED_OPERATION_TOKEN"])
        self.assertIn("eve_agent_runtime_health", reused["expectedEvidence"])

    def test_apply_creates_project_and_deploys_exact_sha_without_secret_values(self):
        client = FakeClient(project_exists=False)
        result = self.provider(client).apply(action())
        self.assertEqual(result["attributes"]["projectChange"], "create")
        deployment = [r for r in client.requests if r["method"] == "POST" and "/v13/deployments" in r["url"]][0]
        self.assertEqual(deployment["body"]["gitSource"]["ref"], SHA)
        payload = json.dumps(deployment["body"])
        self.assertIn("sec_operation_token", payload)
        self.assertNotIn(FakeClient.token, payload)
        self.assertNotIn("actual-secret", payload)

    def test_apply_is_idempotent_for_existing_project_and_propagates_api_failure(self):
        client = FakeClient(project_exists=True)
        self.provider(client).apply(action())
        self.assertFalse(any(r["method"] == "POST" and r["url"].endswith("/v11/projects") for r in client.requests))
        with self.assertRaises(ProviderError): self.provider(FakeClient(fail_deploy=True)).apply(action())

    def test_apply_rejects_branch_and_non_numeric_repository_identity(self):
        for changed in ({**AGENT, "sourceCommitSha": "main"}, {**AGENT, "sourceRepositoryId": "123"}):
            with self.assertRaises(ProviderError): self.provider().apply(action(spec=changed))

    @patch.dict(os.environ, {"EVE_RUNTIME_TOKEN": "runtime-secret"})
    def test_observe_verifies_deployment_eve_company_identity_environment_and_source(self):
        result = self.provider().observe(binding())
        self.assertEqual(result["status"], "healthy")
        self.assertTrue(result["snapshot"]["sourceMatches"])
        self.assertTrue(result["snapshot"]["runtimeIdentityMatches"])
        self.assertEqual([e["type"] for e in result["evidence"]], ["vercel_api_response", "eve_agent_runtime_health"])
        self.assertNotIn("runtime-secret", json.dumps(result))

    @patch.dict(os.environ, {"EVE_RUNTIME_TOKEN": "runtime-secret"})
    def test_observe_degrades_for_wrong_company_identity_source_and_unhealthy_runtime(self):
        variants = [
            FakeClient(runtime={**FakeClient().runtime, "companyRef": "wrong"}),
            FakeClient(runtime={**FakeClient().runtime, "agentIdentity": "wrong"}),
            FakeClient(commit="b" * 40), FakeClient(state="ERROR")
        ]
        for client in variants:
            with self.subTest(client=client): self.assertEqual(self.provider(client).observe(binding())["status"], "degraded")

    def test_connector_apply_and_observe_remain_supported(self):
        applied = self.provider().apply(action("connectors"))
        connector_binding = applied
        client = FakeClient()
        # The fake deployment source defaults to Lily; use metadata-free equality by adapting expected repo.
        connector_binding["attributes"]["spec"]["sourceRepository"] = "mikeajijola/omniseed-lily"
        observed = self.provider(client).observe(connector_binding)
        self.assertEqual(observed["status"], "healthy")
        self.assertEqual(observed["evidence"][1]["type"], "http_company_binding")

    @patch.dict(os.environ, {"EVE_RUNTIME_TOKEN": "runtime-secret"})
    def test_semantic_turn_requires_engine_binding_and_declared_identity(self):
        result = self.provider().invoke("agent.semantic_turn", {"message": "hi", "resourceBinding": binding()}, {"actorId": "lily"})
        self.assertEqual(result["response"], "hello")
        self.assertEqual(result["evidence"]["deploymentId"], "dpl_1")
        for value, actor in [({"message": "hi", "runtimeUrl": "https://evil.test"}, {"actorId": "lily"}), ({"message": "hi", "resourceBinding": binding()}, {"actorId": "other"})]:
            with self.assertRaises(ProviderError): self.provider().invoke("agent.semantic_turn", value, actor)

    def test_protocol_process_reports_validation_error_without_leaking_credentials(self):
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "provider.initialize", "params": {"protocolVersion": PROTOCOL, "configuration": {}, "context": {"companyId": "omniseed_ecosystem"}}},
            {"jsonrpc": "2.0", "id": 2, "method": "provider.apply", "params": {"action": action(spec={**AGENT, "sourceCommitSha": "main"})}},
            {"jsonrpc": "2.0", "id": 3, "method": "provider.shutdown", "params": {}}
        ]
        process = subprocess.run([sys.executable, "provider/vercel_provider.py"], input="\n".join(json.dumps(item) for item in messages) + "\n", text=True, capture_output=True, check=True)
        output = [json.loads(line) for line in process.stdout.splitlines()]
        self.assertEqual(output[1]["error"]["data"]["code"], "invalid_action")
        self.assertNotIn("secret", process.stdout)


if __name__ == "__main__": unittest.main()
