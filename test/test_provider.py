import json
import copy
import os
import subprocess
import sys
import unittest
from unittest.mock import patch
from pathlib import Path

from provider.vercel_provider import JSON_TURN_PROTOCOL, PROTOCOL, internal_error, ProviderError, VercelProvider

SHA = "a" * 40
BASE = {"projectId": "lily-production", "sourceRepository": "mikeajijola/omniseed-lily", "sourceRepositoryId": 123456, "sourceCommitSha": SHA, "expectedCompanyId": "omniseed_ecosystem", "expectedEnvironment": "production", "target": "production"}
AGENT = {**BASE, "agentIdentity": "lily", "runtimeModel": "nvidia/nemotron-3.5-lightning-free", "secretReferences": ["OMNISEED_OPERATION_TOKEN", "EVE_MODEL_TOKEN", "LILY_SESSION_JWT_SECRET"], "observationCredentialReference": "EVE_MODEL_TOKEN", "healthPath": "/eve/v1/health", "infoPath": "/eve/v1/info", "operationEndpoint": "https://omniseed-os.vercel.app", "operationCredentialReference": "OMNISEED_OPERATION_TOKEN", "sessionCredentialReference": "LILY_SESSION_JWT_SECRET", "sessionIssuer": "omniseed", "sessionAudience": "omniseed-lily"}
CONNECTOR = {**BASE, "sourceRepository": "mikeajijola/omniseedos", "expectedRepository": "mikeajijola/omniseed-ecosystem-company", "desiredRevision": "b" * 40, "companyDefinitionPath": "omniform.yaml", "stewardActorId": "lily", "readOnlyInspection": True}
CANONICAL_AGENT = {
    "kind": "ai_agent", "organisationalIdentity": "lily",
    "bootstrap": {"company": "omniseed_ecosystem", "identity": "lily", "omniseedEndpoint": "https://omniseed-os.vercel.app", "credentialReference": "OMNISEED_OPERATION_TOKEN"},
    "implementation": {"framework": "eve", "model": "nvidia/nemotron-3.5-lightning-free", "repository": "https://github.com/mikeajijola/omniseed-lily.git", "repositoryId": 123456, "revision": SHA},
    "runtime": {"project": "lily-production", "environment": "production", "provider": "vercel", "secretReferences": ["OMNISEED_OPERATION_TOKEN", "LILY_RUNTIME_OBSERVATION_TOKEN", "LILY_SESSION_JWT_SECRET"], "observationCredentialReference": "LILY_RUNTIME_OBSERVATION_TOKEN", "session": {"credentialReference": "LILY_SESSION_JWT_SECRET", "issuer": "omniseed", "audience": "omniseed-lily"}, "expectedEndpoints": {"health": "https://omniseed-lily.vercel.app/health", "info": "https://omniseed-lily.vercel.app/info", "operation": "https://omniseed-lily.vercel.app/eve/v1/session"}}
}
CANONICAL_CONNECTOR = {
    "project": "omniseed-os", "environment": "production", "provider": "vercel",
    "access": {"stewardChat": "public"},
    "source": {"repository": "https://github.com/mikeajijola/omniseedos.git", "repositoryId": 987654, "revision": SHA},
    "companyBinding": {"companyId": "omniseed_ecosystem", "repository": "https://github.com/mikeajijola/omniseed-ecosystem-company.git", "desiredRevision": "b" * 40, "path": "omniform.yaml", "stewardActorId": "lily", "readOnlyInspection": True},
    "expectedEndpoints": {"company": "https://omniseed-os.vercel.app/api/company"}
}


def action(family="agents", spec=None, resource_id=None):
    return {"id": "act-1", "family": family, "resourceId": resource_id or ("lily" if family == "agents" else "omniseed_os"), "desired": {"spec": spec or (AGENT if family == "agents" else CONNECTOR)}}


def shared_actions():
    agent = copy.deepcopy(CANONICAL_AGENT)
    agent["runtime"]["project"] = "omniseed-ecosystem-os"
    agent["runtime"]["source"] = {
        "repository": "https://github.com/mikeajijola/omniseedos.git",
        "repositoryId": 987654,
        "revision": "c" * 40,
    }
    connector = copy.deepcopy(CANONICAL_CONNECTOR)
    connector["project"] = "omniseed-ecosystem-os"
    connector["source"]["revision"] = "c" * 40
    return action("agents", agent), action("connectors", connector)


class FakeClient:
    token = "vercel-secret-must-not-leak"
    def __init__(self, project_exists=True, state="READY", runtime=None, commit=SHA, fail_deploy=False, existing_env=None, deployments=None, stale_environment_reads=False):
        self.project_exists, self.state, self.commit, self.fail_deploy = project_exists, state, commit, fail_deploy
        self.runtime = runtime or {"companyRef": "omniseed_ecosystem", "agentIdentity": "lily", "environment": "production", "source": {"repository": "mikeajijola/omniseed-lily", "commitSha": SHA}, "agent": {"framework": "eve"}}
        self.requests = []
        self.existing_env = existing_env or []
        self.deployments = deployments or []
        self.stale_environment_reads = stale_environment_reads
        self.states = list(state) if isinstance(state, (list, tuple)) else None
        self.deployment_source = None

    def request(self, url, authenticated=False, timeout=10, method="GET", body=None, token=None):
        self.requests.append({"url": url, "authenticated": authenticated, "method": method, "body": body, "token": token})
        if url.endswith("/v2/user"):
            return 200, {"user": {"id": "user_1"}}
        if "/v10/projects/" in url and url.split("?")[0].endswith("/env"):
            if method == "GET": return 200, {"envs": [] if self.stale_environment_reads else self.existing_env}
            if method == "POST":
                self.existing_env.append({"id": f"env_{len(self.existing_env) + 1}", **body})
                return 200, {"created": True}
        if "/v9/projects/" in url and "/env/" in url and method == "PATCH":
            environment_id = url.split("/env/", 1)[1].split("?", 1)[0]
            for item in self.existing_env:
                if item.get("id") == environment_id: item.update(body)
            return 200, {"updated": True}
        if "/v9/projects/" in url and method == "GET":
            if not self.project_exists: raise ProviderError("missing", "remote_http_error", {"status": 404})
            return 200, {"id": "prj_1", "name": "lily-production"}
        if url.endswith("/v11/projects") and method == "POST": return 200, {"id": "prj_created"}
        if "/v7/deployments?" in url and method == "GET": return 200, {"deployments": self.deployments}
        if "/v13/deployments" in url and method == "POST":
            if self.fail_deploy: raise ProviderError("api failed", "remote_http_error", {"status": 500})
            self.deployment_source = {
                "repo": body["meta"]["omniseedSourceRepository"].split("/", 1)[1],
                "repoId": body["gitSource"]["repoId"],
                "ref": body["gitSource"]["ref"],
                "repository": body["meta"]["omniseedSourceRepository"],
            }
            return 200, {"id": "dpl_1", "url": "lily.example.test"}
        if "/v13/deployments/" in url:
            state = self.states.pop(0) if self.states else self.state
            source = self.deployment_source or {"repo": "omniseed-lily", "repoId": 123456, "ref": self.commit, "repository": "mikeajijola/omniseed-lily"}
            return 200, {"id": "dpl_1", "readyState": state, "gitSource": {"repo": source["repo"], "repoId": source["repoId"], "ref": source["ref"]}, "meta": {"omniseedSourceRepository": source["repository"]}}
        if url.endswith("/health"): return 200, {"ok": self.state == "READY"}
        if url.endswith("/info"): return 200, self.runtime
        if url.endswith("/eve/v1/session") and method == "POST": return 200, {"ok": True, "sessionId": "ses_1"}
        if url.endswith("/stream"): return 200, {"events": [{"type": "message.appended", "turnId": "turn_1", "data": {"messageDelta": "hello"}}, {"type": "message.completed", "turnId": "turn_1"}]}
        if url.endswith("/api/company"): return 200, {"company": {"id": "omniseed_ecosystem"}, "instance": {"desiredState": {"repository": "https://github.com/mikeajijola/omniseed-ecosystem-company.git"}, "desiredRevision": "b" * 40, "environment": "production-read-only-inspection"}}
        return 200, {}

    def json_request(self, url, authenticated=False, timeout=10, token=None):
        return self.request(url, authenticated, timeout, token=token)

    def text_request(self, url, authenticated=False, timeout=10, method="GET", body=None, token=None):
        self.requests.append({"url": url, "authenticated": authenticated, "method": method, "body": body, "token": token})
        return 200, '\n'.join([json.dumps({"type": "message.appended", "turnId": "turn_1", "data": {"messageDelta": "hello"}}), json.dumps({"type": "message.completed", "turnId": "turn_1"})])


class JsonRuntimeClient(FakeClient):
    def __init__(self, protocol=JSON_TURN_PROTOCOL):
        super().__init__(runtime={
            "companyRef": "omniseed_ecosystem", "agentIdentity": "sage", "environment": "production",
            "source": {"repository": "example/sage", "commitSha": SHA},
            "product": "sage-runtime", "protocol": protocol
        })

    def request(self, url, authenticated=False, timeout=10, method="GET", body=None, token=None):
        if url.endswith("/agent/v1/turn"):
            self.requests.append({"url": url, "authenticated": authenticated, "method": method, "body": body, "token": token})
            return 200, {"sessionId": "json_session", "turnId": "json_turn", "response": "hello from sage"}
        return super().request(url, authenticated, timeout, method, body, token)


def binding(spec=AGENT, family="agents"):
    return {"providerResourceId": "vercel://lily-production/deployments/dpl_1", "attributes": {"family": family, "resourceId": "lily", "spec": {**spec, "deploymentId": "dpl_1", "deploymentUrl": "https://lily.example.test", **({"companyBindingUrl": "https://lily.example.test/api/company"} if family == "connectors" else {})}}}


class ProviderTests(unittest.TestCase):
    def provider(self, client=None, configuration=None):
        provider = VercelProvider({"runtimeAuthTokenEnv": "EVE_RUNTIME_TOKEN", **(configuration or {})}, client or FakeClient())
        provider.company_id = "omniseed_ecosystem"
        return provider

    def test_unexpected_internal_diagnostics_fail_closed_without_exception_values(self):
        try:
            raise TypeError("secret-value-must-not-leak")
        except TypeError as error:
            diagnostic = internal_error(error)
        payload = json.dumps(diagnostic)
        self.assertEqual(diagnostic["data"]["code"], "provider_internal_error")
        self.assertEqual(diagnostic["data"]["exceptionType"], "TypeError")
        self.assertNotIn("secret-value-must-not-leak", payload)
        self.assertTrue(diagnostic["data"]["frames"])

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

    def test_explicit_protocol_selects_second_runtime_adapter_and_neutral_environment(self):
        desired = copy.deepcopy(CANONICAL_AGENT)
        desired["organisationalIdentity"] = "sage"
        desired["bootstrap"]["identity"] = "sage"
        desired["implementation"] = {**desired["implementation"], "product": "sage-runtime", "framework": "custom", "repository": "https://github.com/example/sage.git"}
        desired["runtime"]["interaction"] = {"protocol": JSON_TURN_PROTOCOL, "path": "/agent/v1/turn"}
        desired["runtime"]["environmentMapping"] = {
            "company": "AGENT_COMPANY", "identity": "AGENT_IDENTITY", "environment": "AGENT_ENVIRONMENT",
            "sourceRepository": "AGENT_SOURCE_REPOSITORY", "sourceCommitSha": "AGENT_SOURCE_COMMIT",
            "model": "AGENT_MODEL", "product": "AGENT_PRODUCT", "protocol": "AGENT_PROTOCOL"
        }
        normalized = self.provider()._spec(action("agents", desired, "sage"))
        self.assertEqual(normalized["agentProduct"], "sage-runtime")
        self.assertEqual(normalized["interactionProtocol"], JSON_TURN_PROTOCOL)
        environment = self.provider()._public_environment(normalized, "agents")
        self.assertEqual(environment["AGENT_MODEL"], "nvidia/nemotron-3.5-lightning-free")
        self.assertNotIn("LILY_MODEL", environment)
        planned = self.provider(JsonRuntimeClient()).plan(action("agents", desired, "sage"))
        self.assertEqual(planned["implementation"]["protocol"], JSON_TURN_PROTOCOL)
        self.assertEqual(planned["expectedEvidence"], ["vercel_api_response", "agent_runtime_health"])
        eve_spec = self.provider()._spec(action("agents", CANONICAL_AGENT))
        self.assertNotEqual(
            self.provider()._deployment_configuration([("agents", "sage", normalized)])[3],
            self.provider()._deployment_configuration([("agents", "lily", eve_spec)])[3]
        )

    @patch.dict(os.environ, {"LILY_RUNTIME_OBSERVATION_TOKEN": "runtime-secret"})
    def test_second_protocol_observes_and_invokes_with_safe_product_protocol_evidence(self):
        spec = {
            **AGENT, "agentIdentity": "sage", "agentImplementationRepository": "example/sage",
            "agentImplementationCommitSha": SHA, "agentProduct": "sage-runtime",
            "interactionProtocol": JSON_TURN_PROTOCOL, "interactionPath": "/agent/v1/turn",
            "observationCredentialReference": "LILY_RUNTIME_OBSERVATION_TOKEN"
        }
        spec.pop("runtimeModel")
        resource = binding(spec)
        client = JsonRuntimeClient()
        observed = self.provider(client).observe(resource)
        self.assertEqual(observed["status"], "healthy")
        self.assertEqual(observed["evidence"][1]["type"], "agent_runtime_health")
        self.assertEqual(observed["evidence"][1]["product"], "sage-runtime")
        result = self.provider(client).invoke("agent.semantic_turn", {"message": "hi", "resourceBinding": resource}, {"actorId": "sage"})
        self.assertEqual(result["response"], "hello from sage")
        self.assertEqual(result["evidence"]["protocol"], JSON_TURN_PROTOCOL)
        self.assertNotIn("runtime-secret", json.dumps([observed, result]))

        wrong = JsonRuntimeClient(protocol="wrong.protocol/1")
        self.assertEqual(self.provider(wrong).observe(resource)["status"], "degraded")

    def test_missing_runtime_adapter_fails_truthfully(self):
        desired = copy.deepcopy(CANONICAL_AGENT)
        desired["implementation"]["product"] = "unknown-runtime"
        desired["runtime"]["interaction"] = {"protocol": "unknown.protocol/1"}
        issues = self.provider().validate(action("agents", desired))["issues"]
        self.assertIn("runtime_adapter_missing", [item["code"] for item in issues])

    def test_runtime_environment_mapping_cannot_overwrite_secrets_or_alias_fields(self):
        desired = copy.deepcopy(CANONICAL_AGENT)
        desired["runtime"]["environmentMapping"] = {
            "company": "LILY_SESSION_JWT_SECRET",
            "identity": "SHARED_RUNTIME_VALUE",
            "environment": "SHARED_RUNTIME_VALUE",
            "unsupported": "lowercase-name",
        }
        issues = self.provider().validate(action("agents", desired))["issues"]
        codes = [item["code"] for item in issues]
        self.assertIn("environment_secret_conflict", codes)
        self.assertIn("environment_mapping_conflict", codes)
        self.assertEqual(codes.count("invalid_environment_mapping"), 2)

        with self.assertRaises(ProviderError) as raised:
            self.provider().apply(action("agents", desired))
        self.assertEqual(raised.exception.code, "invalid_action")

    def test_nondeployment_connector_is_not_misread_as_a_shared_deployment(self):
        operation = action("connectors", {"companyBinding": "omniseed_ecosystem", "endpoint": "https://omniseed.example"}, "omniseed_operations")
        normalized = self.provider()._spec(operation)
        self.assertIsNone(normalized["projectId"])
        self.assertFalse(self.provider().validate(operation)["valid"])

    def test_canonical_omniform_resources_normalize_without_shadow_deployment_state(self):
        agent = action("agents", CANONICAL_AGENT)
        connector = action("connectors", CANONICAL_CONNECTOR)
        self.assertTrue(self.provider().validate(agent)["valid"])
        self.assertTrue(self.provider().validate(connector)["valid"])
        planned = self.provider().plan(agent)
        self.assertEqual(planned["project"]["id"], "lily-production")
        self.assertEqual(planned["source"], {"repository": "mikeajijola/omniseed-lily", "repositoryId": 123456, "commitSha": SHA})
        applied = self.provider().apply(connector)
        self.assertEqual(applied["attributes"]["spec"]["companyBindingUrl"], "https://omniseed-os.vercel.app/api/company")
        self.assertTrue(applied["attributes"]["spec"]["publicStewardChat"])

    def test_agent_identity_source_remains_separate_from_shared_deployment_source(self):
        desired = copy.deepcopy(CANONICAL_AGENT)
        desired["runtime"]["project"] = "omniseed-ecosystem-os"
        desired["runtime"]["source"] = {
            "repository": "https://github.com/mikeajijola/omniseedos.git",
            "repositoryId": 987654,
            "revision": "c" * 40,
        }
        normalized = self.provider()._spec(action("agents", desired))
        self.assertEqual(normalized["sourceRepository"], "mikeajijola/omniseedos")
        self.assertEqual(normalized["sourceCommitSha"], "c" * 40)
        self.assertEqual(normalized["agentImplementationRepository"], "mikeajijola/omniseed-lily")
        self.assertEqual(normalized["agentImplementationCommitSha"], SHA)
        self.assertEqual(normalized["runtimeUrl"], "https://omniseed-lily.vercel.app")
        environment = self.provider()._public_environment(normalized, "agents")
        self.assertEqual(environment["OMNISEED_SOURCE_REPOSITORY"], "mikeajijola/omniseed-lily")
        self.assertEqual(environment["OMNISEED_SOURCE_COMMIT_SHA"], SHA)
        self.assertEqual(environment["LILY_MODEL"], "nvidia/nemotron-3.5-lightning-free")

    @patch.dict(os.environ, {
        "OMNISEED_OPERATION_TOKEN": "operation-secret", "LILY_RUNTIME_OBSERVATION_TOKEN": "observation-secret",
        "LILY_SESSION_JWT_SECRET": "session-secret"
    })
    def test_shared_agent_and_interface_resources_create_one_immutable_deployment(self):
        agent, connector = shared_actions()
        client = FakeClient()
        provider = self.provider(client)
        provider.initialize({
            "protocolVersion": PROTOCOL,
            "configuration": {"runtimeAuthTokenEnv": "LILY_RUNTIME_OBSERVATION_TOKEN"},
            "context": {"companyId": "omniseed_ecosystem", "desiredResources": [
                {"family": "agents", "id": "lily", "spec": agent["desired"]["spec"]},
                {"family": "connectors", "id": "omniseed_operations", "spec": {"companyBinding": "omniseed_ecosystem", "endpoint": "https://omniseed.example"}},
                {"family": "connectors", "id": "omniseed_os", "spec": connector["desired"]["spec"]}
            ]}
        })
        lily = provider.apply(agent)
        interface = provider.apply(connector)
        deployments = [request for request in client.requests if request["method"] == "POST" and "/v13/deployments" in request["url"]]
        self.assertEqual(len(deployments), 1)
        self.assertEqual(lily["providerResourceId"], interface["providerResourceId"])
        self.assertEqual(lily["attributes"]["sharedResources"], ["agents:lily", "connectors:omniseed_os"])
        self.assertEqual(interface["attributes"]["deploymentChange"], "reuse")
        self.assertIsNone(deployments[0]["body"]["projectSettings"]["framework"])
        self.assertEqual(deployments[0]["body"]["projectSettings"]["buildCommand"], "npm run build:vercel")
        self.assertNotIn("outputDirectory", deployments[0]["body"]["projectSettings"])
        environment_keys = {item["key"] for item in client.existing_env}
        self.assertTrue({"LILY_MODEL", "OMNISEED_COMPANY_DEFINITION_URL", "OMNISEED_STEWARD_ACTOR_ID"}.issubset(environment_keys))

    @patch.dict(os.environ, {
        "OMNISEED_OPERATION_TOKEN": "operation-secret", "LILY_RUNTIME_OBSERVATION_TOKEN": "observation-secret",
        "LILY_SESSION_JWT_SECRET": "session-secret"
    })
    def test_transaction_reuses_exact_deployment_when_environment_reads_are_stale(self):
        agent, connector = shared_actions()
        client = FakeClient(stale_environment_reads=True)
        provider = self.provider(client)
        provider.initialize({
            "protocolVersion": PROTOCOL, "configuration": {},
            "context": {"companyId": "omniseed_ecosystem", "desiredResources": [
                {"family": "agents", "id": "lily", "spec": agent["desired"]["spec"]},
                {"family": "connectors", "id": "omniseed_os", "spec": connector["desired"]["spec"]}
            ]}
        })
        lily = provider.apply(agent)
        interface = provider.apply(connector)
        deployments = [request for request in client.requests if request["method"] == "POST" and "/v13/deployments" in request["url"]]
        self.assertEqual(len(deployments), 1)
        self.assertEqual(interface["providerResourceId"], lily["providerResourceId"])
        self.assertEqual(interface["attributes"]["deploymentChange"], "reuse")

    @patch.dict(os.environ, {
        "OMNISEED_OPERATION_TOKEN": "operation-secret", "LILY_RUNTIME_OBSERVATION_TOKEN": "observation-secret",
        "LILY_SESSION_JWT_SECRET": "session-secret"
    })
    def test_shared_deployment_is_recovered_and_reused_after_provider_restart(self):
        agent, connector = shared_actions()
        resources = [
            {"family": "agents", "id": "lily", "spec": agent["desired"]["spec"]},
            {"family": "connectors", "id": "omniseed_os", "spec": connector["desired"]["spec"]}
        ]
        first_client, first = FakeClient(), self.provider()
        first.client = first_client
        first.initialize({"protocolVersion": PROTOCOL, "configuration": {}, "context": {"companyId": "omniseed_ecosystem", "desiredResources": resources}})
        created = first.apply(agent)
        deployment_request = next(request for request in first_client.requests if request["method"] == "POST" and "/v13/deployments" in request["url"])
        listed = {"uid": "dpl_1", "url": "lily.example.test", "state": "READY", "meta": deployment_request["body"]["meta"]}
        restarted_client = FakeClient(existing_env=copy.deepcopy(first_client.existing_env), deployments=[listed], commit="c" * 40)
        restarted_client.deployment_source = copy.deepcopy(first_client.deployment_source)
        restarted = self.provider(restarted_client)
        restarted.initialize({"protocolVersion": PROTOCOL, "configuration": {}, "context": {"companyId": "omniseed_ecosystem", "desiredResources": resources}})
        recovered = restarted.apply(connector)
        self.assertEqual(recovered["providerResourceId"], created["providerResourceId"])
        self.assertEqual(recovered["attributes"]["deploymentChange"], "reuse")
        self.assertFalse(any(request["method"] == "POST" and "/v13/deployments" in request["url"] for request in restarted_client.requests))

    def test_plan_reports_create_or_reuse_exact_revision_bindings_and_evidence(self):
        reused = self.provider().plan(action())
        created = self.provider(FakeClient(project_exists=False)).plan(action())
        self.assertEqual(reused["project"]["change"], "reuse")
        self.assertEqual(created["project"]["change"], "create")
        self.assertEqual(reused["source"]["commitSha"], SHA)
        self.assertEqual(reused["environmentBindings"], sorted([
            "EVE_MODEL_TOKEN", "LILY_SESSION_JWT_SECRET", "OMNISEED_OPERATION_TOKEN",
            "OMNISEED_AGENT_IDENTITY", "OMNISEED_COMPANY_REF", "OMNISEED_ENVIRONMENT",
            "OMNISEED_OPERATION_CREDENTIAL_ENV", "OMNISEED_OPERATION_ENDPOINT",
            "OMNISEED_SESSION_CREDENTIAL_ENV", "OMNISEED_SESSION_JWT_AUDIENCE",
            "OMNISEED_SESSION_JWT_ISSUER", "OMNISEED_SOURCE_COMMIT_SHA",
            "OMNISEED_SOURCE_REPOSITORY", "LILY_MODEL"
        ]))
        self.assertIn("eve_agent_runtime_health", reused["expectedEvidence"])

    @patch.dict(os.environ, {"OMNISEED_OPERATION_TOKEN": "operation-secret", "EVE_MODEL_TOKEN": "model-secret", "LILY_SESSION_JWT_SECRET": "session-secret"})
    def test_apply_creates_project_configures_environment_and_deploys_exact_sha_without_evidence_leak(self):
        client = FakeClient(project_exists=False)
        result = self.provider(client).apply(action())
        self.assertEqual(result["attributes"]["projectChange"], "create")
        deployment = [r for r in client.requests if r["method"] == "POST" and "/v13/deployments" in r["url"]][0]
        self.assertEqual(deployment["body"]["gitSource"]["ref"], SHA)
        self.assertEqual(deployment["body"]["gitSource"]["sha"], SHA)
        payload = json.dumps(deployment["body"])
        self.assertNotIn("operation-secret", payload)
        self.assertNotIn("model-secret", payload)
        environment_requests = [r for r in client.requests if "/env" in r["url"] and r["method"] in {"POST", "PATCH"}]
        self.assertTrue(any(r["body"].get("key") == "OMNISEED_OPERATION_TOKEN" and r["body"].get("type") == "sensitive" and r["body"].get("value") == "operation-secret" for r in environment_requests))
        self.assertTrue(any(r["body"].get("key") == "LILY_SESSION_JWT_SECRET" and r["body"].get("type") == "sensitive" and r["body"].get("value") == "session-secret" for r in environment_requests))
        self.assertTrue(any(r["body"].get("key") == "OMNISEED_SESSION_CREDENTIAL_ENV" and r["body"].get("value") == "LILY_SESSION_JWT_SECRET" for r in environment_requests))
        self.assertEqual(deployment["body"]["projectSettings"]["framework"], "eve")
        self.assertNotIn(FakeClient.token, payload)
        self.assertNotIn("actual-secret", payload)
        self.assertNotIn("operation-secret", json.dumps(result))

    @patch.dict(os.environ, {"OMNISEED_OPERATION_TOKEN": "operation-secret", "EVE_MODEL_TOKEN": "model-secret", "LILY_SESSION_JWT_SECRET": "session-secret"})
    def test_apply_reuses_existing_project_rotates_only_explicit_secret_and_propagates_api_failure(self):
        client = FakeClient(project_exists=True, existing_env=[{"id": "env_1", "key": "OMNISEED_OPERATION_TOKEN", "type": "sensitive", "target": ["production", "preview"]}])
        self.provider(client, {"rotateSecretReferences": ["OMNISEED_OPERATION_TOKEN"]}).apply(action())
        self.assertFalse(any(r["method"] == "POST" and r["url"].endswith("/v11/projects") for r in client.requests))
        rotated = [r for r in client.requests if r["method"] == "PATCH" and "/env/env_1" in r["url"]]
        self.assertEqual(len(rotated), 1)
        self.assertEqual(rotated[0]["body"], {"value": "operation-secret", "type": "sensitive", "target": ["production"]})
        with self.assertRaises(ProviderError): self.provider(FakeClient(fail_deploy=True)).apply(action())

    def test_apply_preserves_preprovisioned_vercel_secrets_without_reading_values(self):
        existing = [{"id": f"env_{index}", "key": key, "target": ["production"]} for index, key in enumerate(AGENT["secretReferences"])]
        client = FakeClient(existing_env=existing)
        with patch.dict(os.environ, {}, clear=True):
            result = self.provider(client).apply(action())
        mutated = [request["body"]["key"] for request in client.requests if "/env" in request["url"] and request["method"] in {"POST", "PATCH"}]
        self.assertTrue(set(AGENT["secretReferences"]).isdisjoint(mutated))
        self.assertEqual(result["attributes"]["spec"]["secretReferences"], AGENT["secretReferences"])

    @patch.dict(os.environ, {"OMNISEED_OPERATION_TOKEN": "operation-secret", "EVE_MODEL_TOKEN": "model-secret", "LILY_SESSION_JWT_SECRET": "session-secret"})
    def test_apply_waits_for_immutable_deployment_readiness_and_fails_closed(self):
        desired = {**AGENT, "pollIntervalSeconds": 0.001, "deploymentTimeoutSeconds": 1}
        ready = self.provider(FakeClient(state=["BUILDING", "READY"])).apply(action(spec=desired))
        self.assertEqual(ready["status"], "submitted")
        with self.assertRaises(ProviderError) as failed:
            self.provider(FakeClient(state="ERROR")).apply(action(spec=desired))
        self.assertEqual(failed.exception.code, "deployment_failed")

    def test_apply_fails_closed_when_a_declared_secret_reference_is_unavailable(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ProviderError) as raised:
                self.provider().apply(action())
        self.assertEqual(raised.exception.code, "secret_unavailable")

    def test_apply_rejects_branch_and_non_numeric_repository_identity(self):
        for changed in ({**AGENT, "sourceCommitSha": "main"}, {**AGENT, "sourceRepositoryId": "123"}):
            with self.assertRaises(ProviderError): self.provider().apply(action(spec=changed))

    @patch.dict(os.environ, {"EVE_RUNTIME_TOKEN": "runtime-secret"})
    def test_observe_verifies_deployment_eve_company_identity_environment_and_source(self):
        client = FakeClient()
        resource = binding()
        resource["attributes"]["spec"]["runtimeUrl"] = "https://omniseed-lily.vercel.app"
        provider = self.provider(client)
        result = provider.observe(resource)
        self.assertEqual(result["status"], "healthy")
        self.assertTrue(result["snapshot"]["sourceMatches"])
        self.assertTrue(result["snapshot"]["runtimeIdentityMatches"])
        self.assertEqual([e["type"] for e in result["evidence"]], ["vercel_api_response", "eve_agent_runtime_health"])
        self.assertEqual(len({e["id"] for e in result["evidence"]}), 2)
        repeated = provider.observe(resource)
        self.assertEqual([e["id"] for e in repeated["evidence"]], [e["id"] for e in result["evidence"]])
        self.assertTrue(any(request["url"].startswith("https://omniseed-lily.vercel.app/") for request in client.requests))
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
        client = FakeClient()
        applied = self.provider(client).apply(action("connectors"))
        deployment = [request for request in client.requests if request["method"] == "POST" and "/v13/deployments" in request["url"]][0]
        self.assertTrue(all(isinstance(value, str) for value in deployment["body"]["meta"].values()))
        connector_binding = applied
        observed = self.provider(client).observe(connector_binding)
        self.assertEqual(observed["status"], "healthy")
        self.assertEqual(observed["evidence"][1]["type"], "http_company_binding")
        self.assertTrue(all(item["id"].startswith("vercel_") for item in observed["evidence"]))

    def test_connector_apply_provisions_safe_immutable_company_binding_environment(self):
        client = FakeClient()
        desired = {**CONNECTOR, "publicStewardChat": True}
        self.provider(client).apply(action("connectors", desired))
        values = {request["body"]["key"]: request["body"] for request in client.requests if "/env" in request["url"] and request["method"] in {"POST", "PATCH"}}
        self.assertEqual(values["OMNISEED_DESIRED_REVISION"]["value"], "b" * 40)
        self.assertEqual(values["OMNISEED_COMPANY_DEFINITION_URL"]["value"], f"https://raw.githubusercontent.com/mikeajijola/omniseed-ecosystem-company/{'b' * 40}/omniform.yaml")
        self.assertEqual(values["OMNISEED_STEWARD_ACTOR_ID"]["value"], "lily")
        self.assertEqual(values["OMNISEED_READ_ONLY_INSPECTION"]["value"], "true")
        self.assertEqual(values["OMNISEED_PUBLIC_STEWARD_CHAT"]["value"], "true")
        self.assertTrue(all(value["type"] == "encrypted" for value in values.values()))

    @patch.dict(os.environ, {"DATABASE_URL": "database-secret", "OMNISEED_STATE_TOKEN": "state-secret", "OMNISEED_OPERATOR_TOKEN": "operator-secret", "OMNISEED_OPERATION_TOKEN": "operation-secret", "LILY_SESSION_JWT_SECRET": "session-secret"})
    def test_production_connector_binds_durable_state_and_server_credentials_without_evidence_leak(self):
        client = FakeClient()
        production = {
            **CONNECTOR,
            "readOnlyInspection": False,
            "stateEndpoint": "https://omniseed-os.vercel.app/api/state/companies/omniseed_ecosystem/state",
            "secretReferences": ["DATABASE_URL", "OMNISEED_STATE_TOKEN", "OMNISEED_OPERATOR_TOKEN", "OMNISEED_OPERATION_TOKEN", "LILY_SESSION_JWT_SECRET"]
        }
        result = self.provider(client).apply(action("connectors", production))
        values = {request["body"]["key"]: request["body"] for request in client.requests if "/env" in request["url"] and request["method"] in {"POST", "PATCH"}}
        self.assertEqual(values["OMNISEED_STATE_ENDPOINT"]["value"], production["stateEndpoint"])
        self.assertEqual(values["OMNISEED_READ_ONLY_INSPECTION"]["value"], "false")
        for key in production["secretReferences"]:
            self.assertEqual(values[key]["type"], "sensitive")
        serialized = json.dumps(result)
        for secret in ["database-secret", "state-secret", "operator-secret", "operation-secret", "session-secret"]:
            self.assertNotIn(secret, serialized)

    def test_connector_combines_durable_and_interface_secret_references(self):
        desired = {**CANONICAL_CONNECTOR, "secretReferences": ["LILY_SESSION_JWT_SECRET"], "durableState": {"credentialReferences": ["DATABASE_URL", "OMNISEED_STATE_TOKEN"]}}
        spec = self.provider()._spec(action("connectors", desired))
        self.assertEqual(spec["secretReferences"], ["DATABASE_URL", "OMNISEED_STATE_TOKEN", "LILY_SESSION_JWT_SECRET"])

    def test_connector_observation_reads_engine_instance_projection(self):
        client = FakeClient()
        original = client.request
        def projected(url, *args, **kwargs):
            if url.endswith("/api/company"):
                return 200, {"company": {"id": "omniseed_ecosystem"}, "instance": {"desiredState": {"repository": "https://github.com/mikeajijola/omniseed-ecosystem-company.git"}, "desiredRevision": "b" * 40, "environment": "production-read-only-inspection"}}
            return original(url, *args, **kwargs)
        client.request = projected
        connector = binding(CONNECTOR, "connectors")
        connector["attributes"]["spec"]["sourceRepositoryId"] = 123456
        connector["attributes"]["spec"]["sourceRepository"] = "mikeajijola/omniseed-lily"
        observed = self.provider(client).observe(connector)
        self.assertEqual(observed["status"], "healthy")
        self.assertEqual(observed["snapshot"]["desiredRevision"], "b" * 40)

    def test_production_promotion_requires_persisted_ready_source_binding(self):
        client = FakeClient()
        connector = self.provider(client).apply(action("connectors"))
        result = self.provider(client).invoke("interface.deployment.promote", {"resourceBinding": connector}, {"actorId": "operator"})
        promotion = [request for request in client.requests if request["method"] == "POST" and "/promote/" in request["url"]]
        self.assertEqual(len(promotion), 1)
        self.assertIn("/projects/prj_1/promote/", promotion[0]["url"])
        self.assertEqual(result["status"], "promoted")
        self.assertEqual(result["evidence"]["deploymentId"], connector["attributes"]["spec"]["deploymentId"])

        connector["attributes"]["spec"]["sourceCommitSha"] = "c" * 40
        with self.assertRaises(ProviderError) as raised:
            self.provider(client).invoke("interface.deployment.promote", {"resourceBinding": connector}, {"actorId": "operator"})
        self.assertEqual(raised.exception.code, "promotion_precondition_failed")

    def test_status_checks_the_supplying_provider_boundary(self):
        client = FakeClient()
        provider = VercelProvider({"teamId": "team_1", "statusProjectId": "omniseed-ecosystem-os"}, client)
        self.assertEqual(provider.status(), {"implementation_available": True, "configured": True, "connected": True, "healthy": True})
        self.assertEqual(client.requests[-1]["url"], "https://api.vercel.com/v9/projects/omniseed-ecosystem-os?teamId=team_1")
        provider = VercelProvider({}, FakeClient())
        provider.client.token = None
        self.assertEqual(provider.status(), {"implementation_available": True, "configured": False, "connected": False, "healthy": False})

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
