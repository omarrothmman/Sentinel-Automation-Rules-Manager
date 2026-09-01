from __future__ import annotations

import warnings
from typing import Any

from .errors import ConfigurationError


def create_credential(mode: str, tenant_id: str | None) -> Any:
    try:
        from azure.identity import (
            AzureCliCredential,
            DefaultAzureCredential,
            InteractiveBrowserCredential,
            TokenCachePersistenceOptions,
        )
    except ImportError as exc:
        raise ConfigurationError(
            "Azure authentication dependency is missing. Run: py -m pip install -e ."
        ) from exc

    try:
        # azure-identity's browser flow currently uses a localhost query callback.
        # Newer MSAL versions emit a recommendation that azure-identity doesn't yet
        # expose a switch for; it is informational and obscures normal CLI output.
        warnings.filterwarnings(
            "ignore",
            message=r"response_mode='form_post' is recommended.*",
            category=UserWarning,
            module=r"msal\.oauth2cli\.oauth2",
        )
        if mode == "interactive":
            options = TokenCachePersistenceOptions(name="sentinel-automation-rules")
            kwargs: dict[str, Any] = {"cache_persistence_options": options}
            if tenant_id:
                kwargs["tenant_id"] = tenant_id
            return InteractiveBrowserCredential(**kwargs)
        if mode == "cli":
            return AzureCliCredential(tenant_id=tenant_id) if tenant_id else AzureCliCredential()
        if mode == "default":
            kwargs = {"exclude_interactive_browser_credential": False}
            if tenant_id:
                kwargs["interactive_browser_tenant_id"] = tenant_id
            return DefaultAzureCredential(**kwargs)
    except Exception as exc:
        raise ConfigurationError(f"Could not initialize Azure authentication: {exc}") from exc
    raise ConfigurationError(f"Unsupported authentication mode: {mode}")
