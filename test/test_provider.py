import importlib.util
import json
import pathlib
import unittest

MODULE_PATH = pathlib.Path(__file__).parents[1] / "provider" / "eve_provider.py"
SPEC = importlib.util.spec_from_file_location("eve_provider", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class FakeClient:
    def health(self): return {"ok": True, "status": "healthy"}
    def info(self): return {"agent": {"framework": "eve", "model": "google/gemini"}}
    def turn(self, message): return {"sessionId": "ses_1", "turnId": "turn_1", "response": "semantic: " + message}


def config():
    return {"runtimeUrl": "https://agent.example.test", "companyRef": "example_company", "agentIdentity": "agent_one", "resourceId": "primary_agent", "offers": ["decision_agency"], "authTokenEnv": "EVE_AUTH_TOKEN"}


class ProviderTests(unittest.TestCase):
    def provider(self):
        provider = MODULE.EveProvider(config(), FakeClient())
        provider.company_id = "example_company"
        return provider

    def test_manifest_and_initialization_advertise_only_agents(self):
        result = self.provider().initialize({"protocolVersion": MODULE.PROTOCOL, "configuration": config(), "context": {"companyId": "example_company"}})
        manifest = json.loads((MODULE_PATH.parents[1] / "provider-package.json").read_text())
        self.assertEqual(result["primitiveFamilies"], ["agents"])
        self.assertEqual(result["primitiveFamilies"], manifest["primitiveFamilies"])
        self.assertEqual(result["operations"], manifest["operations"])
        self.assertNotIn("github", json.dumps(result).lower())
        self.assertEqual(result["offerings"][0]["resource"]["id"], "primary_agent")
        self.assertEqual(result["offerings"][0]["resource"]["offers"], ["decision_agency"])
        self.assertNotIn("stewardship_agency", json.dumps(result))

    def test_bootstrap_is_company_reference_identity_and_auth_reference_only(self):
        initialized = self.provider().initialize({"protocolVersion": MODULE.PROTOCOL, "configuration": config(), "context": {"companyId": "example_company"}})
        spec = initialized["offerings"][0]["resource"]["spec"]
        self.assertEqual(spec, {"companyRef": "example_company", "agentIdentity": "agent_one", "runtimeUrl": "https://agent.example.test"})
        self.assertNotIn("authTokenEnv", spec)

    def test_company_context_mismatch_fails_validation(self):
        provider = self.provider()
        provider.company_id = "another_company"
        result = provider.validate()
        self.assertFalse(result["valid"])
        self.assertEqual(result["issues"][0]["code"], "company_mismatch")

    def test_observe_records_real_runtime_response_as_evidence(self):
        result = self.provider().observe({"providerResourceId": "eve://lily"})
        self.assertEqual(result["status"], "healthy")
        self.assertEqual(result["evidence"][0]["type"], "eve_agent_runtime_health")
        self.assertEqual(result["snapshot"]["runtime"]["agent"]["framework"], "eve")

    def test_semantic_turn_requires_declared_actor_and_returns_evidence(self):
        result = self.provider().invoke("agent.semantic_turn", {"message": "What company?"}, {"actorId": "agent_one"})
        self.assertEqual(result["response"], "semantic: What company?")
        self.assertEqual(result["evidence"]["sessionId"], "ses_1")
        with self.assertRaises(MODULE.EveError):
            self.provider().invoke("agent.semantic_turn", {"message": "hello"}, {"actorId": "other_agent"})

    def test_apply_refuses_unhealthy_runtime(self):
        class Down(FakeClient):
            def health(self): raise MODULE.EveError("down")
        provider = MODULE.EveProvider(config(), Down())
        provider.company_id = "example_company"
        with self.assertRaises(MODULE.EveError): provider.apply({"id": "bind"})


if __name__ == "__main__":
    unittest.main()
