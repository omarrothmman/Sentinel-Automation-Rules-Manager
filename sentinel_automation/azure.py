from __future__ import annotations

import json
import random
import time
from typing import Any
from urllib.parse import quote, urlparse

from .errors import AzureRequestError, ConcurrentChangeError
from .inventory import Workspace

ARM_ENDPOINT = "https://management.azure.com"
DEFAULT_API_VERSION = "2025-09-01"
RESOURCE_GRAPH_API_VERSION = "2024-04-01"


class ArmClient:
    def __init__(
        self,
        credential: Any,
        api_version: str = DEFAULT_API_VERSION,
        timeout_seconds: int = 60,
        max_retries: int = 5,
    ) -> None:
        try:
            import requests
        except ImportError as exc:
            raise AzureRequestError(
                "HTTP dependency is missing. Run: py -m pip install -e ."
            ) from exc
        self._requests = requests
        self._session = requests.Session()
        self._credential = credential
        self.api_version = api_version
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries

    def close(self) -> None:
        self._session.close()
        close = getattr(self._credential, "close", None)
        if callable(close):
            close()

    def authenticate(self) -> None:
        self._access_token()

    def _access_token(self) -> str:
        try:
            return self._credential.get_token("https://management.azure.com/.default").token
        except Exception as exc:
            raise AzureRequestError(f"Azure authentication failed: {exc}") from exc

    @staticmethod
    def _collection_url(workspace: Workspace) -> str:
        parts = (
            "subscriptions",
            workspace.subscription_id,
            "resourceGroups",
            workspace.resource_group,
            "providers",
            "Microsoft.OperationalInsights",
            "workspaces",
            workspace.workspace_name,
            "providers",
            "Microsoft.SecurityInsights",
            "automationRules",
        )
        return ARM_ENDPOINT + "/" + "/".join(quote(part, safe="") for part in parts)

    @classmethod
    def _resource_url(cls, workspace: Workspace, rule_id: str) -> str:
        return cls._collection_url(workspace) + "/" + quote(rule_id, safe="")

    @staticmethod
    def _workspace_provider_url(workspace: Workspace, resource_path: str) -> str:
        parts = (
            "subscriptions",
            workspace.subscription_id,
            "resourceGroups",
            workspace.resource_group,
            "providers",
            "Microsoft.OperationalInsights",
            "workspaces",
            workspace.workspace_name,
            "providers",
            "Microsoft.SecurityInsights",
        )
        base = ARM_ENDPOINT + "/" + "/".join(quote(part, safe="") for part in parts)
        return base + "/" + resource_path

    def _request(
        self,
        method: str,
        url: str,
        *,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        allow_not_found: bool = False,
    ) -> dict[str, Any] | None:
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.netloc.casefold() != "management.azure.com":
            raise AzureRequestError(f"Refusing unexpected Azure pagination URL: {url}")

        request_headers = {"Accept": "application/json"}
        if headers:
            request_headers.update(headers)
        last_error: str | None = None
        for attempt in range(self.max_retries + 1):
            request_headers["Authorization"] = f"Bearer {self._access_token()}"
            try:
                response = self._session.request(
                    method,
                    url,
                    params=None if "api-version=" in url else {"api-version": self.api_version},
                    headers=request_headers,
                    json=body,
                    timeout=(10, self.timeout_seconds),
                )
            except self._requests.RequestException as exc:
                last_error = str(exc)
                if attempt >= self.max_retries:
                    break
                self._sleep(attempt, None)
                continue

            if response.status_code == 404 and allow_not_found:
                return None
            if response.status_code == 412:
                raise ConcurrentChangeError(
                    "Azure rejected the update because the rule changed concurrently"
                )
            if response.status_code in (408, 429) or 500 <= response.status_code <= 599:
                last_error = self._response_error(response)
                if attempt < self.max_retries:
                    self._sleep(attempt, response.headers.get("Retry-After"))
                    continue
            if not 200 <= response.status_code <= 299:
                raise AzureRequestError(self._response_error(response))
            if response.status_code == 204 or not response.content:
                return {}
            try:
                result = response.json()
            except ValueError as exc:
                raise AzureRequestError(
                    f"Azure returned non-JSON content for {method} {parsed.path}"
                ) from exc
            if not isinstance(result, dict):
                raise AzureRequestError(
                    f"Azure returned an unexpected response for {method} {parsed.path}"
                )
            return result
        raise AzureRequestError(
            f"Azure request failed after retries: {last_error or 'unknown error'}"
        )

    @staticmethod
    def _response_error(response: Any) -> str:
        request_id = response.headers.get("x-ms-request-id") or response.headers.get(
            "x-ms-correlation-request-id"
        )
        try:
            payload = response.json()
            error = payload.get("error", payload) if isinstance(payload, dict) else payload
            detail = json.dumps(error, ensure_ascii=False)
        except ValueError:
            detail = response.text[:1000]
        suffix = f"; request-id={request_id}" if request_id else ""
        return f"Azure HTTP {response.status_code}: {detail}{suffix}"

    @staticmethod
    def _sleep(attempt: int, retry_after: str | None) -> None:
        try:
            delay = min(float(retry_after), 60.0) if retry_after else 0.0
        except ValueError:
            delay = 0.0
        if delay <= 0:
            delay = min(2**attempt + random.random(), 30.0)
        time.sleep(delay)

    def list_rules(self, workspace: Workspace) -> list[dict[str, Any]]:
        url = self._collection_url(workspace)
        rules: list[dict[str, Any]] = []
        visited: set[str] = set()
        while url:
            if url in visited:
                raise AzureRequestError("Azure list response contains a pagination loop")
            visited.add(url)
            result = self._request("GET", url)
            assert result is not None
            values = result.get("value", [])
            if not isinstance(values, list):
                raise AzureRequestError(
                    "Azure list response does not contain a valid 'value' array"
                )
            rules.extend(item for item in values if isinstance(item, dict))
            next_link = result.get("nextLink")
            if next_link is not None and not isinstance(next_link, str):
                raise AzureRequestError("Azure list response contains an invalid nextLink")
            url = next_link
        return rules

    def resource_graph_query(
        self, query: str, subscriptions: list[str] | None = None
    ) -> list[dict[str, Any]]:
        """Run a paginated tenant- or subscription-scope Azure Resource Graph query."""
        url = (
            f"{ARM_ENDPOINT}/providers/Microsoft.ResourceGraph/resources"
            f"?api-version={RESOURCE_GRAPH_API_VERSION}"
        )
        rows: list[dict[str, Any]] = []
        skip_token: str | None = None
        seen_tokens: set[str] = set()
        while True:
            options: dict[str, Any] = {
                "$top": 1000,
                "allowPartialScopes": True,
                "resultFormat": "objectArray",
            }
            if skip_token:
                options["$skipToken"] = skip_token
            body: dict[str, Any] = {"query": query, "options": options}
            if subscriptions:
                body["subscriptions"] = subscriptions
            result = self._request(
                "POST", url, body=body, headers={"Content-Type": "application/json"}
            )
            assert result is not None
            data = result.get("data")
            if not isinstance(data, list):
                raise AzureRequestError("Azure Resource Graph response has no object-array data")
            rows.extend(item for item in data if isinstance(item, dict))
            next_token = result.get("$skipToken")
            if next_token is None:
                break
            if not isinstance(next_token, str) or not next_token:
                raise AzureRequestError("Azure Resource Graph returned an invalid skip token")
            if next_token in seen_tokens:
                raise AzureRequestError("Azure Resource Graph returned a pagination loop")
            seen_tokens.add(next_token)
            skip_token = next_token
        return rows

    def is_sentinel_workspace(self, workspace: Workspace) -> bool:
        url = self._workspace_provider_url(workspace, "onboardingStates/default")
        result = self._request("GET", url, allow_not_found=True)
        return result is not None

    def get_rule(self, workspace: Workspace, rule_id: str) -> dict[str, Any] | None:
        return self._request("GET", self._resource_url(workspace, rule_id), allow_not_found=True)

    def put_rule(
        self,
        workspace: Workspace,
        rule_id: str,
        body: dict[str, Any],
        etag: str | None,
    ) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if etag:
            headers["If-Match"] = etag
        result = self._request(
            "PUT", self._resource_url(workspace, rule_id), body=body, headers=headers
        )
        assert result is not None
        return result

    def delete_rule(self, workspace: Workspace, rule_id: str, etag: str | None = None) -> None:
        headers = {"If-Match": etag} if etag else None
        self._request("DELETE", self._resource_url(workspace, rule_id), headers=headers)
