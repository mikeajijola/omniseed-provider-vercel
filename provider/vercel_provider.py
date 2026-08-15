#!/usr/bin/env python3
"""Narrow, read-only Vercel connector Provider for OmniSeed Protocol v1."""

import datetime
import json
import os
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
        headers = {"Accept": "application/json", "User-Agent": "omniseed-provider-vercel/0.1"}
        if authenticated:
            if not self.token:
                raise ProviderError("Vercel credentials are unavailable", "not_configured")
            headers["Authorization"] = "Bearer " + self.token
        request = urllib.request.Request(url, headers=headers)
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
        for field in ["projectId", "deploymentUrl", "companyBindingUrl", "expectedCompanyId", "expectedRepository"]:
            if not spec.get(field):
                issues.append({"code": "missing_field", "field": field, "message": field + " is required"})
        if action.get("family") != FAMILY or action.get("resourceId") != RESOURCE_ID:
            issues.append({"code": "unsupported_action", "message": "Only the connectors/omniseed_os resource is supported"})
        if spec.get("expectedCompanyId") and self.company_id and spec["expectedCompanyId"] != self.company_id:
            issues.append({"code": "company_boundary", "message": "Action company does not match Provider context"})
        return issues

    def status(self):
        required = ["projectId", "deploymentUrl", "companyBindingUrl", "expectedCompanyId", "expectedRepository"]
        configured = all(self.configuration.get(key) for key in required) and bool(self.client.token)
        connected = healthy = False
        if configured:
            try:
                snapshot = self._observe_spec(self.configuration)
                connected = snapshot["vercelApiReachable"]
                healthy = snapshot["deploymentReady"] and snapshot["httpReachable"] and snapshot["companyBindingMatches"]
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
            "mode": "observe_existing",
            "mutationSupported": False
        }

    def apply(self, action):
        validation = self.validate(action)
        if not validation["valid"]:
            raise ProviderError("Action is invalid", "invalid_action", {"issues": validation["issues"]})
        raise ProviderError(
            "Vercel deployment mutation is unsupported: no approved immutable artifact or source deployment contract was supplied",
            "mutation_unsupported",
            {"mode": "read_only", "resourceId": RESOURCE_ID}
        )

    def _deployment_endpoint(self, spec):
        team = spec.get("teamId")
        if spec.get("deploymentId"):
            path = "/v13/deployments/" + urllib.parse.quote(spec["deploymentId"], safe="")
        else:
            path = "/v6/deployments?projectId=" + urllib.parse.quote(spec["projectId"], safe="") + "&limit=1"
        return "https://api.vercel.com" + path + (("?" if "?" not in path else "&") + "teamId=" + urllib.parse.quote(team, safe="") if team else "")

    def _observe_spec(self, spec):
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
        actual_repository = binding.get("canonicalRepository") or binding.get("repository") or (binding.get("company") or {}).get("repository")
        actual_environment = binding.get("environment")
        expected_environment = spec.get("expectedEnvironment")
        binding_matches = actual_company == spec["expectedCompanyId"] and actual_repository == spec["expectedRepository"] and (not expected_environment or actual_environment == expected_environment)
        state = deployment.get("readyState") or deployment.get("state")
        return {
            "projectId": spec["projectId"], "teamId": spec.get("teamId"),
            "deploymentId": deployment.get("id") or deployment.get("uid") or spec.get("deploymentId"),
            "deploymentUrl": spec["deploymentUrl"], "deploymentState": state,
            "deploymentReady": state == "READY", "vercelApiReachable": True,
            "httpStatus": http_status, "httpReachable": 200 <= http_status < 400,
            "companyBindingUrl": spec["companyBindingUrl"], "companyId": actual_company,
            "canonicalRepository": actual_repository, "environment": actual_environment,
            "companyBindingMatches": binding_matches, "observedAt": now()
        }

    def observe(self, resource):
        spec = (resource.get("attributes") or {}).get("spec") or resource.get("spec") or self.configuration
        snapshot = self._observe_spec(spec)
        healthy = snapshot["deploymentReady"] and snapshot["httpReachable"] and snapshot["companyBindingMatches"]
        evidence = [
            {"type": "vercel_api_response", "source": PROVIDER_ID, "projectId": snapshot["projectId"], "deploymentId": snapshot["deploymentId"], "state": snapshot["deploymentState"], "observedAt": snapshot["observedAt"]},
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
