import unittest

from sentinel_automation.errors import ConfigurationError, describe_error, present_errors


class ErrorPresentationTests(unittest.TestCase):
    def test_azure_failure_exposes_fields_without_json_message(self):
        error = 'Azure HTTP 403: {"code":"AuthorizationFailed","message":"Access denied","details":[{"code":"MissingRole","message":"Assign a role"}],"target":"workspace"}; request-id=req-123'
        info = describe_error(error)
        self.assertEqual(info["http_status"], 403)
        self.assertEqual(info["code"], "AuthorizationFailed")
        self.assertEqual(info["message"], "Access denied")
        self.assertEqual(info["request_id"], "req-123")
        self.assertEqual(info["target"], "workspace")
        self.assertIn({"label": "details 1 / code", "value": "MissingRole"}, info["details"])

    def test_saved_run_errors_are_normalized_without_mutating_backup(self):
        source = {"results": [{"error": 'Azure HTTP 409: {"code":"Conflict","message":"Changed"}'}]}
        presented = present_errors(source)
        self.assertEqual(presented["results"][0]["error"], "Changed")
        self.assertEqual(presented["results"][0]["error_info"]["code"], "Conflict")
        self.assertTrue(source["results"][0]["error"].startswith("Azure HTTP"))

    def test_plain_and_non_json_failures(self):
        self.assertEqual(
            describe_error(ConfigurationError("Select a workspace"))["code"], "ConfigurationError"
        )
        self.assertEqual(
            describe_error("Azure HTTP 502: Gateway unavailable")["message"], "Gateway unavailable"
        )
        self.assertEqual(
            describe_error(
                'Azure request failed after retries: Azure HTTP 429: {"code":"Throttled","message":"Retry later"}'
            )["code"],
            "Throttled",
        )
