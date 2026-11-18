from __future__ import annotations

import re
import socket
import time
import httpx
from pathlib import Path
from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from lets_meet.config import RuntimeConfig, parse_dotenv_file, update_dotenv_file
from lets_meet.geo import is_in_mainland_china, wgs84_to_gcj02
from lets_meet.llm.free_web import FreeWebLLMCascade
from lets_meet.models import (
    LatLng,
    PlaceSuggestRequest,
    RecommendRequest,
    RoomCreateRequest,
    RoomJoinOrUpdateRequest,
    RoomVoteRequest,
    RuntimeKeyOverrides,
)
from lets_meet.pipeline import run_recommendation
from lets_meet.providers import AmapProvider, GoogleMapsProvider, OpenStreetMapProvider, select_map_provider
from lets_meet.rooms import RoomStore

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = PROJECT_ROOT / "static"
TUNNEL_LOG = PROJECT_ROOT / "tools" / "tunnel.log"

room_store = RoomStore()


def _is_local_machine_request(request: Request) -> bool:
    """True ONLY for direct local requests (not proxied through Cloudflare Quick Tunnel or reverse proxies)."""
    if request.headers.get("cf-connecting-ip") or request.headers.get("cf-ray") or request.headers.get("x-forwarded-for"):
        return False
    client_host = (request.client.host if request.client else "") or ""
    return client_host in ("127.0.0.1", "::1", "localhost", "testclient")


def _mask_secret(val: str) -> str:
    if not val:
        return ""
    if len(val) <= 8:
        return val[:2] + "***"
    return f"{val[:4]}...{val[-4:]}"


def _detect_lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            if not ip.startswith(("127.", "198.18.")):
                return ip
    except Exception:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = str(info[4][0])
            if ip.startswith(("10.", "192.168.", "172.")):
                return ip
    except Exception:
        pass
    return "127.0.0.1"


def _detect_public_tunnel_url() -> str:
    if not TUNNEL_LOG.exists():
        return ""
    try:
        raw = TUNNEL_LOG.read_text(encoding="utf-8", errors="ignore")
        matches = re.findall(r"https://[a-z0-9-]+\.trycloudflare\.com", raw)
        return matches[-1] if matches else ""
    except Exception:
        return ""


async def health_endpoint(request: Request) -> JSONResponse:
    cfg = RuntimeConfig.load_live()
    return JSONResponse(
        {
            "status": "ok",
            "is_local_admin": _is_local_machine_request(request),
            "configured_provider": cfg.map_provider,
            "llm_provider": cfg.llm_provider,
            "has_google_key": bool(cfg.google_maps_api_key),
            "has_amap_key": bool(cfg.amap_api_key),
            "has_llm_key": bool(cfg.llm_api_key),
            "has_qwen_env": bool(cfg.qwen_env_key),
            "has_zhipu_free_env": bool(cfg.zhipu_env_key),
            "free_web_fallback_ready": True,
            "llm_model": cfg.llm_model,
            "public_https_url": _detect_public_tunnel_url(),
        }
    )


async def access_info_endpoint(request: Request) -> JSONResponse:
    port = request.url.port or 8780
    lan_ip = _detect_lan_ip()
    public_url = _detect_public_tunnel_url()
    return JSONResponse(
        {
            "public_https_url": public_url,
            "lan_url": f"http://{lan_ip}:{port}",
            "local_url": f"http://127.0.0.1:{port}",
            "is_local_admin": _is_local_machine_request(request),
        }
    )


async def place_suggest_endpoint(request: Request) -> JSONResponse:
    try:
        if request.method == "GET":
            qp = request.query_params
            q = (qp.get("q") or "").strip()
            if not q:
                return JSONResponse({"error": "请输入地点名称"}, status_code=400)
            bias = None
            if qp.get("lat") and qp.get("lng"):
                bias = LatLng(lat=float(qp["lat"]), lng=float(qp["lng"]))
            req = PlaceSuggestRequest(
                query=q,
                city=(qp.get("city") or "").strip(),
                bias_coords=bias,
                limit=int(qp.get("limit") or 8),
                runtime_keys=RuntimeKeyOverrides(map_provider=qp.get("map_provider")) if qp.get("map_provider") else None,
            )
        else:
            payload = await request.json()
            req = PlaceSuggestRequest.model_validate(payload)

        base_cfg = RuntimeConfig.load_live()
        overrides = req.runtime_keys.model_dump(exclude_none=True) if req.runtime_keys else None
        cfg = base_cfg.with_overrides(overrides)

        provider, warnings = select_map_provider(
            cfg,
            context_text=f"{req.city} {req.query}",
            known_coords=[req.bias_coords],
        )
        try:
            suggestions = await provider.suggest_places(
                req.query,
                city=req.city,
                bias_coords=req.bias_coords,
                limit=req.limit,
            )
        except Exception as exc:
            if provider.name in ("amap", "google"):
                warnings.append(f"{provider.name} 搜索异常（{exc}），已切换 OpenStreetMap 检索。")
                provider = OpenStreetMapProvider()
                suggestions = await provider.suggest_places(
                    req.query,
                    city=req.city,
                    bias_coords=req.bias_coords,
                    limit=req.limit,
                )
            else:
                raise

        return JSONResponse(
            {
                "active_provider": provider.name,
                "suggestions": [s.model_dump() for s in suggestions],
                "warnings": warnings,
            }
        )
    except ValidationError as exc:
        return JSONResponse({"error": "搜索参数错误", "details": exc.errors()}, status_code=422)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


async def reverse_geocode_endpoint(request: Request) -> JSONResponse:
    try:
        lat = float(request.query_params["lat"])
        lng = float(request.query_params["lng"])
    except (KeyError, ValueError):
        return JSONResponse({"error": "缺少有效经纬度 lat/lng"}, status_code=400)

    cfg = RuntimeConfig.load_live()
    in_cn = is_in_mainland_china(lat, lng)

    # 1. Amap reverse geocode when in Mainland China and key present
    if in_cn and cfg.amap_api_key:
        try:
            g_lat, g_lng = wgs84_to_gcj02(lat, lng)
            async with httpx.AsyncClient(timeout=6.0) as client:
                r = await client.get(
                    "https://restapi.amap.com/v3/geocode/regeo",
                    params={
                        "key": cfg.amap_api_key,
                        "location": f"{g_lng},{g_lat}",
                        "extensions": "all",
                        "radius": "300",
                    },
                )
                regeo = (r.json() if r.status_code == 200 else {}).get("regeocode") or {}
                pois = regeo.get("pois") or []
                fmt = str(regeo.get("formatted_address") or "").strip()
                if pois and pois[0].get("name"):
                    name = str(pois[0]["name"])
                    return JSONResponse({"name": name, "subtext": fmt or f"{lat:.5f}, {lng:.5f}", "provider": "amap"})
                if fmt:
                    return JSONResponse({"name": fmt, "subtext": f"{lat:.5f}, {lng:.5f}", "provider": "amap"})
        except Exception:
            pass

    # 2. Google reverse geocode when key present
    if cfg.google_maps_api_key and (not in_cn or not cfg.amap_api_key):
        try:
            async with httpx.AsyncClient(timeout=6.0) as client:
                r = await client.get(
                    "https://maps.googleapis.com/maps/api/geocode/json",
                    params={"latlng": f"{lat},{lng}", "key": cfg.google_maps_api_key, "language": "zh-CN"},
                )
                results = (r.json() if r.status_code == 200 else {}).get("results") or []
                if results:
                    fmt = results[0].get("formatted_address") or ""
                    comps = results[0].get("address_components") or []
                    short = comps[0].get("long_name") if comps else fmt
                    return JSONResponse({"name": short or fmt, "subtext": fmt, "provider": "google"})
        except Exception:
            pass

    # 3. Free Nominatim reverse geocode
    try:
        async with httpx.AsyncClient(
            timeout=6.0,
            headers={"User-Agent": "LetsMeetApp/0.4", "Accept-Language": "zh-CN,zh;q=0.95,en;q=0.8"},
        ) as client:
            r = await client.get(
                "https://nominatim.openstreetmap.org/reverse",
                params={"lat": lat, "lon": lng, "format": "jsonv2", "zoom": 18},
            )
            if r.status_code == 200:
                d = r.json()
                addr = d.get("address") or {}
                city_p = addr.get("city") or addr.get("municipality") or addr.get("state") or ""
                dist_p = addr.get("city_district") or addr.get("district") or addr.get("suburb") or ""
                road_p = addr.get("road") or addr.get("neighbourhood") or ""
                name = str(d.get("name") or road_p or f"地图选点 ({lat:.4f}, {lng:.4f})").strip()
                sub = " · ".join(x for x in (f"{city_p}{dist_p}", road_p) if x and x != name)
                return JSONResponse({"name": name, "subtext": sub or f"{lat:.5f}, {lng:.5f}", "provider": "osm"})
    except Exception:
        pass

    return JSONResponse({"name": f"地图选点 ({lat:.4f}, {lng:.4f})", "subtext": f"{lat:.5f}, {lng:.5f}", "provider": "coord"})


async def local_config_endpoint(request: Request) -> JSONResponse:
    if not _is_local_machine_request(request):
        return JSONResponse({"error": "仅允许在本机 (127.0.0.1 / localhost) 读取或写入 .env 配置"}, status_code=403)

    if request.method == "GET":
        fv = parse_dotenv_file()
        cfg = RuntimeConfig.load_live()
        return JSONResponse(
            {
                "is_local_admin": True,
                "map_provider": cfg.map_provider,
                "llm_provider": cfg.llm_provider,
                "llm_base_url": cfg.llm_base_url,
                "llm_model": cfg.llm_model,
                "amap_masked": _mask_secret(fv.get("AMAP_API_KEY", "")),
                "google_masked": _mask_secret(fv.get("GOOGLE_MAPS_API_KEY", "")),
                "llm_masked": _mask_secret(fv.get("LLM_API_KEY", "")),
                "has_amap_key": bool(cfg.amap_api_key),
                "has_google_key": bool(cfg.google_maps_api_key),
                "has_llm_key": bool(cfg.llm_api_key),
            }
        )

    payload = await request.json()
    updates: dict[str, str] = {}
    field_map = {
        "map_provider": "MAP_PROVIDER",
        "amap_api_key": "AMAP_API_KEY",
        "google_maps_api_key": "GOOGLE_MAPS_API_KEY",
        "llm_provider": "LLM_PROVIDER",
        "llm_api_key": "LLM_API_KEY",
        "llm_base_url": "LLM_BASE_URL",
        "llm_model": "LLM_MODEL",
        "free_web_gateway_url": "FREE_WEB_GATEWAY_URL",
        "kimi_web_token": "KIMI_WEB_TOKEN",
    }
    for req_key, env_key in field_map.items():
        if req_key in payload and payload[req_key] is not None:
            val = str(payload[req_key]).strip()
            # Only overwrite secret keys when non-empty or explicitly cleared via clear_keys=True
            if req_key.endswith(("_key", "_token")) and val == "" and not payload.get("allow_empty_secrets"):
                continue
            updates[env_key] = val

    update_dotenv_file(updates)
    cfg = RuntimeConfig.load_live()
    return JSONResponse(
        {
            "saved": True,
            "updated_keys": list(updates.keys()),
            "has_amap_key": bool(cfg.amap_api_key),
            "has_google_key": bool(cfg.google_maps_api_key),
            "has_llm_key": bool(cfg.llm_api_key),
            "map_provider": cfg.map_provider,
            "llm_model": cfg.llm_model,
        }
    )


async def verify_keys_endpoint(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        body = {}

    overrides = body.get("runtime_keys") if isinstance(body.get("runtime_keys"), dict) else {}
    cfg = RuntimeConfig.load_live().with_overrides(overrides)

    report: dict[str, dict] = {}

    # 1. Probe Amap Web Service Key
    if cfg.amap_api_key:
        t0 = time.perf_counter()
        try:
            cands = await AmapProvider(cfg.amap_api_key).suggest_places("人民广场", city="上海", limit=2)
            ms = int((time.perf_counter() - t0) * 1000)
            sample = f"{cands[0].name}（{cands[0].subtext}）" if cands else "无返回"
            report["amap"] = {"configured": True, "ok": bool(cands), "latency_ms": ms, "detail": f"已连通：{sample}"}
        except Exception as exc:
            report["amap"] = {"configured": True, "ok": False, "detail": str(exc)}
    else:
        report["amap"] = {"configured": False, "ok": False, "detail": "未填写 AMAP_API_KEY"}

    # 2. Probe Google Maps Key
    if cfg.google_maps_api_key:
        t0 = time.perf_counter()
        try:
            cands = await GoogleMapsProvider(cfg.google_maps_api_key).suggest_places("Ferry Building", city="San Francisco", limit=2)
            ms = int((time.perf_counter() - t0) * 1000)
            sample = f"{cands[0].name}（{cands[0].subtext}）" if cands else "无返回"
            report["google"] = {"configured": True, "ok": bool(cands), "latency_ms": ms, "detail": f"已连通：{sample}"}
        except Exception as exc:
            report["google"] = {"configured": True, "ok": False, "detail": str(exc)}
    else:
        report["google"] = {"configured": False, "ok": False, "detail": "未填写 GOOGLE_MAPS_API_KEY"}

    # 3. Probe LLM Channel
    t0 = time.perf_counter()
    out, src = await FreeWebLLMCascade(cfg).chat_completion("只输出纯JSON", '输出 {"ok":1}', timeout=15.0)
    ms = int((time.perf_counter() - t0) * 1000)
    report["llm"] = {
        "configured": bool(cfg.llm_api_key),
        "ok": bool(out),
        "latency_ms": ms,
        "detail": f"已连通：{src}" if out else "模型调用失败，请检查 Key 或网络",
    }

    return JSONResponse(report)


async def recommend_endpoint(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
        req = RecommendRequest.model_validate(payload)
        cfg = RuntimeConfig.load_live()
        result = await run_recommendation(req, cfg)
        return JSONResponse(result.model_dump())
    except ValidationError as exc:
        return JSONResponse({"error": "请求参数校验失败", "details": exc.errors()}, status_code=422)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception as exc:
        return JSONResponse({"error": f"推荐计算异常：{exc}"}, status_code=500)


async def create_room_endpoint(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
        req = RoomCreateRequest.model_validate(payload)
        state = room_store.create(req)
        return JSONResponse(state.model_dump(), status_code=201)
    except ValidationError as exc:
        return JSONResponse({"error": "创建房间参数错误", "details": exc.errors()}, status_code=422)


async def get_room_endpoint(request: Request) -> JSONResponse:
    code = request.path_params["code"]
    room = room_store.get(code)
    if room is None:
        return JSONResponse({"error": f"未找到房间 {code}"}, status_code=404)
    return JSONResponse(room.model_dump())


async def join_room_endpoint(request: Request) -> JSONResponse:
    code = request.path_params["code"]
    try:
        payload = await request.json()
        req = RoomJoinOrUpdateRequest.model_validate(payload)
        room = room_store.upsert_participant(
            code=code,
            participant=req.participant,
            city=req.city,
            venue_category=req.venue_category,
            shared_prompt=req.shared_prompt,
            fairness_mode=req.fairness_mode,
            meetup_time=req.meetup_time,
        )
        return JSONResponse(room.model_dump())
    except KeyError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    except ValidationError as exc:
        return JSONResponse({"error": "参数错误", "details": exc.errors()}, status_code=422)


async def compute_room_endpoint(request: Request) -> JSONResponse:
    code = request.path_params["code"]
    room = room_store.get(code)
    if room is None:
        return JSONResponse({"error": f"未找到房间 {code}"}, status_code=404)
    if len(room.participants) < 2:
        return JSONResponse({"error": "至少需要 2 位参与者加入房间后才能计算中间点"}, status_code=400)

    try:
        body = await request.json()
    except Exception:
        body = {}
    runtime_keys = RuntimeKeyOverrides.model_validate(body["runtime_keys"]) if body.get("runtime_keys") else None

    rec_req = RecommendRequest(
        participants=room.participants,
        city=room.city,
        venue_category=room.venue_category,
        shared_prompt=room.shared_prompt,
        fairness_mode=room.fairness_mode,
        meetup_time=room.meetup_time,
        runtime_keys=runtime_keys,
    )
    try:
        cfg = RuntimeConfig.load_live()
        result = await run_recommendation(rec_req, cfg)
        updated = room_store.save_result(code, result)
        return JSONResponse(updated.model_dump())
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


async def vote_room_endpoint(request: Request) -> JSONResponse:
    code = request.path_params["code"]
    try:
        payload = await request.json()
        req = RoomVoteRequest.model_validate(payload)
        updated = room_store.toggle_vote(code, req.participant_id, req.place_id)
        return JSONResponse(updated.model_dump())
    except KeyError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    except ValidationError as exc:
        return JSONResponse({"error": "投票参数错误", "details": exc.errors()}, status_code=422)


async def index_page(_request: Request) -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})


async def manifest_file(_request: Request) -> FileResponse:
    return FileResponse(STATIC_DIR / "manifest.json", media_type="application/manifest+json")


async def sw_file(_request: Request) -> FileResponse:
    return FileResponse(
        STATIC_DIR / "sw.js",
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache"},
    )


routes = [
    Route("/", index_page, methods=["GET"]),
    Route("/manifest.json", manifest_file, methods=["GET"]),
    Route("/sw.js", sw_file, methods=["GET"]),
    Route("/api/health", health_endpoint, methods=["GET"]),
    Route("/api/access-info", access_info_endpoint, methods=["GET"]),
    Route("/api/places/suggest", place_suggest_endpoint, methods=["GET", "POST"]),
    Route("/api/places/reverse", reverse_geocode_endpoint, methods=["GET"]),
    Route("/api/local-config", local_config_endpoint, methods=["GET", "POST"]),
    Route("/api/verify-keys", verify_keys_endpoint, methods=["POST"]),
    Route("/api/recommend", recommend_endpoint, methods=["POST"]),
    Route("/api/rooms", create_room_endpoint, methods=["POST"]),
    Route("/api/rooms/{code}", get_room_endpoint, methods=["GET"]),
    Route("/api/rooms/{code}/join", join_room_endpoint, methods=["POST"]),
    Route("/api/rooms/{code}/compute", compute_room_endpoint, methods=["POST"]),
    Route("/api/rooms/{code}/vote", vote_room_endpoint, methods=["POST"]),
    Mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static"),
]

app = Starlette(
    debug=False,
    routes=routes,
    middleware=[
        Middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        )
    ],
)


if __name__ == "__main__":
    import os
    import uvicorn

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8780"))
    uvicorn.run("lets_meet.app:app", host=host, port=port, reload=False)
