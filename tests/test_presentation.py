import os
import unittest
from unittest.mock import patch

from sentinel_automation.presentation import Column, render_table


class PresentationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.columns = (
            Column("TARGET", 18, 30),
            Column("STATUS", 10, 18),
            Column("RESULT", 30, 80),
        )
        self.rows = [
            ("customer-a", "Updated", "Rule updated and verified"),
            ("customer-b", "Skipped", "Rule not found"),
        ]

    def test_wide_terminal_uses_numbered_aligned_table(self) -> None:
        with patch(
            "sentinel_automation.presentation.shutil.get_terminal_size",
            return_value=os.terminal_size((120, 30)),
        ):
            output = render_table("RESULTS", "2 targets", self.columns, self.rows)
        self.assertIn("#     TARGET", output)
        self.assertIn("1     customer-a", output)
        self.assertIn("Rule updated and verified", output)

    def test_narrow_terminal_uses_cards_without_losing_values(self) -> None:
        with patch(
            "sentinel_automation.presentation.shutil.get_terminal_size",
            return_value=os.terminal_size((60, 30)),
        ):
            output = render_table("RESULTS", "2 targets", self.columns, self.rows)
        self.assertIn("[01] customer-a", output)
        self.assertIn("Status:", output)
        self.assertIn("Rule updated and verified", output)


if __name__ == "__main__":
    unittest.main()
