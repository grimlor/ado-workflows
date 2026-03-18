"""Partial stubs for azure.devops.v7_1.location.location_client."""

from __future__ import annotations

class _AuthenticatedUser:
    provider_display_name: str
    custom_display_name: str | None
    id: str
    descriptor: str | None


class _ConnectionData:
    authenticated_user: _AuthenticatedUser


class LocationClient:
    def get_connection_data(self) -> _ConnectionData: ...
