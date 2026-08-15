#!/usr/bin/env python3
"""Narrow EVE Provider for OmniSeed Provider Protocol v1."""

import datetime
import json
import os
import sys
import urllib.error
import urllib.request

PROTOCOL = "omniseed.provider.protocol/1.0"
METHODS = [
    "provider.initialize", "provider.status", "provider.validate", "provider.plan",
    "provider.apply", "provider.observe", "provider.invoke", "provider.shutdown"
]
OPERATIONS = ["agent.semantic_turn"]


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


class EveError(RuntimeError):
    def __init__(self, message, details=None):
        super().__init__(message)
        self.details = details or {}


class EveClient:
    def __init__(self, runtime_url, token=None):
        self.runtime_url = runtime_url.rstrip("/")
        self.token = token

    def request(self, path, method="GET", body=None):
        headers = {"accept": "application/json"}
        if body is not None:
            headers["content-type"] = "application/json"
        if self.token:
            headers["authorization"] = "Bearer " + self.token
        request = urllib.request.Request(
            self.runtime_url + path,
            data=None if body is None else json.dumps(body).encode(),
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read().decode(), response.headers.get("content-type", "")
        except (urllib.error.URLError, TimeoutError) as error:
            raise EveError("EVE runtime request failed", {"path": path, "diagnostic": str(error)}) from error

    def json(self, path, method="GET", body=None):
        text, _ = self.request(path, method, body)
        try:
            return json.loads(text)
        except json.JSONDecodeError as error:
            raise EveError("EVE runtime returned invalid JSON", {"path": path}) from error

    def health(self):
        return self.json("/eve/v1/health")

    def info(self):
        return self.json("/eve/v1/info")

    def turn(self, message):
        started = self.json("/eve/v1/session", "POST", {"message": message})
        session_id = started.get("sessionId")
        if not started.get("ok") or not session_id:
            raise EveError("EVE did not start a session", {"response": started})
        text, _ = self.request(f"/eve/v1/session/{session_id}/stream")
        answer = ""
        completed = False
        turn_id = None
        for line in text.splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            turn_id = event.get("turnId", turn_id)
            if event.get("type") == "message.appended":
                answer += (event.get("data") or {}).get("messageDelta", "")
            if event.get("type") == "message.completed":
                completed = True
        if not completed:
            raise EveError("EVE session stream ended without a completed message", {"sessionId": session_id})
        return {"sessionId": session_id, "turnId": turn_id, "response": answer.strip()}


class EveProvider:
    def __init__(self, configuration=None, client=None):
        self.configuration = configuration or {}
        self.client = client
        self.company_id = None

    def _client(self):
        if self.client:
            return self.client
        token_env = self.configuration.get("authTokenEnv")
        return EveClient(self.configuration.get("runtimeUrl", ""), os.environ.get(token_env, "") if token_env else None)

    def initialize(self, params):
        if params.get("protocolVersion") != PROTOCOL:
            raise EveError("Unsupported protocol version", {"requested": params.get("protocolVersion"), "supported": PROTOCOL})
        self.configuration = params.get("configuration") or {}
        self.company_id = (params.get("context") or {}).get("companyId")
        return {
            "protocolVersion": PROTOCOL,
            "provider": {"id": "eve_agent_runtime", "name": "EVE Agent Runtime Provider", "version": "0.1.0-alpha.0"},
            "primitiveFamilies": ["agents"],
            "configurationSchema": "./provider-configuration.schema.json",
            "observationTypes": ["eve_agent_runtime_state"],
            "evidenceTypes": ["eve_agent_runtime_health", "eve_agent_semantic_turn"],
            "offerings": [{"family": "agents", "id": "semantic_agent_runtime", "resource": {
                "family": "agents", "id": self.configuration.get("resourceId", "agent"),
                "name": "EVE semantic agent runtime", "offers": self.configuration.get("offers", []), "risk": "high",
                "spec": {"companyRef": self.configuration.get("companyRef"), "agentIdentity": self.configuration.get("agentIdentity"), "runtimeUrl": self.configuration.get("runtimeUrl")}
            }}],
            "operations": OPERATIONS,
            "methods": METHODS,
        }

    def validate(self, action=None):
        issues = []
        for field in ("runtimeUrl", "companyRef", "agentIdentity", "resourceId", "offers", "authTokenEnv"):
            if not self.configuration.get(field):
                issues.append({"code": "missing_field", "field": field, "message": f"{field} is required"})
        if self.configuration.get("runtimeUrl") and not self.configuration["runtimeUrl"].startswith("https://"):
            issues.append({"code": "insecure_runtime", "field": "runtimeUrl", "message": "Production EVE runtime must use HTTPS"})
        if self.company_id and self.configuration.get("companyRef") != self.company_id:
            issues.append({"code": "company_mismatch", "message": "Provider company reference does not match engine context"})
        return {"valid": not issues, "issues": issues}

    def status(self):
        configured = self.validate()["valid"]
        connected = healthy = False
        runtime = None
        if configured and os.environ.get(self.configuration["authTokenEnv"]):
            try:
                health = self._client().health()
                runtime = self._client().info()
                connected = True
                healthy = health.get("ok") is True
            except EveError:
                pass
        return {"implementation_available": True, "configured": configured, "connected": connected, "healthy": healthy, "runtime": runtime}

    def plan(self, action):
        return {"deterministic": True, "actionId": (action or {}).get("id"), "effect": "bind_existing_eve_runtime"}

    def apply(self, action):
        validation = self.validate(action)
        if not validation["valid"]:
            raise EveError("EVE runtime binding is invalid", {"issues": validation["issues"]})
        observation = self.observe({"providerResourceId": self.configuration["runtimeUrl"]})
        if observation["status"] != "healthy":
            raise EveError("EVE runtime is not healthy", {"observation": observation})
        return {"providerResourceId": self.configuration["runtimeUrl"], "status": "bound", "attributes": {
            "companyRef": self.configuration["companyRef"], "agentIdentity": self.configuration["agentIdentity"], "boundAt": now()
        }}

    def observe(self, resource):
        checked_at = now()
        try:
            health = self._client().health()
            info = self._client().info()
            healthy = health.get("ok") is True
            evidence = {"type": "eve_agent_runtime_health", "source": "eve_agent_runtime", "runtimeUrl": self.configuration.get("runtimeUrl"), "health": health, "runtime": info, "observedAt": checked_at}
            return {"status": "healthy" if healthy else "degraded", "checkedAt": checked_at, "providerResourceId": (resource or {}).get("providerResourceId"), "evidence": [evidence], "snapshot": {"health": health, "runtime": info}}
        except EveError as error:
            return {"status": "unavailable", "checkedAt": checked_at, "providerResourceId": (resource or {}).get("providerResourceId"), "evidence": [], "error": str(error)}

    def invoke(self, operation, input_value, actor):
        if operation not in OPERATIONS:
            raise EveError("Unsupported agent operation", {"operation": operation})
        if (actor or {}).get("actorId") != self.configuration.get("agentIdentity"):
            raise EveError("Actor does not match the configured organisational identity")
        message = (input_value or {}).get("message")
        if not isinstance(message, str) or not message.strip():
            raise EveError("A non-empty message is required")
        result = self._client().turn(message)
        return {**result, "evidence": {"type": "eve_agent_semantic_turn", "source": "eve_agent_runtime", "sessionId": result["sessionId"], "turnId": result["turnId"], "observedAt": now()}}


def respond(request_id, result=None, error=None):
    message = {"jsonrpc": "2.0", "id": request_id}
    message["error" if error is not None else "result"] = error if error is not None else result
    sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def main():
    provider = EveProvider()
    for line in sys.stdin:
        try:
            request = json.loads(line)
            method = request.get("method")
            params = request.get("params") or {}
            handlers = {
                "provider.initialize": lambda: provider.initialize(params),
                "provider.status": provider.status,
                "provider.validate": lambda: provider.validate(params.get("action")),
                "provider.plan": lambda: provider.plan(params.get("action")),
                "provider.apply": lambda: provider.apply(params.get("action")),
                "provider.observe": lambda: provider.observe(params.get("resource")),
                "provider.invoke": lambda: provider.invoke(params.get("operation"), params.get("input"), params.get("actor")),
                "provider.shutdown": lambda: {"stopped": True},
            }
            if request.get("jsonrpc") != "2.0" or method not in handlers:
                respond(request.get("id"), error={"code": -32601, "message": "Method not found"})
            else:
                respond(request.get("id"), result=handlers[method]())
        except (EveError, ValueError) as error:
            respond(request.get("id") if "request" in locals() else None, error={"code": -32000, "message": str(error), "data": getattr(error, "details", {})})


if __name__ == "__main__":
    main()
