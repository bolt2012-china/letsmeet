import asyncio
import httpx
from lets_meet.app import app


async def _run_api_flow():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        # 1. Health check & static PWA assets
        health = await client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"
        assert health.json()["free_web_fallback_ready"] is True

        index_resp = await client.get("/")
        assert index_resp.status_code == 200
        assert "折中" in index_resp.text or "Let's Meet" in index_resp.text

        # 2. Deterministic offline demo flow
        rec_resp = await client.post(
            "/api/recommend",
            json={
                "participants": [
                    {
                        "id": "p1",
                        "name": "我",
                        "address": "五道口",
                        "transport_mode": "transit",
                        "preference_note": "不吃辣，地铁方便",
                    },
                    {
                        "id": "p2",
                        "name": "朋友",
                        "address": "望京SOHO",
                        "transport_mode": "driving",
                        "preference_note": "日料，好停车",
                    },
                ],
                "venue_category": "japanese",
                "fairness_mode": "fair",
                "meetup_time": "19:00",
                "shared_prompt": "人均150，安静适合聊天",
                "runtime_keys": {"map_provider": "demo", "llm_provider": "disabled"},
            },
        )
        assert rec_resp.status_code == 200
        rec_data = rec_resp.json()
        assert len(rec_data["recommendations"]) >= 3
        top = rec_data["recommendations"][0]
        assert len(top["legs"]) == 2
        assert "amap" in top["legs"][0]["navigation_links"]
        assert "google_maps" in top["legs"][0]["navigation_links"]

        # 2b. Station / Landmark non-restaurant meetup flow
        st_resp = await client.post(
            "/api/recommend",
            json={
                "participants": [
                    {"id": "p1", "name": "A", "address": "北京大学", "transport_mode": "transit"},
                    {"id": "p2", "name": "B", "address": "望京SOHO", "transport_mode": "transit"},
                ],
                "venue_category": "station",
                "fairness_mode": "equal_time",
                "shared_prompt": "地铁换乘站汇合",
                "runtime_keys": {"map_provider": "demo", "llm_provider": "disabled"},
            },
        )
        assert st_resp.status_code == 200
        st_venues = st_resp.json()["recommendations"]
        assert any("站" in item["venue"]["name"] for item in st_venues)

        # 3. Collaborative Room creation -> Friend join -> Compute -> Vote
        create_resp = await client.post(
            "/api/rooms",
            json={
                "title": "周五约饭",
                "venue_category": "restaurant",
                "fairness_mode": "fair",
                "meetup_time": "18:30",
                "shared_prompt": "Ramen or Italian",
                "host": {
                    "id": "p1",
                    "name": "Alex",
                    "address": "Mission District",
                    "transport_mode": "transit",
                },
            },
        )
        assert create_resp.status_code == 201
        code = create_resp.json()["room_code"]

        join_resp = await client.post(
            f"/api/rooms/{code}/join",
            json={
                "participant": {
                    "id": "p2",
                    "name": "Sam",
                    "address": "Marina",
                    "transport_mode": "driving",
                    "preference_note": "ramen, garage parking",
                }
            },
        )
        assert join_resp.status_code == 200

        comp_resp = await client.post(
            f"/api/rooms/{code}/compute",
            json={"runtime_keys": {"map_provider": "demo", "llm_provider": "disabled"}},
        )
        assert comp_resp.status_code == 200
        comp_room = comp_resp.json()
        first_place_id = comp_room["last_result"]["recommendations"][0]["venue"]["place_id"]

        vote_resp = await client.post(
            f"/api/rooms/{code}/vote",
            json={"participant_id": "p2", "place_id": first_place_id},
        )
        assert vote_resp.status_code == 200
        assert "p2" in vote_resp.json()["votes"][first_place_id]

        # 4. Interactive Place Suggest + Localhost-only .env protection
        sug_resp = await client.post(
            "/api/places/suggest",
            json={
                "query": "五道口",
                "city": "北京",
                "runtime_keys": {"map_provider": "demo"},
            },
        )
        assert sug_resp.status_code == 200
        sugs = sug_resp.json()["suggestions"]
        assert len(sugs) >= 1
        assert "coords" in sugs[0] and "subtext" in sugs[0]

        # External tunnel requests (with CF-Connecting-IP) must be blocked from /api/local-config
        blocked_cfg = await client.get("/api/local-config", headers={"cf-connecting-ip": "203.0.113.9"})
        assert blocked_cfg.status_code == 403

        # Direct local requests are allowed
        local_cfg = await client.get("/api/local-config")
        assert local_cfg.status_code == 200
        assert local_cfg.json()["is_local_admin"] is True


def test_full_api_and_room_collaboration():
    asyncio.run(_run_api_flow())
