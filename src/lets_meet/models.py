from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field

TransportMode = Literal["transit", "driving", "walking", "cycling"]
FairnessMode = Literal["fair", "equal_time", "fast_total"]
ProviderName = Literal["auto", "osm", "google", "amap", "demo"]
LLMProviderName = Literal[
    "auto",
    "official",
    "free_public",
    "qwen_web",
    "deepseek_web",
    "kimi_web",
    "doubao_web",
    "disabled",
]


class LatLng(BaseModel):
    """Canonical coordinate in WGS-84."""

    lat: float = Field(..., ge=-90.0, le=90.0)
    lng: float = Field(..., ge=-180.0, le=180.0)


class ParticipantInput(BaseModel):
    id: str = Field(default="p1")
    name: str = Field(..., min_length=1, max_length=40)
    address: str = Field(default="", max_length=200)
    resolved_address: str = Field(default="", max_length=260)
    coords: LatLng | None = None
    transport_mode: TransportMode = "transit"
    preference_note: str = Field(default="", max_length=300)


class ResolvedParticipant(BaseModel):
    id: str
    name: str
    input_address: str
    resolved_address: str
    coords: LatLng
    transport_mode: TransportMode
    preference_note: str = ""


class RuntimeKeyOverrides(BaseModel):
    map_provider: ProviderName | None = None
    google_maps_api_key: str | None = None
    amap_api_key: str | None = None
    llm_provider: LLMProviderName | None = None
    llm_api_key: str | None = None
    llm_base_url: str | None = None
    llm_model: str | None = None
    free_web_gateway_url: str | None = None
    kimi_web_token: str | None = None
    deepseek_web_token: str | None = None
    qwen_web_token: str | None = None
    doubao_web_token: str | None = None


class RecommendRequest(BaseModel):
    participants: list[ParticipantInput] = Field(..., min_length=2, max_length=8)
    city: str = Field(default="", max_length=80)
    venue_category: str = Field(default="restaurant", max_length=60)
    shared_prompt: str = Field(default="", max_length=500)
    fairness_mode: FairnessMode = "fair"
    meetup_time: str = Field(default="", description="Optional HH:MM target arrival time, e.g. 19:00")
    limit: int = Field(default=6, ge=1, le=15)
    runtime_keys: RuntimeKeyOverrides | None = None


class ParsedIntent(BaseModel):
    search_keywords: list[str]
    cuisine_tags: list[str] = Field(default_factory=list)
    need_parking: bool = False
    near_subway: bool = False
    quiet_vibe: bool = False
    budget_hint: str = ""
    compromise_summary: str = ""
    used_llm: bool = False
    llm_source: str = ""


class VenueCandidate(BaseModel):
    place_id: str
    name: str
    address: str
    coords: LatLng
    rating: float | None = None
    review_count: int = 0
    price_level: str = ""
    categories: list[str] = Field(default_factory=list)
    open_now: bool | None = None
    highlights: list[str] = Field(default_factory=list)
    subway_hint: str = ""
    parking_hint: str = ""
    provider: str = "osm"


class ParticipantLeg(BaseModel):
    participant_id: str
    participant_name: str
    transport_mode: TransportMode
    duration_minutes: float
    distance_km: float
    route_summary: str = ""
    suggested_departure: str = ""
    navigation_links: dict[str, str] = Field(default_factory=dict)


class RankedVenue(BaseModel):
    rank: int
    venue: VenueCandidate
    total_score: float
    fairness_score: float
    quality_score: float
    semantic_score: float
    max_duration_minutes: float
    duration_gap_minutes: float
    avg_duration_minutes: float
    ai_reason: str
    transport_tip: str = ""
    legs: list[ParticipantLeg]


class CorridorAnchor(BaseModel):
    label: str
    coords: LatLng
    weight_from_first: float


class RecommendResponse(BaseModel):
    active_provider: str
    llm_active: bool
    llm_source: str = ""
    fairness_mode: FairnessMode
    parsed_intent: ParsedIntent
    participants: list[ResolvedParticipant]
    geometric_midpoint: LatLng
    time_weighted_midpoint: LatLng
    corridor_anchors: list[CorridorAnchor]
    search_radius_m: int
    recommendations: list[RankedVenue]
    warnings: list[str] = Field(default_factory=list)


class RoomCreateRequest(BaseModel):
    title: str = Field(default="周末约饭", max_length=80)
    city: str = Field(default="", max_length=80)
    venue_category: str = Field(default="restaurant", max_length=60)
    fairness_mode: FairnessMode = "fair"
    meetup_time: str = Field(default="19:00", max_length=10)
    shared_prompt: str = Field(default="", max_length=500)
    host: ParticipantInput


class RoomJoinOrUpdateRequest(BaseModel):
    participant: ParticipantInput
    city: str | None = None
    venue_category: str | None = None
    shared_prompt: str | None = None
    fairness_mode: FairnessMode | None = None
    meetup_time: str | None = None


class RoomVoteRequest(BaseModel):
    participant_id: str
    place_id: str


class RoomState(BaseModel):
    room_code: str
    title: str
    city: str
    venue_category: str
    fairness_mode: FairnessMode
    meetup_time: str
    shared_prompt: str
    participants: list[ParticipantInput]
    votes: dict[str, list[str]] = Field(default_factory=dict)
    last_result: RecommendResponse | None = None


class PlaceSuggestion(BaseModel):
    place_id: str
    name: str
    subtext: str = ""
    city: str = ""
    category: str = ""
    coords: LatLng
    provider: str = "osm"


class PlaceSuggestRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=160)
    city: str = Field(default="", max_length=80)
    bias_coords: LatLng | None = None
    limit: int = Field(default=8, ge=1, le=15)
    runtime_keys: RuntimeKeyOverrides | None = None
