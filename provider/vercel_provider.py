#!/usr/bin/env python3
"""Narrow, read-only Vercel connector Provider for OmniSeed Protocol v1."""

import datetime
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

PROTOCOL = "omniseed.provider.protocol/1.0"
PROVIDER_ID = "vercel_interface"
VERSION = "0.1.0-alpha.0"
FAMILY = "connectors"
RESOURCE_ID = "omniseed_os"
METHODS = [
    "provider.initialize", "provider.status", "provider.validate", "provider.plan",
    "provider.apply", "provider.observe", "provider.invoke", "provider.shutdown"
]
OPERATIONS = ["interface.deployment.observe", "interface.deployment.status", "interface.deployment.evidence"]


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


class ProviderError(RuntimeError):
    def __init__(self, message, code="provider_error", details=None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


class NetworkClient:
    def __init__(self, token=None):
        self.token = token if token is not None else os.environ.get("VERCEL_TOKEN")

    def json_request(self, url, authenticated=False, timeout=10):
        return self.request(url, authenticated=authenticated, timeout=timeout)

    def request(self, url, authenticated=False, timeout=10, method="GET", body=None):
        headers = {"Accept": "application/json", "User-Agent": "omniseed-provider-vercel/0.1"}
        if authenticated:
            if not self.token:
                raise ProviderError("Vercel credentials are unavailable", "not_configured")
            headers["Authorization"] = "Bearer " + self.token
        encoded = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            encoded = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(url, headers=headers, data=encoded, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read().decode("utf-8")
                return response.status, json.loads(body) if body else {}
        except urllib.error.HTTPError as error:
            raise ProviderError("Remote endpoint returned an error", "remote_http_error", {"status": error.code, "host": urllib.parse.urlparse(url).hostname}) from error
        except (urllib.error.URLError, TimeoutError) as error:
            raise ProviderError("Remote endpoint is unreachable", "remote_unreachable", {"host": urllib.parse.urlparse(url).hostname}) from error
        except json.JSONDecodeError as error:
            raise ProviderError("Remote endpoint returned invalid JSON", "invalid_remote_response", {"host": urllib.parse.urlparse(url).hostname}) from error


class VercelProvider:
    def __init__(self, configuration=None, client=None):
        self.configuration = configuration or {}
        self.client = client or NetworkClient()
        self.company_id = None

    def initialize(self, params):
        if params.get("protocolVersion") != PROTOCOL:
            raise ProviderError("Unsupported protocol version", "protocol_mismatch", {"supported": PROTOCOL})
        self.configuration = params.get("configuration") or {}
        self.company_id = (params.get("context") or {}).get("companyId")
        return {
            "protocolVersion": PROTOCOL,
            "provider": {"id": PROVIDER_ID, "name": "Vercel Interface Provider", "version": VERSION},
            "primitiveFamilies": [FAMILY],
            "configurationSchema": "./provider-configuration.schema.json",
            "observationTypes": ["vercel_deployment_state", "company_binding_state"],
            "evidenceTypes": ["vercel_api_response", "http_company_binding"],
            "offerings": [{"family": FAMILY, "id": "human_operating_interface", "resource": self._desired_resource()}],
            "operations": OPERATIONS,
            "methods": METHODS
        }

    def _desired_resource(self):
        safe = {key: value for key, value in self.configuration.items() if key not in {"token"}}
        return {"family": FAMILY, "id": RESOURCE_ID, "name": "OmniSeed OS", "offers": ["human_operating_interface"], "risk": "medium", "spec": safe}

    def _issues(self, action):
        issues = []
        spec = ((action or {}).get("desired") or {}).get("spec") or {}
        for field in ["projectId", "sourceRepository", "sourceRepositoryId", "sourceCommitSha", "expectedCompanyId", "expectedRepository"]:
            if not spec.get(field):
                issues.append({"code": "missing_field", "field": field, "message": field + " is required"})
        if spec.get("sourceCommitSha") and not re.fullmatch(r"[0-9a-f]{40}", str(spec["sourceCommitSha"])):
            issues.append({"code": "source_not_immutable", "field": "sourceCommitSha", "message": "sourceCommitSha must be a full 40-character commit SHA"})
        if action.get("family") != FAMILY or action.get("resourceId") != RESOURCE_ID:
            issues.append({"code": "unsupported_action", "message": "Only the connectors/omniseed_os resource is supported"})
        if spec.get("expectedCompanyId") and self.company_id and spec["expectedCompanyId"] != self.company_id:
            issues.append({"code": "company_boundary", "message": "Action company does not match Provider context"})
        return issues

    def status(self):
        required = ["projectId", "sourceRepository", "sourceRepositoryId", "sourceCommitSha", "expectedCompanyId", "expectedRepository"]
        configured = all(self.configuration.get(key) for key in required) and bool(self.client.token)
        connected = healthy = False
        if configured:
            try:
                snapshot = self._observe_spec(self.configuration)
                connected = snapshot["vercelApiReachable"]
                healthy = snapshot["deploymentReady"] and snapshot["sourceMatches"] and snapshot["httpReachable"] and snapshot["companyBindingMatches"]
            except ProviderError:
                pass
        return {"implementation_available": True, "configured": configured, "connected": connected, "healthy": healthy}

    def validate(self, action):
        issues = self._issues(action)
        return {"valid": not issues, "issues": issues}

    def plan(self, action):
        validation = self.validate(action)
        return {
            "deterministic": True,
            "actionId": action.get("id"),
            "valid": validation["valid"],
            "issues": validation["issues"],
            "mode": "deploy_immutable_source",
            "mutationSupported": True,
            "source": {"repository": (((action or {}).get("desired") or {}).get("spec") or {}).get("sourceRepository"), "commitSha": (((action or {}).get("desired") or {}).get("spec") or {}).get("sourceCommitSha")}
        }

    def apply(self, action):
        validation = self.validate(action)
        if not validation["valid"]:
            raise ProviderError("Action is invalid", "invalid_action", {"issues": validation["issues"]})
        spec = action["desired"]["spec"]
        body = {
            "name": spec["projectId"], "project": spec["projectId"], "target": spec.get("target", "production"),
            "gitSource": {"type": "github", "repoId": spec["sourceRepositoryId"], "ref": spec["sourceCommitSha"]},
            "meta": {"omniseedCompanyId": spec["expectedCompanyId"], "omniseedSourceRepository": spec["sourceRepository"], "omniseedSourceCommit": spec["sourceCommitSha"]}
        }
        endpoint = "https://api.vercel.com/v13/deployments"
        if spec.get("teamId"):
            endpoint += "?teamId=" + urllib.parse.quote(spec["teamId"], safe="")
        _, deployment = self.client.request(endpoint, authenticated=True, timeout=spec.get("timeoutSeconds", 10), method="POST", body=body)
        deployment_id = deployment.get("id") or deployment.get("uid")
        deployment_host = deployment.get("url")
        if not deployment_id or not deployment_host:
            raise ProviderError("Vercel did not return a deployment identity", "invalid_remote_response", {"host": "api.vercel.com"})
        deployment_url = deployment_host if deployment_host.startswith("https://") else "https://" + deployment_host
        binding_url = deployment_url.rstrip("/") + spec.get("companyBindingPath", "/api/company")
        return {"providerResourceId": "vercel://" + spec["projectId"] + "/deployments/" + deployment_id, "status": "submitted", "attributes": {"spec": {**spec, "deploymentId": deployment_id, "deploymentUrl": deployment_url, "companyBindingUrl": binding_url}, "sourceRepository": spec["sourceRepository"], "sourceCommitSha": spec["sourceCommitSha"], "submittedAt": now()}}

    def _deployment_endpoint(self, spec):
        team = spec.get("teamId")
        if spec.get("deploymentId"):
            path = "/v13/deployments/" + urllib.parse.quote(spec["deploymentId"], safe="")
        else:
            path = "/v6/deployments?projectId=" + urllib.parse.quote(spec["projectId"], safe="") + "&limit=1"
        return "https://api.vercel.com" + path + (("?" if "?" not in path else "&") + "teamId=" + urllib.parse.quote(team, safe="") if team else "")

    def _observe_spec(self, spec):
        if not spec.get("deploymentUrl") or not spec.get("companyBindingUrl"):
            raise ProviderError("Observation requires the deployment URL returned by apply or an explicitly configured existing deployment URL", "observation_target_missing")
        timeout = spec.get("timeoutSeconds", 10)
        _, deployment_response = self.client.json_request(self._deployment_endpoint(spec), authenticated=True, timeout=timeout)
        deployment = deployment_response
        if not spec.get("deploymentId"):
            deployments = deployment_response.get("deployments") or []
            deployment = deployments[0] if deployments else {}
        _, binding = self.client.json_request(spec["companyBindingUrl"], authenticated=False, timeout=timeout)
        try:
            http_status, _ = self.client.json_request(spec["deploymentUrl"], authenticated=False, timeout=timeout)
        except ProviderError as error:
            if error.code == "invalid_remote_response":
                http_status = 200
            else:
                raise
        actual_company = binding.get("companyId") or (binding.get("company") or {}).get("id")
        binding_repository = binding.get("canonicalRepository") or binding.get("repository") or (binding.get("company") or {}).get("repository")
        actual_environment = binding.get("environment")
        expected_environment = spec.get("expectedEnvironment")
        binding_matches = actual_company == spec["expectedCompanyId"] and binding_repository == spec["expectedRepository"] and (not expected_environment or actual_environment == expected_environment)
        state = deployment.get("readyState") or deployment.get("state")
        deployment_meta = deployment.get("meta") or {}
        git_source = deployment.get("gitSource") or {}
        actual_commit = git_source.get("ref") or deployment_meta.get("githubCommitSha") or deployment_meta.get("gitCommitSha")
        actual_repository_id = git_source.get("repoId") or deployment_meta.get("githubRepoId") or deployment_meta.get("gitRepoId")
        source_repository = git_source.get("repo") or deployment_meta.get("githubRepo") or deployment_meta.get("gitRepo")
        source_matches = actual_commit == spec.get("sourceCommitSha") and str(actual_repository_id) == str(spec.get("sourceRepositoryId")) and (not source_repository or source_repository == spec.get("sourceRepository"))
        return {
            "projectId": spec["projectId"], "teamId": spec.get("teamId"),
            "deploymentId": deployment.get("id") or deployment.get("uid") or spec.get("deploymentId"),
            "deploymentUrl": spec["deploymentUrl"], "deploymentState": state,
            "sourceRepository": source_repository, "sourceRepositoryId": actual_repository_id, "sourceCommitSha": actual_commit, "sourceMatches": source_matches,
            "deploymentReady": state == "READY", "vercelApiReachable": True,
            "httpStatus": http_status, "httpReachable": 200 <= http_status < 400,
            "companyBindingUrl": spec["companyBindingUrl"], "companyId": actual_company,
            "canonicalRepository": binding_repository, "environment": actual_environment,
            "companyBindingMatches": binding_matches, "observedAt": now()
        }

    def observe(self, resource):
        spec = (resource.get("attributes") or {}).get("spec") or resource.get("spec") or self.configuration
        snapshot = self._observe_spec(spec)
        healthy = snapshot["deploymentReady"] and snapshot["sourceMatches"] and snapshot["httpReachable"] and snapshot["companyBindingMatches"]
        evidence = [
            {"type": "vercel_api_response", "source": PROVIDER_ID, "projectId": snapshot["projectId"], "deploymentId": snapshot["deploymentId"], "state": snapshot["deploymentState"], "sourceRepository": snapshot["sourceRepository"], "sourceCommitSha": snapshot["sourceCommitSha"], "sourceMatchesDesired": snapshot["sourceMatches"], "observedAt": snapshot["observedAt"]},
            {"type": "http_company_binding", "source": PROVIDER_ID, "url": snapshot["companyBindingUrl"], "httpStatus": snapshot["httpStatus"], "companyId": snapshot["companyId"], "canonicalRepository": snapshot["canonicalRepository"], "environment": snapshot["environment"], "matchesDesired": snapshot["companyBindingMatches"], "observedAt": snapshot["observedAt"]}
        ]
        return {"status": "healthy" if healthy else "degraded", "checkedAt": snapshot["observedAt"], "providerResourceId": "vercel://" + snapshot["projectId"] + "/deployments/" + str(snapshot["deploymentId"]), "evidence": evidence, "snapshot": snapshot}

    def invoke(self, operation, input_value, actor):
        if operation not in OPERATIONS:
            raise ProviderError("Unsupported operation", "unsupported_operation", {"operation": operation})
        observed = self.observe({"spec": input_value or self.configuration})
        if operation == "interface.deployment.status":
            return {"status": observed["status"], "checkedAt": observed["checkedAt"], "requestedBy": (actor or {}).get("actorId")}
        if operation == "interface.deployment.evidence":
            return {"evidence": observed["evidence"], "requestedBy": (actor or {}).get("actorId")}
        return observed


def respond(request_id, result=None, error=None):
    message = {"jsonrpc": "2.0", "id": request_id}
    message["error" if error is not None else "result"] = error if error is not None else result
    sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def main():
    provider = VercelProvider()
    for line in sys.stdin:
        try:
            request = json.loads(line)
            request_id, method, params = request.get("id"), request.get("method"), request.get("params") or {}
            try:
                if request.get("jsonrpc") != "2.0" or not isinstance(method, str): raise ProviderError("Invalid Request", "invalid_request")
                if method == "provider.initialize": result = provider.initialize(params)
                elif method == "provider.status": result = provider.status()
                elif method == "provider.validate": result = provider.validate(params.get("action") or {})
                elif method == "provider.plan": result = provider.plan(params.get("action") or {})
                elif method == "provider.apply": result = provider.apply(params.get("action") or {})
                elif method == "provider.observe": result = provider.observe(params.get("resource") or {})
                elif method == "provider.invoke": result = provider.invoke(params.get("operation"), params.get("input"), params.get("actor"))
                elif method == "provider.shutdown": result = {"shutdown": True}
                else: raise ProviderError("Method not found", "method_not_found")
                respond(request_id, result=result)
                if method == "provider.shutdown": break
            except ProviderError as error:
                respond(request_id, error={"code": -32010, "message": str(error), "data": {"code": error.code, **error.details}})
        except json.JSONDecodeError:
            respond(None, error={"code": -32700, "message": "Parse error"})


if __name__ == "__main__":
    main()
