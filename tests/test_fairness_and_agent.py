import asyncio
from lets_meet.config import RuntimeConfig
from lets_meet.fairness import compute_suggested_departure, score_and_rank_venues
from lets_meet.llm import MeetingLLMAgent
from lets_meet.llm.free_web import FreeWebLLMCascade
from lets_meet.models import LatLng, ParticipantInput, ResolvedParticipant, VenueCandidate


def test_departure_time_calculation():
    assert compute_suggested_departure("19:00", 28.0, buffer_minutes=5) == "18:27"
    assert compute_suggested_departure("00:10", 20.0, buffer_minutes=5) == "23:45"


def test_fairness_modes_change_venue_priority():
    participants = [
        ResolvedParticipant(
            id="p1", name="A", input_address="A", resolved_address="A",
            coords=LatLng(lat=39.99, lng=116.33), transport_mode="transit"
        ),
        ResolvedParticipant(
            id="p2", name="B", input_address="B", resolved_address="B",
            coords=LatLng(lat=39.99, lng=116.48), transport_mode="driving"
        ),
    ]
    venues = [
        VenueCandidate(
            place_id="v_skewed", name="偏置门店", address="A旁边",
            coords=LatLng(lat=39.99, lng=116.35), rating=4.6, review_count=500
        ),
        VenueCandidate(
            place_id="v_balanced", name="等时门店", address="折中点",
            coords=LatLng(lat=39.99, lng=116.41), rating=4.6, review_count=500
        ),
    ]
    matrix = [
        [(10.0, 3.0, "地铁"), (30.0, 10.0, "地铁")],
        [(42.0, 18.0, "驾车"), (30.0, 12.0, "驾车")],
    ]
    agent = MeetingLLMAgent(RuntimeConfig(llm_provider="disabled"))
    intent = asyncio.run(
        agent.parse_joint_intent(
            [ParticipantInput(name="A", address="A"), ParticipantInput(name="B", address="B")],
            venue_category="restaurant",
            shared_prompt="",
        )
    )

    ranked_fair = score_and_rank_venues(participants, venues, matrix, "fair", intent, "19:00")
    ranked_equal = score_and_rank_venues(participants, venues, matrix, "equal_time", intent, "19:00")
    ranked_fast = score_and_rank_venues(participants, venues, matrix, "fast_total", intent, "19:00")

    assert ranked_fair[0].venue.place_id == "v_balanced"
    assert ranked_equal[0].venue.place_id == "v_balanced"
    assert ranked_fast[0].venue.place_id == "v_skewed"


def test_heuristic_intent_negation_and_heterogeneous_modes():
    agent = MeetingLLMAgent(RuntimeConfig(llm_provider="disabled"))
    intent = asyncio.run(
        agent.parse_joint_intent(
            participants=[
                ParticipantInput(
                    id="p1", name="A", address="五道口", transport_mode="transit",
                    preference_note="不想吃火锅，不想吃太辣，地铁直达"
                ),
                ParticipantInput(
                    id="p2", name="B", address="望京", transport_mode="driving",
                    preference_note="想吃日料，开车好停车，安静聊天"
                ),
            ],
            venue_category="restaurant",
            shared_prompt="人均150",
        )
    )
    assert "日料" in intent.cuisine_tags
    assert "火锅" not in intent.cuisine_tags
    assert intent.need_parking is True
    assert intent.near_subway is True
    assert intent.quiet_vibe is True
    assert "150" in intent.budget_hint


def test_free_web_llm_cascade_chain_builder():
    # When no API key is configured, cascade includes web channels + zero-key Pollinations fallback
    cfg = RuntimeConfig(
        llm_provider="auto",
        llm_api_key="",
        kimi_web_token="eyJhbGciOiJIUzI1NiJ9.mock",
        doubao_web_token="mock_session",
    )
    cascade = FreeWebLLMCascade(cfg)
    chain = cascade._build_candidate_chain()
    labels = [c[0] for c in chain]
    assert any("Kimi 网页版直连" in l for l in labels)
    assert any("豆包网页版免费端口" in l for l in labels)
    assert any("Pollinations" in l for l in labels)
