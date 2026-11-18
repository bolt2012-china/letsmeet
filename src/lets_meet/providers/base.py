from __future__ import annotations

from abc import ABC, abstractmethod
from lets_meet.models import LatLng, PlaceSuggestion, ResolvedParticipant, VenueCandidate


class MapProvider(ABC):
    name: str = "base"

    @abstractmethod
    async def geocode(
        self,
        address: str,
        city: str = "",
        bias_coords: LatLng | None = None,
    ) -> tuple[LatLng, str]:
        """Resolve free-text address into canonical WGS-84 (LatLng, formatted_address)."""

    async def suggest_places(
        self,
        query: str,
        city: str = "",
        bias_coords: LatLng | None = None,
        limit: int = 8,
    ) -> list[PlaceSuggestion]:
        """Return interactive candidate places so the user can choose & confirm the exact location."""
        coords, formatted = await self.geocode(query, city=city, bias_coords=bias_coords)
        return [
            PlaceSuggestion(
                place_id=f"{self.name}_{coords.lat:.5f}_{coords.lng:.5f}",
                name=query.strip(),
                subtext=formatted,
                city=city,
                coords=coords,
                provider=self.name,
            )
        ]

    @abstractmethod
    async def search_places(
        self,
        center: LatLng,
        radius_m: int,
        keywords: list[str],
        category: str = "restaurant",
        city: str = "",
    ) -> list[VenueCandidate]:
        """Search candidate venues around center (WGS-84) within radius_m."""

    @abstractmethod
    async def compute_travel_matrix(
        self,
        participants: list[ResolvedParticipant],
        venues: list[VenueCandidate],
        city: str = "",
    ) -> list[list[tuple[float, float, str]]]:
        """
        Return N x M matrix where entry [i][j] = (duration_minutes, distance_km, route_summary)
        for participant i using participants[i].transport_mode to reach venues[j].
        """
