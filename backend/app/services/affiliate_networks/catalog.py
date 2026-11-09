"""Provider-neutral affiliate discovery contracts.

This module defines the shared catalog contract for approved affiliate-network
connectors. It intentionally contains no network credentials or live API calls:
provider access must be configured and approved before a connector can fetch data.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol


SUPPORTED_NETWORKS = ("cj", "awin", "partnerstack")


@dataclass(frozen=True)
class AffiliateOffer:
    """Normalized product/program record returned by an affiliate network."""

    network: str
    external_id: str
    name: str
    description: str | None = None
    category: str | None = None
    image_url: str | None = None
    destination_url: str | None = None
    affiliate_url: str | None = None
    commission_value: float | None = None
    commission_type: str | None = None
    currency: str | None = None
    partnership_status: str = "unknown"
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.network not in SUPPORTED_NETWORKS:
            raise ValueError("Unsupported affiliate network")
        if not self.external_id.strip() or not self.name.strip():
            raise ValueError("external_id and name are required")
        if self.partnership_status not in {
            "discovered", "application_required", "pending", "approved",
            "rejected", "link_available", "unknown",
        }:
            raise ValueError("Invalid partnership_status")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AffiliateDiscoveryConnector(Protocol):
    """Contract implemented by each network-specific discovery adapter."""

    network: str

    def health(self) -> dict[str, Any]: ...

    def discover(self, *, category: str | None = None, cursor: str | None = None,
                 limit: int = 100) -> dict[str, Any]: ...


@dataclass(frozen=True)
class NetworkAccessStatus:
    """Safe, non-secret connection state suitable for the Hub UI."""

    network: str
    configured: bool
    publisher_approved: bool
    credential_type: str | None
    status: str
    message: str
    checked_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AffiliateNetworkRegistry:
    """Registry for discovery adapters; never stores credentials."""

    def __init__(self) -> None:
        self._connectors: dict[str, AffiliateDiscoveryConnector] = {}

    def register(self, connector: AffiliateDiscoveryConnector) -> None:
        network = getattr(connector, "network", "")
        if network not in SUPPORTED_NETWORKS:
            raise ValueError("Unsupported affiliate network")
        self._connectors[network] = connector

    def get(self, network: str) -> AffiliateDiscoveryConnector | None:
        return self._connectors.get(network.strip().lower())

    def list_networks(self) -> list[str]:
        return list(SUPPORTED_NETWORKS)

    def statuses(self) -> list[dict[str, Any]]:
        result = []
        for network in SUPPORTED_NETWORKS:
            connector = self._connectors.get(network)
            if connector is None:
                result.append(NetworkAccessStatus(
                    network=network,
                    configured=False,
                    publisher_approved=False,
                    credential_type=None,
                    status="not_configured",
                    message="Publisher access and credentials have not been configured.",
                ).to_dict())
            else:
                # Connector health must return safe metadata only. Never expose
                # tokens, keys, authorization headers, or raw provider responses.
                result.append(connector.health())
        return result


__all__ = [
    "AffiliateOffer",
    "AffiliateDiscoveryConnector",
    "AffiliateNetworkRegistry",
    "NetworkAccessStatus",
    "SUPPORTED_NETWORKS",
]
