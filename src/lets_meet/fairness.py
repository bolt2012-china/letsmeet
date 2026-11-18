from __future__ import annotations

import math
from lets_meet.models import (
    FairnessMode,
    ParsedIntent,
    ParticipantLeg,
    RankedVenue,
    ResolvedParticipant,
    VenueCandidate,
)
from lets_meet.nav_links import build_navigation_links


def compute_suggested_departure(meetup_time: str, duration_minutes: float, buffer_minutes: int = 5) -> str:
    """Given HH:MM target arrival time, return suggested departure HH:MM including safety buffer."""
    if not meetup_time or ":" not in meetup_time:
        return ""
    try:
        hh_str, mm_str = meetup_time.strip().split(":", 1)
        target_total = int(hh_str) * 60 + int(mm_str)
        depart_total = (target_total - int(round(duration_minutes)) - buffer_minutes) % (24 * 60)
        return f"{depart_total // 60:02d}:{depart_total % 60:02d}"
    except ValueError:
        return ""


def compute_travel_cost(
    durations: list[float],
    fairness_mode: FairnessMode,
) -> tuple[float, float, float, float]:
    """
    Returns (cost_minutes, max_min, gap_min, avg_min).
    Lower cost_minutes is better.
    """
    max_d = max(durations)
    min_d = min(durations)
    gap_d = max_d - min_d
    avg_d = sum(durations) / len(durations)

    if fairness_mode == "equal_time":
        # Prioritize equalizing travel burden (|t_A - t_B|), with worst-case tiebreaker
        cost = 0.75 * gap_d + 0.25 * max_d
    elif fairness_mode == "fast_total":
        # Minimize total/average commute first; use gap only as a near-tie breaker
        cost = avg_d + 0.02 * gap_d
    else:
        # "fair" (Balanced Minimax): minimize worst traveler's trip AND keep gap tight
        cost = 0.65 * max_d + 0.35 * gap_d

    return cost, round(max_d, 1), round(gap_d, 1), round(avg_d, 1)


def compute_quality_score(venue: VenueCandidate) -> float:
    """Bayesian-smoothed rating + popularity score in [0, 100]."""
    rating = venue.rating if venue.rating is not None else 4.1
    reviews = max(0, venue.review_count)
    # Bayesian shrinkage toward 4.0 prior with m=40 reviews
    prior_m = 40.0
    bayes_rating = (reviews * rating + prior_m * 4.0) / (reviews + prior_m)
    rating_norm = max(0.0, min(1.0, (bayes_rating - 3.2) / 1.7))
    pop_bonus = min(1.0, math.log10(reviews + 1) / 3.2)
    open_bonus = 0.05 if venue.open_now is True else (-0.12 if venue.open_now is False else 0.0)
    score = (0.78 * rating_norm + 0.22 * pop_bonus + open_bonus) * 100.0
    return round(max(20.0, min(99.0, score)), 1)


def rule_semantic_match_score(venue: VenueCandidate, intent: ParsedIntent) -> tuple[float, str, str]:
    """
    Deterministic semantic match score in [0, 100] + baseline reason & transport tip.
    Used directly in fast mode or blended with LLM semantic judgment when LLM is active.
    """
    score = 72.0
    matched_points: list[str] = []
    haystack = " ".join(
        [venue.name, venue.address, " ".join(venue.categories), " ".join(venue.highlights), venue.subway_hint, venue.parking_hint]
    ).lower()

    for tag in intent.cuisine_tags + intent.search_keywords:
        t = tag.strip().lower()
        if t and t in haystack:
            score += 8.5
            if tag not in matched_points:
                matched_points.append(tag)

    if intent.need_parking:
        if venue.parking_hint or any(k in haystack for k in ("停车", "parking", "商场", "广场", "商城", "mall", "plaza")):
            score += 8.0
            matched_points.append("方便停车")
        else:
            score -= 4.0

    if intent.near_subway:
        if venue.subway_hint or any(k in haystack for k in ("地铁", "站", "station", "bart", "metro", "轨道")):
            score += 8.0
            matched_points.append("靠近车站")

    if intent.quiet_vibe and any(k in haystack for k in ("安静", "包间", "图书馆", "公园", "bistro", "quiet", "cafe", "茶")):
        score += 7.0
        matched_points.append("环境相对安静")

    score = round(max(40.0, min(98.0, score)), 1)

    tips: list[str] = []
    if venue.subway_hint:
        tips.append(venue.subway_hint)
    if venue.parking_hint:
        tips.append(venue.parking_hint)
    transport_tip = "；".join(tips)

    if matched_points:
        reason = "、".join(matched_points[:3])
    else:
        reason = "位置居中，两边过去都不绕路"

    return score, reason, transport_tip


def score_and_rank_venues(
    participants: list[ResolvedParticipant],
    candidates: list[VenueCandidate],
    travel_matrix: list[list[tuple[float, float, str]]],
    fairness_mode: FairnessMode,
    intent: ParsedIntent,
    meetup_time: str = "",
) -> list[RankedVenue]:
    """
    Combine real travel-time matrix (N participants x M candidates) + Bayesian quality + semantic match.
    travel_matrix[i][j] = (duration_minutes, distance_km, route_summary)
    """
    if not candidates:
        return []

    raw_items: list[dict] = []
    costs: list[float] = []

    for j, venue in enumerate(candidates):
        durations: list[float] = []
        legs: list[ParticipantLeg] = []
        unreachable = False

        for i, p in enumerate(participants):
            dur_min, dist_km, summary = travel_matrix[i][j]
            if dur_min <= 0 or math.isinf(dur_min):
                unreachable = True
                break
            durations.append(dur_min)
            nav_links = build_navigation_links(
                origin=p.coords,
                origin_name=p.resolved_address or p.name,
                dest=venue.coords,
                dest_name=venue.name,
                place_id=venue.place_id,
                mode=p.transport_mode,
                provider=venue.provider,
            )
            legs.append(
                ParticipantLeg(
                    participant_id=p.id,
                    participant_name=p.name,
                    transport_mode=p.transport_mode,
                    duration_minutes=round(dur_min, 1),
                    distance_km=round(dist_km, 2),
                    route_summary=summary,
                    suggested_departure=compute_suggested_departure(meetup_time, dur_min),
                    navigation_links=nav_links,
                )
            )

        if unreachable or not durations:
            continue

        cost, max_d, gap_d, avg_d = compute_travel_cost(durations, fairness_mode)
        costs.append(cost)
        q_score = compute_quality_score(venue)
        sem_score, base_reason, transport_tip = rule_semantic_match_score(venue, intent)

        raw_items.append(
            {
                "venue": venue,
                "legs": legs,
                "cost": cost,
                "max_d": max_d,
                "gap_d": gap_d,
                "avg_d": avg_d,
                "q_score": q_score,
                "sem_score": sem_score,
                "base_reason": base_reason,
                "transport_tip": transport_tip,
            }
        )

    if not raw_items:
        return []

    best_cost = min(costs)
    spread_cost = max(10.0, max(costs) - best_cost)

    ranked: list[RankedVenue] = []
    for item in raw_items:
        # Relative + absolute travel fairness score in [25, 99]
        rel_penalty = ((item["cost"] - best_cost) / spread_cost) * 42.0
        abs_penalty = max(0.0, item["cost"] - 15.0) * 0.45
        fairness_score = round(max(25.0, min(99.0, 98.0 - rel_penalty - abs_penalty)), 1)

        # Total composite score: 52% Real Travel Fairness + 23% Venue Quality + 25% Semantic Preference Fit
        total_score = round(
            0.52 * fairness_score + 0.23 * item["q_score"] + 0.25 * item["sem_score"],
            1,
        )

        leg_desc = "，".join(f"{leg.participant_name}约 {int(round(leg.duration_minutes))} 分钟" for leg in item["legs"])
        gap_txt = f"相差 {item['gap_d']:.0f} 分钟" if item["gap_d"] >= 2 else "两边耗时几乎一样"
        full_reason = f"{leg_desc}（{gap_txt}），{item['base_reason']}。"

        ranked.append(
            RankedVenue(
                rank=0,
                venue=item["venue"],
                total_score=total_score,
                fairness_score=fairness_score,
                quality_score=item["q_score"],
                semantic_score=item["sem_score"],
                max_duration_minutes=item["max_d"],
                duration_gap_minutes=item["gap_d"],
                avg_duration_minutes=item["avg_d"],
                ai_reason=full_reason,
                transport_tip=item["transport_tip"],
                legs=item["legs"],
            )
        )

    ranked.sort(key=lambda r: (-r.total_score, r.max_duration_minutes, r.duration_gap_minutes))
    for idx, r in enumerate(ranked, start=1):
        r.rank = idx
    return ranked
