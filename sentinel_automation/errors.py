import json
import re
from typing import Any


def describe_error(error: Any) -> dict[str, Any]:
    """Normalize Azure failures (including older saved runs) for human-readable clients."""
    message = str(error)
    result: dict[str, Any] = {
        "code": type(error).__name__ if isinstance(error, Exception) else "OperationFailed",
        "message": message,
        "details": [],
    }
    match = re.search(r"Azure HTTP (\d+):\s*(.*)", message, re.DOTALL)
    payload = None
    if match:
        result["http_status"] = int(match.group(1))
        raw, _, request_id = match.group(2).partition("; request-id=")
        if request_id:
            result["request_id"] = request_id.strip()
        try:
            payload = json.loads(raw)
        except (ValueError, TypeError):
            result["message"] = raw
    elif message.lstrip().startswith("{"):
        try:
            payload = json.loads(message)
        except ValueError:
            pass
    if isinstance(payload, dict):
        payload = payload.get("error", payload)
        if isinstance(payload, dict):
            result["code"] = str(payload.get("code") or result["code"])
            result["message"] = str(
                payload.get("message") or "Azure could not complete the request"
            )
            if payload.get("target"):
                result["target"] = str(payload["target"])

            def collect(value: Any, prefix: str = "") -> None:
                if isinstance(value, dict):
                    for key, child in value.items():
                        collect(child, f"{prefix} / {key}" if prefix else key)
                elif isinstance(value, list):
                    for index, child in enumerate(value, 1):
                        collect(child, f"{prefix} {index}".strip())
                elif value is not None:
                    result["details"].append({"label": prefix, "value": str(value)})

            for key, value in payload.items():
                if key not in ("code", "message", "target"):
                    collect(value, key)
    return result


def present_errors(value: Any) -> Any:
    if isinstance(value, list):
        return [present_errors(item) for item in value]
    if isinstance(value, dict):
        result = {key: present_errors(item) for key, item in value.items()}
        if isinstance(value.get("error"), str):
            info = describe_error(value["error"])
            result.update(error=info["message"], error_info=info)
        return result
    return value


class SentinelAutomationError(Exception):
    """Expected, user-facing application error."""


class ConfigurationError(SentinelAutomationError):
    """Invalid local configuration."""


class AzureRequestError(SentinelAutomationError):
    """Azure Resource Manager request failed."""


class RuleNotFoundError(SentinelAutomationError):
    """The requested automation rule was not found."""


class AmbiguousRuleError(SentinelAutomationError):
    """More than one automation rule matched a selector."""


class ConcurrentChangeError(SentinelAutomationError):
    """Azure state changed after a plan was generated."""


class PlanIntegrityError(SentinelAutomationError):
    """A plan is invalid or has been modified."""
