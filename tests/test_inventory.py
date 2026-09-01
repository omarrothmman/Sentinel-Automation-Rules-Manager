import json
import tempfile
import unittest
from pathlib import Path

from sentinel_automation.errors import ConfigurationError
from sentinel_automation.inventory import Inventory


class InventoryTests(unittest.TestCase):
    def _write(self, value: object) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "inventory.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def test_modern_inventory_and_tag_selection(self) -> None:
        inventory = Inventory.load(
            self._write(
                {
                    "managing_tenant_id": "tenant-id",
                    "workspaces": [
                        {
                            "key": "a",
                            "subscription_id": "sub-a",
                            "resource_group": "rg-a",
                            "workspace_name": "ws-a",
                            "tags": ["prod"],
                        },
                        {
                            "key": "b",
                            "subscription_id": "sub-b",
                            "resource_group": "rg-b",
                            "workspace_name": "ws-b",
                            "tags": ["prod"],
                        },
                    ],
                }
            )
        )
        self.assertEqual([item.key for item in inventory.select("prod")], ["a", "b"])
        self.assertEqual(inventory.managing_tenant_id, "tenant-id")

    def test_legacy_database_is_supported(self) -> None:
        inventory = Inventory.load(
            self._write(
                {
                    "1": {
                        "tenant_name": "Customer",
                        "subscription_id": "sub",
                        "resource_group_name": "rg",
                        "workspace_name": "ws",
                    }
                }
            )
        )
        self.assertEqual(inventory.workspaces[0].display_name, "Customer")
        self.assertEqual(inventory.workspaces[0].resource_group, "rg")

    def test_duplicate_keys_are_rejected_case_insensitively(self) -> None:
        with self.assertRaises(ConfigurationError):
            Inventory.load(
                self._write(
                    {
                        "workspaces": [
                            {
                                "key": "A",
                                "subscription_id": "s",
                                "resource_group": "r",
                                "workspace_name": "w",
                            },
                            {
                                "key": "a",
                                "subscription_id": "s",
                                "resource_group": "r",
                                "workspace_name": "w",
                            },
                        ]
                    }
                )
            )


if __name__ == "__main__":
    unittest.main()
