"""User-scoped capability and tool discovery for P1-7A."""

from __future__ import annotations

from typing import Any, Callable

from .connectors.registry import ConnectorRegistry
from .p1_7_contracts import ToolContract
from .tool_registry import ToolRegistry


class CapabilityDiscovery:
    """Filter registered tools by a caller's connection and granted scopes."""

    def __init__(self, tool_registry: ToolRegistry, connector_registry: ConnectorRegistry) -> None:
        self._tools = tool_registry
        self._connectors = connector_registry

    def discover(
        self,
        owner_id: str,
        connection_lookup: Callable[[str, str], dict[str, Any] | None],
    ) -> dict[str, Any]:
        if not owner_id:
            return {"success": False, "error": "owner_id is required", "tools": [], "capabilities": []}
        available: list[dict[str, Any]] = []
        capabilities: set[str] = set()
        for raw_tool in self._tools.list_tools().get("tools", []):
            contract = ToolContract(
                tool_name=raw_tool["tool_name"],
                version=raw_tool.get("version", "1.0"),
                description=raw_tool.get("description", ""),
                input_schema=raw_tool.get("input_schema", raw_tool.get("schema", {})),
                output_schema=raw_tool.get("output_schema", {}),
                capability=raw_tool.get("capability"),
                platform=raw_tool.get("platform"),
                operation=raw_tool.get("operation"),
                risk_level=raw_tool.get("risk_level", "READ_ONLY"),
                requires_connection=raw_tool.get("requires_connection", False),
                requires_approval=raw_tool.get("requires_approval", False),
                supports_dry_run=raw_tool.get("supports_dry_run", True),
                idempotency_behavior=raw_tool.get("idempotency_behavior", "not_applicable"),
                timeout=raw_tool.get("timeout", 30.0),
                retry_policy=raw_tool.get("retry_policy", "bounded"),
            )
            if contract.platform:
                connector = self._connectors.get(contract.platform)
                if connector is None:
                    continue
                connection = connection_lookup(owner_id, contract.platform) or {}
                if contract.requires_connection and connection.get("status") != "connected":
                    continue
                required_scopes = set(raw_tool.get("required_scopes", []))
                granted_scopes = set(connection.get("scopes", []))
                if not required_scopes.issubset(granted_scopes):
                    continue
                connector_caps = connector.capabilities.to_dict()
                # Onboarding is the capability that establishes a
                # connection; requiring that capability to already be enabled
                # would make first-time onboarding undiscoverable. Keep it
                # owner-scoped and require the connector to be registered.
                is_onboarding = contract.operation in {"start_onboarding", "onboarding"}
                if contract.capability and not connector_caps.get(contract.capability, False) and not is_onboarding:
                    continue
            available.append(contract.to_dict())
            if contract.capability:
                capabilities.add(contract.capability)
        return {
            "success": True,
            "owner_id": owner_id,
            "tools": available,
            "capabilities": sorted(capabilities),
        }


__all__ = ["CapabilityDiscovery"]