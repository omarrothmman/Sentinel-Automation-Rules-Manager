import unittest
from types import SimpleNamespace
from typing import Any

from sentinel_automation.azure import ArmClient
from sentinel_automation.errors import ConcurrentChangeError

from .helpers import workspace


class FakeCredential:
    def get_token(self, _scope: str) -> Any:
        return SimpleNamespace(token="token-value")

    def close(self) -> None:
        return None


class FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, Any] | None = None) -> None:
        self.status_code = status_code
        self._payload = payload
        self.headers: dict[str, str] = {}
        self.content = b"json" if payload is not None else b""
        self.text = ""

    def json(self) -> dict[str, Any]:
        if self._payload is None:
            raise ValueError
        return self._payload


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.responses.pop(0)

    def close(self) -> None:
        return None


class AzureClientTests(unittest.TestCase):
    def _client(self, responses: list[FakeResponse]) -> tuple[ArmClient, FakeSession]:
        client = ArmClient(FakeCredential(), max_retries=0)
        session = FakeSession(responses)
        client._session = session
        return client, session

    def test_list_follows_management_endpoint_pagination(self) -> None:
        next_link = "https://management.azure.com/next?page=2&api-version=2025-09-01"
        client, session = self._client(
            [
                FakeResponse(200, {"value": [{"name": "one"}], "nextLink": next_link}),
                FakeResponse(200, {"value": [{"name": "two"}]}),
            ]
        )
        rules = client.list_rules(workspace())
        self.assertEqual([rule["name"] for rule in rules], ["one", "two"])
        self.assertEqual(session.calls[0]["headers"]["Authorization"], "Bearer token-value")
        self.assertIsNone(session.calls[1]["params"])

    def test_put_uses_etag_for_concurrency(self) -> None:
        client, session = self._client([FakeResponse(200, {"name": "rule"})])
        client.put_rule(workspace(), "rule", {"properties": {}}, '"etag"')
        self.assertEqual(session.calls[0]["headers"]["If-Match"], '"etag"')

    def test_precondition_failure_is_concurrent_change(self) -> None:
        client, _ = self._client([FakeResponse(412, {"error": {"message": "changed"}})])
        with self.assertRaises(ConcurrentChangeError):
            client.put_rule(workspace(), "rule", {"properties": {}}, '"etag"')

    def test_resource_graph_query_follows_skip_token(self) -> None:
        client, session = self._client(
            [
                FakeResponse(200, {"data": [{"name": "one"}], "$skipToken": "next"}),
                FakeResponse(200, {"data": [{"name": "two"}]}),
            ]
        )
        rows = client.resource_graph_query("Resources | project name")
        self.assertEqual([row["name"] for row in rows], ["one", "two"])
        self.assertEqual(session.calls[1]["json"]["options"]["$skipToken"], "next")

    def test_sentinel_onboarding_404_means_not_enabled(self) -> None:
        client, _ = self._client([FakeResponse(404, {"error": {"code": "NotFound"}})])
        self.assertFalse(client.is_sentinel_workspace(workspace()))


if __name__ == "__main__":
    unittest.main()
