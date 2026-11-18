from __future__ import annotations

import asyncio
from lets_meet.config import RuntimeConfig
from lets_meet.fairness import score_and_rank_venues
from lets_meet.geo import CITY_HUBS, compute_search_corridor, resolve_city_center, spherical_centroid
from lets_meet.llm import MeetingLLMAgent
from lets_meet.models import (
    LatLng,
    ParticipantInput,
    RecommendRequest,
    RecommendResponse,
    ResolvedParticipant,
    VenueCandidate,
)
from lets_meet.providers import OpenStreetMapProvider, select_map_provider


async def _resolve_one(
    p: ParticipantInput,
    provider,
    city: str,
    bias_coords: LatLng | None = None,
) -> ResolvedParticipant:
    if p.coords is not None:
        label = p.resolved_address.strip() or p.address.strip() or f"地图选点 ({p.coords.lat:.4f}, {p.coords.lng:.4f})"
        return ResolvedParticipant(
            id=p.id,
            name=p.name,
            input_address=p.address or label,
            resolved_address=label,
            coords=p.coords,
            transport_mode=p.transport_mode,
            preference_note=p.preference_note,
        )
    if not p.address.strip():
        raise ValueError(f"参与者「{p.name}」未填写出发地址或选点坐标")

    coords, formatted = await provider.geocode(p.address, city=city, bias_coords=bias_coords)
    return ResolvedParticipant(
        id=p.id,
        name=p.name,
        input_address=p.address,
        resolved_address=formatted,
        coords=coords,
        transport_mode=p.transport_mode,
        preference_note=p.preference_note,
    )


def _has_explicit_city_hint(address: str) -> bool:
    low = address.strip().lower()
    return any(hub in low for hub in CITY_HUBS)


async def _resolve_all_participants(
    participants: list[ParticipantInput],
    provider,
    city: str,
) -> list[ResolvedParticipant]:
    """
    Two-stage origin resolution to prevent homonym jumps:
      1. Resolve already-confirmed coordinates and addresses containing explicit city names first.
      2. Use their centroid (or selected `city` center) as `bias_coords` for shorter/ambiguous place names.
    """
    n = len(participants)
    results: list[ResolvedParticipant | None] = [None] * n
    city_bias = resolve_city_center(city)

    anchored_idxs = [
        i for i, p in enumerate(participants) if p.coords is not None or _has_explicit_city_hint(p.address)
    ]
    ambiguous_idxs = [i for i in range(n) if i not in anchored_idxs]

    if anchored_idxs:
        anchored_res = await asyncio.gather(
            *[_resolve_one(participants[i], provider, city, bias_coords=city_bias) for i in anchored_idxs]
        )
        for idx, res in zip(anchored_idxs, anchored_res):
            results[idx] = res

    known_pts = [r.coords for r in results if r is not None]
    eff_bias = spherical_centroid(known_pts) if known_pts else city_bias

    if ambiguous_idxs:
        amb_res = await asyncio.gather(
            *[_resolve_one(participants[i], provider, city, bias_coords=eff_bias) for i in ambiguous_idxs]
        )
        for idx, res in zip(ambiguous_idxs, amb_res):
            results[idx] = res

    return [r for r in results if r is not None]


async def run_recommendation(
    req: RecommendRequest,
    base_config: RuntimeConfig,
) -> RecommendResponse:
    overrides = req.runtime_keys.model_dump(exclude_none=True) if req.runtime_keys else None
    cfg = base_config.with_overrides(overrides)

    context_text = " ".join(
        [req.city, req.shared_prompt] + [f"{p.address} {p.preference_note}" for p in req.participants]
    )
    known_coords = [p.coords for p in req.participants]
    provider, warnings = select_map_provider(cfg, context_text=context_text, known_coords=known_coords)
    llm_agent = MeetingLLMAgent(cfg)

    intent_task = asyncio.create_task(
        llm_agent.parse_joint_intent(
            participants=req.participants,
            venue_category=req.venue_category,
            shared_prompt=req.shared_prompt,
            city=req.city,
        )
    )

    try:
        resolved_participants = await _resolve_all_participants(req.participants, provider, req.city)
    except Exception as exc:
        if provider.name in ("amap", "google"):
            warnings.append(f"{provider.name} 引擎调用异常（{exc}），已自动回退至 OpenStreetMap 真实路网。")
            provider = OpenStreetMapProvider()
            resolved_participants = await _resolve_all_participants(req.participants, provider, req.city)
        else:
            intent_task.cancel()
            raise

    parsed_intent = await intent_task

    # Step 3: Compute geometric midpoint, speed-weighted equal-time anchor, and corridor anchors
    geometric_mid, time_weighted_mid, corridor_anchors, radius_m = compute_search_corridor(
        list(resolved_participants)
    )

    # Step 4: Recall candidate venues across corridor & deduplicate
    try:
        if provider.name == "osm":
            anchor_batches = [
                await provider.search_places(
                    center=time_weighted_mid,
                    radius_m=int(radius_m * 1.45),
                    keywords=parsed_intent.search_keywords,
                    category=req.venue_category,
                    city=req.city,
                )
            ]
        else:
            search_tasks = [
                provider.search_places(
                    center=anchor.coords,
                    radius_m=radius_m,
                    keywords=parsed_intent.search_keywords,
                    category=req.venue_category,
                    city=req.city,
                )
                for anchor in corridor_anchors
            ]
            anchor_batches = await asyncio.gather(*search_tasks)
    except Exception as exc:
        if provider.name in ("amap", "google"):
            warnings.append(f"{provider.name} 周边检索失败（{exc}），已切换 OpenStreetMap 检索。")
            provider = OpenStreetMapProvider()
            anchor_batches = [
                await provider.search_places(
                    center=time_weighted_mid,
                    radius_m=int(radius_m * 1.45),
                    keywords=parsed_intent.search_keywords,
                    category=req.venue_category,
                    city=req.city,
                )
            ]
        else:
            raise

    deduped: dict[str, VenueCandidate] = {}
    for batch in anchor_batches:
        for v in batch:
            key = v.place_id or f"{v.name}_{v.coords.lat:.4f}_{v.coords.lng:.4f}"
            if key not in deduped:
                deduped[key] = v

    candidates = list(deduped.values())[:20]

    # Step 5: Compute real N x M travel-time matrix across heterogeneous transport modes
    travel_matrix = await provider.compute_travel_matrix(
        participants=list(resolved_participants),
        venues=candidates,
        city=req.city,
    )

    # Step 6: Multi-objective fairness scoring + LLM semantic rerank & explanation
    ranked = score_and_rank_venues(
        participants=list(resolved_participants),
        candidates=candidates,
        travel_matrix=travel_matrix,
        fairness_mode=req.fairness_mode,
        intent=parsed_intent,
        meetup_time=req.meetup_time,
    )
    ranked, rerank_used_llm, rerank_source = await llm_agent.rerank_and_explain(
        ranked=ranked,
        participants=req.participants,
        intent=parsed_intent,
        shared_prompt=req.shared_prompt,
    )

    active_llm_source = rerank_source or (parsed_intent.llm_source if parsed_intent.used_llm else "")

    return RecommendResponse(
        active_provider=provider.name,
        llm_active=bool(parsed_intent.used_llm or rerank_used_llm),
        llm_source=active_llm_source,
        fairness_mode=req.fairness_mode,
        parsed_intent=parsed_intent,
        participants=list(resolved_participants),
        geometric_midpoint=geometric_mid,
        time_weighted_midpoint=time_weighted_mid,
        corridor_anchors=corridor_anchors,
        search_radius_m=radius_m,
        recommendations=ranked[: req.limit],
        warnings=warnings,
    )
