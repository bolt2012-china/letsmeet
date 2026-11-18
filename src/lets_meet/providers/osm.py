from __future__ import annotations

import asyncio
import math
import httpx
from lets_meet.geo import URBAN_SPEED_KMH, haversine_km, resolve_city_center
from lets_meet.models import LatLng, PlaceSuggestion, ResolvedParticipant, VenueCandidate
from lets_meet.providers.base import MapProvider

_HEADERS = {
    "User-Agent": "LetsMeetApp/0.4 (https://localhost; transit-landmark-midpoint)",
    "Accept-Language": "zh-CN,zh;q=0.95,en;q=0.8",
}

_GEOCODE_CACHE: dict[str, tuple[LatLng, str]] = {}
_SUGGEST_CACHE: dict[str, list[PlaceSuggestion]] = {}
_POI_CACHE: dict[str, list[VenueCandidate]] = {}

_CATEGORY_TO_NOMINATIM_QUERIES: dict[str, list[str]] = {
    "any": ["subway", "mall", "park", "cafe"],
    "station": ["subway", "station"],
    "landmark": ["mall", "park", "plaza", "library"],
    "cafe": ["cafe", "bakery"],
    "restaurant": ["restaurant", "mall"],
    "japanese": ["restaurant", "mall"],
    "hotpot": ["restaurant", "mall"],
    "western": ["restaurant", "cafe"],
    "bar": ["bar", "pub"],
}


def _classify_osm_place(q: str, name: str, osm_type_tag: str) -> tuple[str, str, str]:
    """Return (category_label, subway_hint, parking_hint) in plain language."""
    if q in ("subway", "station") or osm_type_tag in ("station", "subway_entrance", "halt", "tram_stop") or any(
        k in name for k in ("地铁", "站", "Station", "BART", "Caltrain")
    ):
        return "轨道车站", "站内或出入口碰头，换乘进出方便", ""
    if q == "park" or osm_type_tag in ("park", "garden") or any(k in name for k in ("公园", "绿地", "Park", "Square")):
        return "公园广场", "地面开阔地标，碰面好认", ""
    if q in ("mall", "plaza") or any(
        k in name for k in ("商城", "广场", "购物中心", "天地", "城", "荟", "中心", "MALL", "Mall", "Plaza")
    ):
        return "商业地标", "紧邻商圈主干道与地铁口", "配套地下车库"
    if q == "library" or "图书馆" in name or "博物馆" in name:
        return "公共场馆", "安静公共地标，适合直接碰头", ""
    if q in ("cafe", "bakery"):
        return "咖啡茶饮", "", ""
    return "餐饮门店", "", ""


class OpenStreetMapProvider(MapProvider):
    """
    Keyless global map provider built on OpenStreetMap:
      - Nominatim (addressdetails=1) + Photon (location-biased) for interactive disambiguated place suggestions
      - Nominatim bounded viewbox for stations, plazas, parks, malls, cafes, and restaurants
      - OSRM /table/v1 for road distances and travel times
    """

    name = "osm"

    async def suggest_places(
        self,
        query: str,
        city: str = "",
        bias_coords: LatLng | None = None,
        limit: int = 8,
    ) -> list[PlaceSuggestion]:
        raw = query.strip()
        if "," in raw:
            parts = raw.split(",", 1)
            try:
                lat, lng = float(parts[0].strip()), float(parts[1].strip())
                if -90 <= lat <= 90 and -180 <= lng <= 180:
                    pt = LatLng(lat=lat, lng=lng)
                    return [
                        PlaceSuggestion(
                            place_id=f"coord_{lat:.5f}_{lng:.5f}",
                            name=f"{lat:.5f}, {lng:.5f}",
                            subtext="直接输入经纬度坐标 (WGS-84)",
                            coords=pt,
                            provider="osm",
                        )
                    ]
            except ValueError:
                pass

        eff_bias = bias_coords or resolve_city_center(city)
        search_q = f"{city} {raw}".strip() if city and city not in raw else raw
        bias_key = f"{eff_bias.lat:.2f},{eff_bias.lng:.2f}" if eff_bias else "none"
        cache_key = f"{search_q.lower()}|{bias_key}|{limit}"
        if cache_key in _SUGGEST_CACHE:
            return _SUGGEST_CACHE[cache_key]

        raw_items: list[PlaceSuggestion] = []

        async with httpx.AsyncClient(timeout=9.0, headers=_HEADERS) as client:
            nom_params: dict[str, str | int] = {
                "q": search_q,
                "format": "jsonv2",
                "addressdetails": 1,
                "limit": 10,
            }
            photon_params: dict[str, str | int | float] = {
                "q": search_q,
                "limit": 8,
            }
            if eff_bias:
                photon_params["lat"] = eff_bias.lat
                photon_params["lon"] = eff_bias.lng

            nom_task = client.get("https://nominatim.openstreetmap.org/search", params=nom_params)
            pho_task = client.get("https://photon.komoot.io/api/", params=photon_params)
            nom_resp, pho_resp = await asyncio.gather(nom_task, pho_task, return_exceptions=True)

            if not isinstance(nom_resp, Exception) and nom_resp.status_code == 200:
                for item in nom_resp.json() or []:
                    lat = round(float(item["lat"]), 6)
                    lng = round(float(item["lon"]), 6)
                    addr = item.get("address") or {}
                    city_part = (
                        addr.get("city")
                        or addr.get("municipality")
                        or addr.get("state")
                        or addr.get("province")
                        or ""
                    )
                    dist_part = (
                        addr.get("city_district")
                        or addr.get("district")
                        or addr.get("borough")
                        or addr.get("county")
                        or addr.get("suburb")
                        or ""
                    )
                    road_part = addr.get("road") or addr.get("neighbourhood") or addr.get("quarter") or ""
                    country = addr.get("country") or ""

                    disp_tokens = [s.strip() for s in str(item.get("display_name") or "").split(",") if s.strip()]
                    name = str(item.get("name") or (disp_tokens[0] if disp_tokens else raw)).strip()
                    region_str = "".join(dict.fromkeys(x for x in (city_part, dist_part) if x))
                    sub_parts = [x for x in (region_str or country, road_part) if x and x != name]
                    if not sub_parts and len(disp_tokens) > 1:
                        sub_parts = disp_tokens[1:4]

                    osm_type = str(item.get("type") or "")
                    cat_label, _, _ = _classify_osm_place("", name, osm_type)
                    raw_items.append(
                        PlaceSuggestion(
                            place_id=f"osm_{item.get('osm_type', 'n')}_{item.get('osm_id', '')}",
                            name=name,
                            subtext=" · ".join(sub_parts) or str(item.get("display_name") or ""),
                            city=city_part,
                            category=cat_label if osm_type not in ("administrative", "city", "yes") else "",
                            coords=LatLng(lat=lat, lng=lng),
                            provider="osm",
                        )
                    )

            if not isinstance(pho_resp, Exception) and pho_resp.status_code == 200:
                for feat in (pho_resp.json().get("features") or []):
                    lon, lat = feat["geometry"]["coordinates"]
                    props = feat.get("properties") or {}
                    name = str(props.get("name") or "").strip()
                    if not name:
                        continue
                    city_part = str(props.get("city") or props.get("state") or "").strip()
                    dist_part = str(props.get("district") or props.get("county") or "").strip()
                    street = str(props.get("street") or props.get("locality") or "").strip()
                    country = str(props.get("country") or "").strip()
                    region_str = "".join(dict.fromkeys(x for x in (city_part, dist_part) if x))
                    sub_parts = [x for x in (region_str or country, street) if x and x != name]
                    raw_items.append(
                        PlaceSuggestion(
                            place_id=f"photon_{props.get('osm_type', 'N')}_{props.get('osm_id', '')}",
                            name=name,
                            subtext=" · ".join(sub_parts),
                            city=city_part,
                            coords=LatLng(lat=round(float(lat), 6), lng=round(float(lon), 6)),
                            provider="osm",
                        )
                    )

        # Deduplicate close-by pins (< 120m with same name)
        deduped: list[PlaceSuggestion] = []
        for cand in raw_items:
            if any(
                cand.name == ex.name and haversine_km(cand.coords, ex.coords) < 0.12
                for ex in deduped
            ):
                continue
            deduped.append(cand)

        clean_city = city.strip().rstrip("市")

        def _score_cand(c: PlaceSuggestion) -> tuple[int, int, float]:
            city_miss = 0
            if clean_city:
                city_miss = 0 if clean_city in (c.city + c.subtext + c.name) else 1
            name_miss = 0 if (raw.lower() in c.name.lower() or c.name.lower() in raw.lower()) else 1
            dist_penalty = 0.0
            if eff_bias:
                d = haversine_km(eff_bias, c.coords)
                dist_penalty = 0.0 if d <= 38.0 else d
            return (city_miss, name_miss, dist_penalty)

        deduped.sort(key=_score_cand)
        out = deduped[:limit]
        if out:
            _SUGGEST_CACHE[cache_key] = out
        return out

    async def geocode(
        self,
        address: str,
        city: str = "",
        bias_coords: LatLng | None = None,
    ) -> tuple[LatLng, str]:
        bias_key = f"{bias_coords.lat:.2f},{bias_coords.lng:.2f}" if bias_coords else "none"
        cache_key = f"{city}|{address.strip().lower()}|{bias_key}"
        if cache_key in _GEOCODE_CACHE:
            return _GEOCODE_CACHE[cache_key]

        cands = await self.suggest_places(address, city=city, bias_coords=bias_coords, limit=5)
        if not cands:
            raise ValueError(f"没找到「{address}」，请点击输入框旁的「搜地点」从列表选定或直接在地图选点")
        top = cands[0]
        formatted = f"{top.name}（{top.subtext}）" if top.subtext else top.name
        res = (top.coords, formatted)
        _GEOCODE_CACHE[cache_key] = res
        return res

    async def search_places(
        self,
        center: LatLng,
        radius_m: int,
        keywords: list[str],
        category: str = "any",
        city: str = "",
    ) -> list[VenueCandidate]:
        eff_km = max(1.8, min(9.5, (radius_m / 1000.0) * 1.25))
        d_lat = eff_km / 111.0
        d_lng = eff_km / max(25.0, 111.0 * abs(math.cos(math.radians(center.lat))))
        west = round(center.lng - d_lng, 4)
        east = round(center.lng + d_lng, 4)
        north = round(center.lat + d_lat, 4)
        south = round(center.lat - d_lat, 4)

        q_terms = list(_CATEGORY_TO_NOMINATIM_QUERIES.get(category, ["subway", "mall", "cafe"]))
        kw_joined = " ".join(keywords).lower()
        if any(k in kw_joined for k in ("站", "地铁", "火车", "高铁", "station", "subway")) and "station" not in q_terms:
            q_terms.insert(0, "station")
        if any(k in kw_joined for k in ("公园", "散步", "绿地", "park")) and "park" not in q_terms:
            q_terms.insert(0, "park")
        if any(k in kw_joined for k in ("图书馆", "书店", "博物馆", "library")) and "library" not in q_terms:
            q_terms.insert(0, "library")

        selected_queries = q_terms[:2]
        cache_key = f"{west},{north},{east},{south}:{','.join(selected_queries)}"
        if cache_key in _POI_CACHE:
            return _POI_CACHE[cache_key]

        buckets: list[list[VenueCandidate]] = []
        async with httpx.AsyncClient(timeout=10.0, headers=_HEADERS) as client:
            for idx, q in enumerate(selected_queries):
                if idx > 0:
                    await asyncio.sleep(1.05)
                try:
                    resp = await client.get(
                        "https://nominatim.openstreetmap.org/search",
                        params={
                            "q": q,
                            "format": "jsonv2",
                            "viewbox": f"{west},{north},{east},{south}",
                            "bounded": 1,
                            "limit": 12,
                        },
                    )
                    if resp.status_code != 200:
                        continue
                    rows = resp.json() or []
                except Exception:
                    continue

                bucket: list[VenueCandidate] = []
                for r in rows:
                    name = (r.get("name") or "").strip()
                    if not name:
                        continue
                    if "食堂" in name and not any("食堂" in k for k in keywords):
                        continue
                    osm_type_tag = str(r.get("type") or "")
                    if osm_type_tag == "bus_stop":
                        continue

                    place_id = f"osm_{r.get('osm_type', 'n')}_{r.get('osm_id', name)}"
                    lat = round(float(r["lat"]), 6)
                    lng = round(float(r["lon"]), 6)
                    disp_parts = [s.strip() for s in (r.get("display_name") or "").split(",") if s.strip()]
                    short_addr = " · ".join(disp_parts[1:4]) if len(disp_parts) > 1 else name

                    cat_label, subway_hint, parking_hint = _classify_osm_place(q, name, osm_type_tag)
                    is_hub = cat_label in ("轨道车站", "商业地标", "公园广场")

                    bucket.append(
                        VenueCandidate(
                            place_id=place_id,
                            name=name,
                            address=short_addr,
                            coords=LatLng(lat=lat, lng=lng),
                            rating=4.5 if is_hub else 4.3,
                            review_count=550 if is_hub else 180,
                            price_level="",
                            categories=[cat_label],
                            open_now=True,
                            highlights=[],
                            subway_hint=subway_hint,
                            parking_hint=parking_hint,
                            provider="osm",
                        )
                    )
                if bucket:
                    buckets.append(bucket)

        found: dict[str, VenueCandidate] = {}
        max_len = max((len(b) for b in buckets), default=0)
        for i in range(max_len):
            for b in buckets:
                if i < len(b) and b[i].place_id not in found:
                    found[b[i].place_id] = b[i]

        result = list(found.values())
        if result:
            _POI_CACHE[cache_key] = result
        return result

    async def compute_travel_matrix(
        self,
        participants: list[ResolvedParticipant],
        venues: list[VenueCandidate],
        city: str = "",
    ) -> list[list[tuple[float, float, str]]]:
        if not venues:
            return [[] for _ in participants]

        n_orig = len(participants)
        all_pts = [p.coords for p in participants] + [v.coords for v in venues]
        coords_str = ";".join(f"{pt.lng},{pt.lat}" for pt in all_pts)
        sources_idx = ";".join(str(i) for i in range(n_orig))
        dests_idx = ";".join(str(n_orig + j) for j in range(len(venues)))

        osrm_durations: list[list[float | None]] | None = None
        osrm_distances: list[list[float | None]] | None = None

        try:
            async with httpx.AsyncClient(timeout=9.0, headers=_HEADERS) as client:
                resp = await client.get(
                    f"https://router.project-osrm.org/table/v1/driving/{coords_str}",
                    params={
                        "sources": sources_idx,
                        "destinations": dests_idx,
                        "annotations": "duration,distance",
                    },
                )
                if resp.status_code == 200:
                    body = resp.json()
                    if body.get("code") == "Ok":
                        osrm_durations = body.get("durations")
                        osrm_distances = body.get("distances")
        except Exception:
            pass

        matrix: list[list[tuple[float, float, str]]] = []
        for i, p in enumerate(participants):
            row: list[tuple[float, float, str]] = []
            mode = p.transport_mode
            for j, v in enumerate(venues):
                road_km: float | None = None
                drive_min: float | None = None
                if osrm_distances and i < len(osrm_distances) and osrm_distances[i][j] is not None:
                    road_km = max(0.2, float(osrm_distances[i][j]) / 1000.0)
                if osrm_durations and i < len(osrm_durations) and osrm_durations[i][j] is not None:
                    drive_min = max(2.0, float(osrm_durations[i][j]) / 60.0)

                if road_km is None:
                    road_km = max(0.3, haversine_km(p.coords, v.coords) * 1.32)
                if drive_min is None:
                    drive_min = (road_km / URBAN_SPEED_KMH["driving"]) * 60.0

                is_station = "轨道车站" in v.categories
                if mode == "driving":
                    dur = drive_min * 1.15 + (2.5 if v.parking_hint else 4.5)
                    summary = f"驾车 {road_km:.1f} km"
                elif mode == "transit":
                    hub_factor = 0.85 if is_station else (0.92 if v.parking_hint else 1.05)
                    dur = 7.0 + (road_km / URBAN_SPEED_KMH["transit"]) * 60.0 * hub_factor
                    summary = f"公交地铁 {road_km:.1f} km"
                elif mode == "cycling":
                    dur = 1.5 + (road_km / URBAN_SPEED_KMH["cycling"]) * 60.0
                    summary = f"骑行 {road_km:.1f} km"
                else:
                    walk_km = max(0.2, haversine_km(p.coords, v.coords) * 1.20)
                    dur = (walk_km / URBAN_SPEED_KMH["walking"]) * 60.0
                    road_km = walk_km
                    summary = f"步行 {walk_km:.1f} km"

                row.append((round(max(3.0, dur), 1), round(road_km, 2), summary))
            matrix.append(row)
        return matrix
