import unittest

from sentinel_automation.discovery import (
    SUBSCRIPTIONS_QUERY,
    DiscoveryResult,
    discover_sentinel_workspaces,
    discovery_table,
    inventory_document,
)
from sentinel_automation.errors import AzureRequestError
from sentinel_automation.inventory import Inventory, Workspace

from .helpers import workspace


class FakeDiscoveryClient:
    def __init__(self) -> None:
        self.verified = {"sentinel-a": True, "ordinary-law": False}

    def resource_graph_query(self, query: str, subscriptions=None):
        if query == SUBSCRIPTIONS_QUERY:
            return [
                {
                    "subscriptionId": "11111111-1111-1111-1111-111111111111",
                    "subscriptionName": "Customer A",
                    "tenantId": "customer-tenant",
                }
            ]
        return [
            {
                "name": "sentinel-a",
                "resourceGroup": "security-rg",
                "subscriptionId": "11111111-1111-1111-1111-111111111111",
                "tenantId": "customer-tenant",
                "location": "westeurope",
            },
            {
                "name": "ordinary-law",
                "resourceGroup": "logs-rg",
                "subscriptionId": "11111111-1111-1111-1111-111111111111",
                "tenantId": "customer-tenant",
                "location": "westeurope",
            },
        ]

    def is_sentinel_workspace(self, target: Workspace) -> bool:
        return self.verified[target.workspace_name]


class DiscoveryTests(unittest.TestCase):
    def test_discovers_only_sentinel_and_marks_lighthouse(self) -> None:
        result = discover_sentinel_workspaces(FakeDiscoveryClient(), "managing-tenant")
        self.assertEqual(result.subscription_count, 1)
        self.assertEqual(result.log_analytics_count, 2)
        self.assertEqual(len(result.workspaces), 1)
        self.assertEqual(result.workspaces[0].workspace_name, "sentinel-a")
        self.assertEqual(result.workspaces[0].key, "sentinel-a")
        self.assertIn("lighthouse", result.workspaces[0].tags)

    def test_discovery_table_has_numbered_aligned_columns(self) -> None:
        result = discover_sentinel_workspaces(FakeDiscoveryClient(), "managing-tenant")
        rendered = discovery_table(result)
        self.assertIn("MICROSOFT SENTINEL WORKSPACE DISCOVERY", rendered)
        self.assertIn("SUBSCRIPTION", rendered)
        self.assertIn("Lighthouse", rendered)
        self.assertNotIn("\t", rendered)

    def test_verification_failures_are_reported_without_hiding_other_results(self) -> None:
        client = FakeDiscoveryClient()

        def verify(target: Workspace) -> bool:
            if target.workspace_name == "ordinary-law":
                raise AzureRequestError("forbidden")
            return True

        client.is_sentinel_workspace = verify
        result = discover_sentinel_workspaces(client, "managing-tenant")
        self.assertEqual(len(result.workspaces), 1)
        self.assertEqual(len(result.issues), 1)
        self.assertIn("forbidden", result.issues[0].error)

    def test_inventory_merge_preserves_manual_settings_and_stale_entries(self) -> None:
        current = workspace("custom-key")
        existing = Inventory(
            "managing-tenant",
            (
                current,
                Workspace(
                    key="manual",
                    display_name="Manual",
                    subscription_id="sub-2",
                    resource_group="rg-2",
                    workspace_name="ws-2",
                    enabled=False,
                    tags=("manual",),
                ),
            ),
        )
        discovered = Workspace(
            key="generated-key",
            display_name="Generated",
            subscription_id=current.subscription_id,
            resource_group=current.resource_group,
            workspace_name=current.workspace_name,
            enabled=True,
            tags=("discovered",),
        )
        result = DiscoveryResult((discovered,), (), 1, 1)
        document = inventory_document(result, "managing-tenant", existing)
        rows = {row["key"]: row for row in document["workspaces"]}
        self.assertIn("custom-key", rows)
        self.assertIn("manual", rows)
        self.assertIn("discovered", rows["custom-key"]["tags"])

    def test_inventory_replace_removes_stale_entries(self) -> None:
        existing = Inventory("tenant", (workspace("stale"),))
        fresh = Workspace(
            key="fresh",
            display_name="Fresh",
            subscription_id="sub-new",
            resource_group="rg-new",
            workspace_name="ws-new",
            enabled=True,
            tags=("discovered",),
        )
        document = inventory_document(DiscoveryResult((fresh,), (), 1, 1), "tenant", existing, True)
        self.assertEqual([row["key"] for row in document["workspaces"]], ["fresh"])

    def test_inventory_merge_resolves_generated_key_collision(self) -> None:
        existing = Inventory("tenant", (workspace("collision"),))
        fresh = Workspace(
            key="collision",
            display_name="Different Workspace",
            subscription_id="22222222-2222-2222-2222-222222222222",
            resource_group="different-rg",
            workspace_name="different-ws",
            enabled=True,
            tags=("discovered",),
        )
        document = inventory_document(DiscoveryResult((fresh,), (), 1, 1), "tenant", existing)
        keys = [row["key"] for row in document["workspaces"]]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertIn("collision", keys)


if __name__ == "__main__":
    unittest.main()
