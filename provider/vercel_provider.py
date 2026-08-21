#!/usr/bin/env python3
"""Vercel Provider for connector deployments and immutable Eve Agent runtimes."""

import datetime
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

PROTOCOL = "omniseed.provider.protocol/1.0"
PROVIDER_ID = "vercel"
VERSION = "0.2.0-alpha.6"
FAMILIES = ["agents", "connectors"]
METHODS = [
    "provider.initialize", "provider.status", "provider.validate", "provider.plan",
    "provider.apply", "provider.observe", "provider.invoke", "provider.shutdown"
]
OPERATIONS = [
    "interface.deployment.observe", "interface.deployment.status",
    "interface.deployment.evidence", "interface.deployment.promote", "agent.semantic_turn"
]


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

    def json_request(self, url, authenticated=False, timeout=10, token=None):
        return self.request(url, authenticated=authenticated, timeout=timeout, token=token)

    def request(self, url, authenticated=False, timeout=10, method="GET", body=None, token=None):
        headers = {"Accept": "application/json", "User-Agent": "omniseed-provider-vercel/0.2"}
        credential = token or (self.token if authenticated else None)
        if authenticated and not credential:
            raise ProviderError("Credentials are unavailable", "not_configured")
        if credential:
            headers["Authorization"] = "Bearer " + credential
        encoded = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            encoded = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(url, headers=headers, data=encoded, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                response_body = response.read().decode("utf-8")
                return response.status, json.loads(response_body) if response_body else {}
        except urllib.error.HTTPError as error:
            raise ProviderError("Remote endpoint returned an error", "remote_http_error", {
                "status": error.code, "host": urllib.parse.urlparse(url).hostname
            }) from error
        except (urllib.error.URLError, TimeoutError) as error:
            raise ProviderError("Remote endpoint is unreachable", "remote_unreachable", {
                "host": urllib.parse.urlparse(url).hostname
            }) from error
        except json.JSONDecodeError as error:
            raise ProviderError("Remote endpoint returned invalid JSON", "invalid_remote_response", {
                "host": urllib.parse.urlparse(url).hostname
            }) from error

    def text_request(self, url, authenticated=False, timeout=10, method="GET", body=None, token=None):
        headers = {"Accept": "application/x-ndjson, text/event-stream, text/plain", "User-Agent": "omniseed-provider-vercel/0.2"}
        credential = token or (self.token if authenticated else None)
        if authenticated and not credential:
            raise ProviderError("Credentials are unavailable", "not_configured")
        if credential:
            headers["Authorization"] = "Bearer " + credential
        encoded = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            encoded = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(url, headers=headers, data=encoded, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.status, response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            raise ProviderError("Remote endpoint returned an error", "remote_http_error", {
                "status": error.code, "host": urllib.parse.urlparse(url).hostname
            }) from error
        except (urllib.error.URLError, TimeoutError) as error:
            raise ProviderError("Remote endpoint is unreachable", "remote_unreachable", {
                "host": urllib.parse.urlparse(url).hostname
            }) from error


class EveClient:
    """Authenticated Eve protocol client created only from persisted resource state."""

    def __init__(self, network, runtime_url, token=None, timeout=30, health_path="/health", info_path="/info"):
        self.network = network
        self.runtime_url = runtime_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.health_path = health_path
        self.info_path = info_path

    def json(self, path, method="GET", body=None):
        _, value = self.network.request(
            self.runtime_url + path, authenticated=True, token=self.token,
            timeout=self.timeout, method=method, body=body
        )
        return value

    def health(self):
        return self.json(self.health_path)

    def info(self):
        return self.json(self.info_path)

    def turn(self, message):
        started = self.json("/eve/v1/session", "POST", {"message": message})
        session_id = started.get("sessionId")
        if not started.get("ok") or not session_id:
            raise ProviderError("Eve did not start a session", "invalid_remote_response")
        _, stream = self.network.text_request(
            f"{self.runtime_url}/eve/v1/session/{session_id}/stream",
            authenticated=True, token=self.token, timeout=self.timeout
        )
        answer, turn_id, completed = "", None, False
        for line in stream.splitlines():
            if not line.strip():
                continue
            if line.startswith("data:"):
                line = line[5:].strip()
            try:
                event = json.loads(line)
            except json.JSONDecodeError as error:
                raise ProviderError("Eve returned an invalid session event", "invalid_remote_response") from error
            turn_id = event.get("turnId", turn_id)
            if event.get("type") == "message.appended":
                answer += (event.get("data") or {}).get("messageDelta", "")
            completed = completed or event.get("type") == "message.completed"
        if not completed:
            raise ProviderError("Eve session ended without a completed message", "invalid_remote_response")
        return {"sessionId": session_id, "turnId": turn_id, "response": answer.strip()}


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
            "provider": {"id": PROVIDER_ID, "name": "Vercel", "version": VERSION},
            "primitiveFamilies": FAMILIES,
            "configurationSchema": "./provider-configuration.schema.json",
            "observationTypes": ["vercel_deployment_state", "company_binding_state", "eve_agent_runtime_state"],
            "evidenceTypes": ["vercel_api_response", "http_company_binding", "eve_agent_runtime_health", "eve_agent_semantic_turn"],
            "offerings": [
                {"family": "agents", "id": "semantic_agent_runtime", "products": ["eve", "functions", "ai_gateway"]},
                {"family": "connectors", "id": "deployment_runtime", "products": ["functions", "deployment_services"]}
            ],
            "operations": OPERATIONS, "methods": METHODS
        }

    @staticmethod
    def _repository(value):
        repository = str(value or "")
        repository = re.sub(r"^https://github\.com/", "", repository)
        return re.sub(r"\.git$", "", repository)

    @staticmethod
    def _endpoint_path(value, default):
        return urllib.parse.urlparse(value).path if value else default

    @staticmethod
    def _endpoint_origin(value):
        parsed = urllib.parse.urlparse(value or "")
        return f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme == "https" and parsed.netloc else None

    def _spec(self, action):
        raw = ((action or {}).get("desired") or {}).get("spec") or {}
        family = (action or {}).get("family")
        if raw.get("projectId"):
            spec = dict(raw)
            if family == "agents":
                spec.setdefault("agentImplementationRepository", spec.get("sourceRepository"))
                spec.setdefault("agentImplementationCommitSha", spec.get("sourceCommitSha"))
            return spec
        if family == "agents":
            runtime, implementation, bootstrap = raw.get("runtime") or {}, raw.get("implementation") or {}, raw.get("bootstrap") or {}
            source = runtime.get("source") or implementation
            endpoints = runtime.get("expectedEndpoints") or {}
            session = runtime.get("session") or {}
            return {
                "projectId": runtime.get("project"),
                "sourceRepository": self._repository(source.get("repository")),
                "sourceRepositoryId": source.get("repositoryId"),
                "sourceCommitSha": source.get("revision"),
                "agentImplementationRepository": self._repository(implementation.get("repository")),
                "agentImplementationCommitSha": implementation.get("revision"),
                "expectedCompanyId": bootstrap.get("company"),
                "expectedEnvironment": runtime.get("environment"),
                "agentIdentity": raw.get("organisationalIdentity") or bootstrap.get("identity"),
                "secretReferences": runtime.get("secretReferences") or [],
                "observationCredentialReference": runtime.get("observationCredentialReference"),
                "runtimeUrl": self._endpoint_origin(endpoints.get("health")),
                "healthPath": self._endpoint_path(endpoints.get("health"), "/health"),
                "infoPath": self._endpoint_path(endpoints.get("info"), "/info"),
                "operationEndpoint": bootstrap.get("omniseedEndpoint"),
                "operationCredentialReference": bootstrap.get("credentialReference"),
                "sessionCredentialReference": session.get("credentialReference"),
                "sessionIssuer": session.get("issuer", "omniseed"),
                "sessionAudience": session.get("audience", "omniseed-lily"),
                "target": runtime.get("target", "production"),
                "timeoutSeconds": runtime.get("timeoutSeconds", 10),
                "deploymentTimeoutSeconds": runtime.get("deploymentTimeoutSeconds", 300),
                "pollIntervalSeconds": runtime.get("pollIntervalSeconds", 2),
            }
        if family == "connectors":
            source, binding, endpoints, durable, access = raw.get("source") or {}, raw.get("companyBinding") or {}, raw.get("expectedEndpoints") or {}, raw.get("durableState") or {}, raw.get("access") or {}
            secret_references = list(dict.fromkeys([*(durable.get("credentialReferences") or []), *(raw.get("secretReferences") or [])]))
            return {
                "projectId": raw.get("project"),
                "sourceRepository": self._repository(source.get("repository")),
                "sourceRepositoryId": source.get("repositoryId"),
                "sourceCommitSha": source.get("revision"),
                "expectedCompanyId": binding.get("companyId"),
                "expectedRepository": self._repository(binding.get("repository")),
                "desiredRevision": self.configuration.get("desiredRevision") or binding.get("desiredRevision"),
                "companyDefinitionPath": binding.get("path", "omniform.yaml"),
                "stewardActorId": binding.get("stewardActorId"),
                "readOnlyInspection": binding.get("readOnlyInspection") is True,
                "publicStewardChat": access.get("stewardChat") == "public",
                "expectedEnvironment": raw.get("environment"),
                "stateEndpoint": durable.get("endpoint"),
                "secretReferences": secret_references,
                "companyBindingUrl": endpoints.get("company"),
                "companyBindingPath": self._endpoint_path(endpoints.get("company"), "/api/company"),
                "target": raw.get("target", "production"),
                "timeoutSeconds": raw.get("timeoutSeconds", 10),
                "deploymentTimeoutSeconds": raw.get("deploymentTimeoutSeconds", 300),
                "pollIntervalSeconds": raw.get("pollIntervalSeconds", 2),
            }
        return dict(raw)

    def _issues(self, action):
        raw = ((action or {}).get("desired") or {}).get("spec") or {}
        family, resource_id, spec = action.get("family"), action.get("resourceId"), self._spec(action)
        issues = []
        common = ["projectId", "sourceRepository", "sourceRepositoryId", "sourceCommitSha", "expectedCompanyId", "expectedEnvironment"]
        family_fields = {
            "agents": ["agentIdentity", "agentImplementationRepository", "agentImplementationCommitSha", "secretReferences", "observationCredentialReference", "healthPath", "infoPath", "operationEndpoint", "operationCredentialReference", "sessionCredentialReference", "sessionIssuer", "sessionAudience"],
            "connectors": ["expectedRepository", "desiredRevision", "companyDefinitionPath", "stewardActorId"]
        }
        if family == "connectors" and not spec.get("readOnlyInspection"):
            family_fields["connectors"].extend(["stateEndpoint", "secretReferences"])
        if family not in FAMILIES:
            issues.append({"code": "unsupported_family", "message": "Only agents and connectors are supported"})
        if not resource_id:
            issues.append({"code": "missing_field", "field": "resourceId", "message": "resourceId is required"})
        for field in common + family_fields.get(family, []):
            if spec.get(field) in (None, "", [], {}):
                issues.append({"code": "missing_field", "field": field, "message": field + " is required"})
        if spec.get("sourceCommitSha") and not re.fullmatch(r"[0-9a-f]{40}", str(spec["sourceCommitSha"])):
            issues.append({"code": "source_not_immutable", "field": "sourceCommitSha", "message": "sourceCommitSha must be a full 40-character commit SHA"})
        if spec.get("agentImplementationCommitSha") and not re.fullmatch(r"[0-9a-f]{40}", str(spec["agentImplementationCommitSha"])):
            issues.append({"code": "implementation_not_immutable", "field": "agentImplementationCommitSha", "message": "agentImplementationCommitSha must be a full 40-character commit SHA"})
        if spec.get("desiredRevision") and not re.fullmatch(r"[0-9a-f]{40}", str(spec["desiredRevision"])):
            issues.append({"code": "company_source_not_immutable", "field": "desiredRevision", "message": "desiredRevision must be a full 40-character commit SHA"})
        if spec.get("sourceRepositoryId") and (isinstance(spec["sourceRepositoryId"], bool) or not isinstance(spec["sourceRepositoryId"], int)):
            issues.append({"code": "invalid_repository_id", "field": "sourceRepositoryId", "message": "sourceRepositoryId must be the numeric Vercel Git integration ID"})
        if spec.get("expectedCompanyId") and self.company_id and spec["expectedCompanyId"] != self.company_id:
            issues.append({"code": "company_boundary", "message": "Action company does not match Provider context"})
        if family == "agents" and raw.get("projectId") and "runtimeUrl" in raw:
            issues.append({"code": "caller_runtime_forbidden", "field": "runtimeUrl", "message": "Runtime URL must come from Vercel deployment state"})
        if family == "agents" and spec.get("sessionCredentialReference") not in (spec.get("secretReferences") or []):
            issues.append({"code": "missing_secret_reference", "field": "sessionCredentialReference", "message": "The Eve session credential must be included in secretReferences"})
        return issues

    def status(self):
        configured = bool(self.client.token)
        connected = healthy = False
        if configured:
            try:
                status_project = self.configuration.get("statusProjectId")
                endpoint = (self._project_endpoint({"projectId": status_project})
                            if status_project else "https://api.vercel.com/v2/user")
                self.client.request(endpoint, authenticated=True)
                connected = healthy = True
            except ProviderError:
                pass
        return {"implementation_available": True, "configured": configured, "connected": connected, "healthy": healthy}

    def validate(self, action):
        issues = self._issues(action)
        return {"valid": not issues, "issues": issues}

    def _team_query(self, spec):
        team = spec.get("teamId") or self.configuration.get("teamId")
        return "?teamId=" + urllib.parse.quote(team, safe="") if team else ""

    def _project_endpoint(self, spec):
        return "https://api.vercel.com/v9/projects/" + urllib.parse.quote(spec["projectId"], safe="") + self._team_query(spec)

    def _project_state(self, spec):
        try:
            _, project = self.client.request(self._project_endpoint(spec), authenticated=True, timeout=spec.get("timeoutSeconds", 10))
            return "reuse", project
        except ProviderError as error:
            if error.code == "remote_http_error" and error.details.get("status") == 404:
                return "create", None
            raise

    def plan(self, action):
        validation, spec = self.validate(action), self._spec(action)
        secret_references = spec.get("secretReferences") or []
        if isinstance(secret_references, dict):
            secret_references = list(secret_references)
        project_change = "unknown"
        if validation["valid"]:
            project_change, _ = self._project_state(spec)
        return {
            "deterministic": True, "actionId": action.get("id"), "valid": validation["valid"],
            "issues": validation["issues"], "mode": "deploy_immutable_source", "mutationSupported": True,
            "family": action.get("family"), "resourceId": action.get("resourceId"),
            "project": {"id": spec.get("projectId"), "change": project_change},
            "source": {"repository": spec.get("sourceRepository"), "repositoryId": spec.get("sourceRepositoryId"), "commitSha": spec.get("sourceCommitSha")},
            "implementation": ({"repository": spec.get("agentImplementationRepository"), "commitSha": spec.get("agentImplementationCommitSha")} if action.get("family") == "agents" else None),
            "environmentBindings": self._environment_binding_names(spec, action.get("family")),
            "deploymentImpact": {"target": spec.get("target", "production"), "environment": spec.get("expectedEnvironment")},
            "expectedEvidence": ["vercel_api_response"] + (["eve_agent_runtime_health"] if action.get("family") == "agents" else ["http_company_binding"])
        }

    def _ensure_project(self, spec):
        state, project = self._project_state(spec)
        if state == "reuse":
            return state, project
        body = {"name": spec["projectId"], "gitRepository": {"type": "github", "repo": spec["sourceRepository"]}}
        endpoint = "https://api.vercel.com/v11/projects" + self._team_query(spec)
        _, project = self.client.request(endpoint, authenticated=True, timeout=spec.get("timeoutSeconds", 10), method="POST", body=body)
        return "create", project

    def _secret_value(self, reference):
        aliases = self.configuration.get("secretReferenceEnvironment") or {}
        environment_name = aliases.get(reference, reference)
        value = os.environ.get(environment_name)
        if not value:
            raise ProviderError("A declared secret reference is unavailable", "secret_unavailable", {"reference": reference})
        return value

    def _environment_binding_names(self, spec, family):
        secret_references = spec.get("secretReferences") or []
        if isinstance(secret_references, dict):
            secret_references = list(secret_references)
        public = self._public_environment(spec, family)
        return sorted([*public, *secret_references])

    def _public_environment(self, spec, family):
        if family == "connectors":
            repository = spec["expectedRepository"]
            revision = spec["desiredRevision"]
            path = str(spec.get("companyDefinitionPath") or "omniform.yaml").lstrip("/")
            definition_url = f"https://raw.githubusercontent.com/{repository}/{revision}/{urllib.parse.quote(path, safe='/')}"
            return {
                "OMNISEED_COMPANY_DEFINITION_URL": definition_url,
                "OMNISEED_DESIRED_REVISION": revision,
                "OMNISEED_ENVIRONMENT": spec["expectedEnvironment"],
                "OMNISEED_STATE_ENDPOINT": spec.get("stateEndpoint"),
                "OMNISEED_STEWARD_ACTOR_ID": spec["stewardActorId"],
                "OMNISEED_READ_ONLY_INSPECTION": "true" if spec.get("readOnlyInspection") else "false",
                "OMNISEED_PUBLIC_STEWARD_CHAT": "true" if spec.get("publicStewardChat") else "false",
            }
        if family != "agents":
            return {}
        return {
            "OMNISEED_COMPANY_REF": spec["expectedCompanyId"],
            "OMNISEED_AGENT_IDENTITY": spec["agentIdentity"],
            "OMNISEED_ENVIRONMENT": spec["expectedEnvironment"],
            "OMNISEED_SOURCE_REPOSITORY": spec.get("agentImplementationRepository") or spec["sourceRepository"],
            "OMNISEED_SOURCE_COMMIT_SHA": spec.get("agentImplementationCommitSha") or spec["sourceCommitSha"],
            "OMNISEED_OPERATION_ENDPOINT": spec["operationEndpoint"],
            "OMNISEED_OPERATION_CREDENTIAL_ENV": spec["operationCredentialReference"],
            "OMNISEED_SESSION_CREDENTIAL_ENV": spec["sessionCredentialReference"],
            "OMNISEED_SESSION_JWT_ISSUER": spec["sessionIssuer"],
            "OMNISEED_SESSION_JWT_AUDIENCE": spec["sessionAudience"],
        }
    def _environment_values(self, spec, family, existing_secret_references=None):
        target = spec.get("target", "production")
        public = self._public_environment(spec, family)
        values = [{"key": key, "value": value, "type": "encrypted", "target": [target]} for key, value in sorted(public.items()) if value is not None]
        secret_references = spec.get("secretReferences") or []
        if isinstance(secret_references, dict):
            secret_references = list(secret_references)
        preserved = set(existing_secret_references or [])
        for reference in sorted(secret_references):
            aliases = self.configuration.get("secretReferenceEnvironment") or {}
            supplied = os.environ.get(aliases.get(reference, reference))
            if supplied:
                values.append({"key": reference, "value": supplied, "type": "sensitive", "target": [target]})
            elif reference not in preserved:
                self._secret_value(reference)
        return values

    def _upsert_environment(self, spec, family):
        base = "https://api.vercel.com/v10/projects/" + urllib.parse.quote(spec["projectId"], safe="") + "/env"
        query = self._team_query(spec)
        _, response = self.client.request(base + query, authenticated=True, timeout=spec.get("timeoutSeconds", 10))
        items = response if isinstance(response, list) else response.get("envs", [])
        existing = {(item.get("key"), item.get("gitBranch")): item for item in items}
        target = spec.get("target", "production")
        existing_secrets = {item.get("key") for item in items if item.get("key") in (spec.get("secretReferences") or []) and target in (item.get("target") or [])}
        values = self._environment_values(spec, family, existing_secrets)
        for value in values:
            current = existing.get((value["key"], value.get("gitBranch")))
            if current and current.get("id"):
                endpoint = "https://api.vercel.com/v9/projects/" + urllib.parse.quote(spec["projectId"], safe="") + "/env/" + urllib.parse.quote(current["id"], safe="") + query
                patch = {key: item for key, item in value.items() if key != "key"} if current.get("type") == "sensitive" else value
                self.client.request(endpoint, authenticated=True, timeout=spec.get("timeoutSeconds", 10), method="PATCH", body=patch)
            else:
                self.client.request(base + query, authenticated=True, timeout=spec.get("timeoutSeconds", 10), method="POST", body=value)

    def apply(self, action):
        validation = self.validate(action)
        if not validation["valid"]:
            raise ProviderError("Action is invalid", "invalid_action", {"issues": validation["issues"]})
        spec, family = self._spec(action), action["family"]
        project_change, _ = self._ensure_project(spec)
        self._upsert_environment(spec, family)
        body = {
            "name": spec["projectId"], "project": spec["projectId"], "target": spec.get("target", "production"),
            "gitSource": {"type": "github", "repoId": spec["sourceRepositoryId"], "ref": spec["sourceCommitSha"], "sha": spec["sourceCommitSha"]},
            "meta": {key: value for key, value in {
                "omniseedFamily": family, "omniseedResourceId": action["resourceId"],
                "omniseedCompanyId": spec["expectedCompanyId"], "omniseedAgentIdentity": spec.get("agentIdentity"),
                "omniseedSourceRepository": spec["sourceRepository"], "omniseedSourceCommit": spec["sourceCommitSha"]
            }.items() if value is not None}
        }
        if family == "agents":
            body["projectSettings"] = {"framework": "eve", "buildCommand": "npm run build:runtime", "outputDirectory": ".output", "nodeVersion": "24.x"}
        endpoint = "https://api.vercel.com/v13/deployments" + self._team_query(spec)
        _, deployment = self.client.request(endpoint, authenticated=True, timeout=spec.get("timeoutSeconds", 10), method="POST", body=body)
        deployment_id, host = deployment.get("id") or deployment.get("uid"), deployment.get("url")
        if not deployment_id or not host:
            raise ProviderError("Vercel did not return a deployment identity", "invalid_remote_response", {"host": "api.vercel.com"})
        deployment_url = host if host.startswith("https://") else "https://" + host
        applied_spec = {**spec, "deploymentId": deployment_id, "deploymentUrl": deployment_url}
        self._wait_for_deployment(applied_spec)
        if family == "connectors":
            applied_spec["companyBindingUrl"] = spec.get("companyBindingUrl") or deployment_url.rstrip("/") + spec.get("companyBindingPath", "/api/company")
        return {
            "providerResourceId": f"vercel://{spec['projectId']}/deployments/{deployment_id}", "status": "submitted",
            "attributes": {"family": family, "resourceId": action["resourceId"], "spec": applied_spec, "projectChange": project_change, "submittedAt": now()}
        }

    def _deployment_endpoint(self, spec):
        path = "/v13/deployments/" + urllib.parse.quote(spec["deploymentId"], safe="")
        return "https://api.vercel.com" + path + self._team_query(spec)

    def _deployment_snapshot(self, spec):
        if not spec.get("deploymentId") or not spec.get("deploymentUrl"):
            raise ProviderError("Observation requires persisted deployment state returned by apply", "observation_target_missing")
        _, deployment = self.client.json_request(self._deployment_endpoint(spec), authenticated=True, timeout=spec.get("timeoutSeconds", 10))
        meta, git_source = deployment.get("meta") or {}, deployment.get("gitSource") or {}
        actual_commit = git_source.get("ref") or meta.get("githubCommitSha") or meta.get("gitCommitSha")
        actual_repository_id = git_source.get("repoId") or meta.get("githubRepoId") or meta.get("gitRepoId")
        actual_repository = meta.get("omniseedSourceRepository") or git_source.get("repo") or meta.get("githubRepo") or meta.get("gitRepo")
        source_matches = actual_commit == spec.get("sourceCommitSha") and str(actual_repository_id) == str(spec.get("sourceRepositoryId")) and (not actual_repository or actual_repository == spec.get("sourceRepository"))
        return deployment, {"sourceRepository": actual_repository, "sourceRepositoryId": actual_repository_id, "sourceCommitSha": actual_commit, "sourceMatches": source_matches}

    def _wait_for_deployment(self, spec):
        deadline = time.monotonic() + max(1, int(spec.get("deploymentTimeoutSeconds", 300)))
        while True:
            deployment, source = self._deployment_snapshot(spec)
            state = deployment.get("readyState") or deployment.get("state")
            if state == "READY":
                if not source["sourceMatches"]:
                    raise ProviderError("Ready deployment does not match the approved immutable source", "deployment_source_mismatch")
                return deployment
            if state in {"ERROR", "CANCELED"}:
                raise ProviderError("Vercel deployment did not become ready", "deployment_failed", {"state": state})
            if time.monotonic() >= deadline:
                raise ProviderError("Timed out waiting for Vercel deployment readiness", "deployment_timeout", {"state": state})
            time.sleep(max(0.1, float(spec.get("pollIntervalSeconds", 2))))

    def _runtime_token(self, spec):
        name = spec.get("observationCredentialReference") or self.configuration.get("runtimeAuthTokenEnv")
        return os.environ.get(name) if name else None

    def _eve(self, spec):
        return EveClient(self.client, spec.get("runtimeUrl") or spec["deploymentUrl"], self._runtime_token(spec), spec.get("timeoutSeconds", 10), spec.get("healthPath", "/health"), spec.get("infoPath", "/info"))

    def observe(self, resource):
        attributes = resource.get("attributes") or {}
        spec, family = attributes.get("spec") or resource.get("spec") or {}, attributes.get("family") or resource.get("family")
        deployment, source = self._deployment_snapshot(spec)
        checked_at, state = now(), deployment.get("readyState") or deployment.get("state")
        deployment_ok = state == "READY" and source["sourceMatches"]
        evidence = [{"type": "vercel_api_response", "source": PROVIDER_ID, "projectId": spec["projectId"], "deploymentId": spec["deploymentId"], "state": state, **source, "observedAt": checked_at}]
        snapshot = {"projectId": spec["projectId"], "deploymentId": spec["deploymentId"], "deploymentUrl": spec["deploymentUrl"], "deploymentState": state, "deploymentReady": state == "READY", **source}
        if family == "agents":
            health, info = self._eve(spec).health(), self._eve(spec).info()
            runtime_company = info.get("companyRef") or (info.get("company") or {}).get("id")
            runtime_identity = info.get("agentIdentity") or (info.get("agent") or {}).get("identity")
            runtime_environment = info.get("environment")
            runtime_source = info.get("source") or {}
            implementation_repository = spec.get("agentImplementationRepository") or spec["sourceRepository"]
            implementation_commit = spec.get("agentImplementationCommitSha") or spec["sourceCommitSha"]
            runtime_matches = runtime_company == spec["expectedCompanyId"] and runtime_identity == spec["agentIdentity"] and runtime_environment == spec["expectedEnvironment"] and runtime_source.get("repository") == implementation_repository and runtime_source.get("commitSha") == implementation_commit
            healthy = deployment_ok and health.get("ok") is True and runtime_matches
            snapshot.update({"health": health, "runtime": info, "runtimeIdentityMatches": runtime_matches})
            evidence.append({"type": "eve_agent_runtime_health", "source": PROVIDER_ID, "product": "eve", "deploymentId": spec["deploymentId"], "health": health, "runtime": info, "matchesDesired": runtime_matches, "observedAt": checked_at})
        elif family == "connectors":
            _, binding = self.client.json_request(spec["companyBindingUrl"], timeout=spec.get("timeoutSeconds", 10))
            instance = binding.get("instance") or {}
            actual_company = binding.get("companyId") or (binding.get("company") or {}).get("id") or instance.get("companyId")
            repository = binding.get("canonicalRepository") or binding.get("repository") or (binding.get("company") or {}).get("repository") or (instance.get("desiredState") or {}).get("repository")
            repository = self._repository(repository)
            environment = binding.get("environment") or instance.get("environment")
            desired_revision = binding.get("desiredRevision") or instance.get("desiredRevision")
            expected_environment = f"{spec['expectedEnvironment']}-read-only-inspection" if spec.get("readOnlyInspection") else spec["expectedEnvironment"]
            binding_matches = actual_company == spec["expectedCompanyId"] and repository == spec["expectedRepository"] and environment == expected_environment and desired_revision == spec.get("desiredRevision")
            healthy = deployment_ok and binding_matches
            snapshot.update({"companyId": actual_company, "canonicalRepository": repository, "desiredRevision": desired_revision, "environment": environment, "companyBindingMatches": binding_matches})
            evidence.append({"type": "http_company_binding", "source": PROVIDER_ID, "url": spec["companyBindingUrl"], "companyId": actual_company, "canonicalRepository": repository, "desiredRevision": desired_revision, "environment": environment, "matchesDesired": binding_matches, "observedAt": checked_at})
        else:
            raise ProviderError("Persisted resource family is unsupported", "unsupported_family", {"family": family})
        return {"status": "healthy" if healthy else "degraded", "checkedAt": checked_at, "providerResourceId": f"vercel://{spec['projectId']}/deployments/{spec['deploymentId']}", "evidence": evidence, "snapshot": snapshot}

    def invoke(self, operation, input_value, actor):
        if operation not in OPERATIONS:
            raise ProviderError("Unsupported operation", "unsupported_operation", {"operation": operation})
        if operation == "agent.semantic_turn":
            value = input_value or {}
            if "runtimeUrl" in value:
                raise ProviderError("Caller-supplied runtime URLs are forbidden", "caller_runtime_forbidden")
            binding = value.get("resourceBinding")
            if not binding or (binding.get("attributes") or {}).get("family") != "agents":
                raise ProviderError("Agent invocation requires an Engine resource binding", "resource_binding_required")
            spec = binding["attributes"]["spec"]
            if (actor or {}).get("actorId") != spec.get("agentIdentity"):
                raise ProviderError("Actor does not match the deployed organisational identity", "identity_mismatch")
            message = value.get("message")
            if not isinstance(message, str) or not message.strip():
                raise ProviderError("A non-empty message is required", "invalid_input")
            result = self._eve(spec).turn(message)
            return {**result, "evidence": {"type": "eve_agent_semantic_turn", "source": PROVIDER_ID, "product": "eve", "deploymentId": spec["deploymentId"], "sessionId": result["sessionId"], "turnId": result["turnId"], "observedAt": now()}}
        if operation == "interface.deployment.promote":
            binding = (input_value or {}).get("resourceBinding")
            attributes = (binding or {}).get("attributes") or {}
            if attributes.get("family") != "connectors":
                raise ProviderError("Production promotion requires a persisted connector binding", "resource_binding_required")
            spec = attributes.get("spec") or {}
            deployment, source = self._deployment_snapshot(spec)
            state = deployment.get("readyState") or deployment.get("state")
            if spec.get("target") != "production" or state != "READY" or not source["sourceMatches"]:
                raise ProviderError("Only the ready deployment matching approved source may be promoted", "promotion_precondition_failed", {"state": state, "sourceMatches": source["sourceMatches"]})
            _, project = self._project_state(spec)
            project_id = (project or {}).get("id")
            if not project_id:
                raise ProviderError("Vercel project identity is unavailable", "promotion_precondition_failed")
            path = "/v10/projects/" + urllib.parse.quote(project_id, safe="") + "/promote/" + urllib.parse.quote(spec["deploymentId"], safe="")
            self.client.request("https://api.vercel.com" + path + self._team_query(spec), authenticated=True, timeout=spec.get("timeoutSeconds", 10), method="POST", body={})
            promoted_at = now()
            return {"status": "promoted", "providerResourceId": f"vercel://{spec['projectId']}/deployments/{spec['deploymentId']}", "evidence": {"type": "vercel_api_response", "source": PROVIDER_ID, "action": "production_promotion", "projectId": spec["projectId"], "deploymentId": spec["deploymentId"], "sourceCommitSha": spec["sourceCommitSha"], "requestedBy": (actor or {}).get("actorId"), "observedAt": promoted_at}}
        binding = (input_value or {}).get("resourceBinding") or {"spec": input_value or {}}
        observed = self.observe(binding)
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
