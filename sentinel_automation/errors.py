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
