from __future__ import annotations

import asyncio
import httpx
from lets_meet.geo import (
    gcj02_to_wgs84,
    haversine_km,
    infer_amap_city_code,
    is_in_mainland_china,
    resolve_city_center,
    wgs84_to_gcj02,
)
from lets_meet.models import LatLng, PlaceSuggestion, ResolvedParticipant, VenueCandidate
from lets_meet.providers.base import MapProvider


def _raise_if_amap_key_error(data: dict) -> None:
    if str(data.get("status", "1")) != "0":
        return
    code = str(data.get("infocode", ""))
    info = str(data.get("info", "UNKNOWN"))
    if code == "10009" or "PLAT_NOMATCH" in info:
        raise ValueError(
            "高德 Key 平台类型不匹配（USERKEY_PLAT_NOMATCH）：请在高德开放平台创建「Web服务」类型的 Key，不能使用「Web端(JS API)」Key。"
        )
    if code in ("10001", "10004") or "INVALID_USER_KEY" in info:
        raise ValueError(f"高德 AMAP_API_KEY 无效或未生效（{info}）。")
    if code == "10003" or "OVER_LIMIT" in info:
        raise ValueError(f"高德 API 配额已超限（{info}）。")


def _parse_raw_latlng(raw: str) -> LatLng | None:
    if "," not in raw:
        return None
    parts = raw.split(",", 1)
    try:
        lat, lng = float(parts[0].strip()), float(parts[1].strip())
        if -90 <= lat <= 90 and -180 <= lng <= 180:
            return LatLng(lat=lat, lng=lng)
    except ValueError:
        pass
    return None


class AmapProvider(MapProvider):
    """
    高德开放平台 (Amap Web Service API) adapter.
    Boundary invariant:
      - All Amap HTTP requests send GCJ-02 ("lng,lat").
      - All returned LatLng objects are converted back to canonical WGS-84.
    """

    name = "amap"

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key.strip()

    async def suggest_places(
        self,
        query: str,
        city: str = "",
        bias_coords: LatLng | None = None,
        limit: int = 8,
    ) -> list[PlaceSuggestion]:
        raw = query.strip()
        parsed_pt = _parse_raw_latlng(raw)
        if parsed_pt:
            return [
                PlaceSuggestion(
                    place_id=f"coord_{parsed_pt.lat:.5f}_{parsed_pt.lng:.5f}",
                    name=f"{parsed_pt.lat:.5f}, {parsed_pt.lng:.5f}",
                    subtext="直接输入经纬度坐标 (WGS-84)",
                    coords=parsed_pt,
                    provider="amap",
                )
            ]

        eff_bias = bias_coords or resolve_city_center(city)
        loc_param = ""
        if eff_bias and is_in_mainland_china(eff_bias.lat, eff_bias.lng):
            g_lat, g_lng = wgs84_to_gcj02(eff_bias.lat, eff_bias.lng)
            loc_param = f"{g_lng},{g_lat}"

        suggestions: list[PlaceSuggestion] = []
        seen_keys: set[str] = set()

        async with httpx.AsyncClient(timeout=9.0) as client:
            tips_params: dict[str, str] = {
                "key": self.api_key,
                "keywords": raw,
                "datatype": "all",
            }
            if city:
                tips_params["city"] = city
            if loc_param:
                tips_params["location"] = loc_param

            poi_params: dict[str, str] = {
                "key": self.api_key,
                "keywords": raw,
                "offset": str(min(12, max(6, limit))),
                "page": "1",
                "extensions": "base",
            }
            if city:
                poi_params["city"] = city

            tips_task = client.get("https://restapi.amap.com/v3/assistant/inputtips", params=tips_params)
            poi_task = client.get("https://restapi.amap.com/v3/place/text", params=poi_params)
            tips_resp, poi_resp = await asyncio.gather(tips_task, poi_task, return_exceptions=True)

            if not isinstance(tips_resp, Exception) and tips_resp.status_code == 200:
                t_data = tips_resp.json()
                _raise_if_amap_key_error(t_data)
                for tip in t_data.get("tips") or []:
                    loc = tip.get("location")
                    if not isinstance(loc, str) or "," not in loc:
                        continue
                    lng_s, lat_s = loc.split(",", 1)
                    w_lat, w_lng = gcj02_to_wgs84(float(lat_s), float(lng_s))
                    name = str(tip.get("name") or "").strip()
                    district = str(tip.get("district") or "").strip()
                    addr = tip.get("address")
                    addr_str = str(addr).strip() if isinstance(addr, str) else ""
                    sub_parts = [x for x in (district, addr_str) if x]
                    key = str(tip.get("id") or f"{name}_{w_lat:.4f}_{w_lng:.4f}")
                    if key in seen_keys or not name:
                        continue
                    seen_keys.add(key)
                    suggestions.append(
                        PlaceSuggestion(
                            place_id=f"amap_{key}",
                            name=name,
                            subtext=" · ".join(sub_parts) or "高德地图地点",
                            city=district,
                            coords=LatLng(lat=w_lat, lng=w_lng),
                            provider="amap",
                        )
                    )

            if not isinstance(poi_resp, Exception) and poi_resp.status_code == 200:
                p_data = poi_resp.json()
                _raise_if_amap_key_error(p_data)
                for p in p_data.get("pois") or []:
                    loc = p.get("location")
                    if not isinstance(loc, str) or "," not in loc:
                        continue
                    lng_s, lat_s = loc.split(",", 1)
                    w_lat, w_lng = gcj02_to_wgs84(float(lat_s), float(lng_s))
                    name = str(p.get("name") or "").strip()
                    pname = str(p.get("pname") or "").strip()
                    cname = str(p.get("cityname") or "").strip()
                    adname = str(p.get("adname") or "").strip()
                    addr = p.get("address")
                    addr_str = str(addr).strip() if isinstance(addr, str) else ""
                    region = "".join(dict.fromkeys(x for x in (pname, cname, adname) if x))
                    type_tail = str(p.get("type") or "").split(";")[-1]
                    sub_parts = [x for x in (region, addr_str or type_tail) if x]
                    key = str(p.get("id") or f"{name}_{w_lat:.4f}_{w_lng:.4f}")
                    if key in seen_keys or not name:
                        continue
                    seen_keys.add(key)
                    suggestions.append(
                        PlaceSuggestion(
                            place_id=f"amap_{key}",
                            name=name,
                            subtext=" · ".join(sub_parts),
                            city=cname or pname,
                            category=type_tail,
                            coords=LatLng(lat=w_lat, lng=w_lng),
                            provider="amap",
                        )
                    )

        if eff_bias and suggestions:
            clean_city = city.strip().rstrip("市")

            def _rank_key(item: PlaceSuggestion) -> tuple[int, float]:
                city_hit = 0 if (clean_city and clean_city in (item.subtext + item.city)) else 1
                dist = haversine_km(eff_bias, item.coords)
                return (city_hit, dist if dist > 35.0 else 0.0)

            suggestions.sort(key=_rank_key)

        return suggestions[:limit]

    async def geocode(
        self,
        address: str,
        city: str = "",
        bias_coords: LatLng | None = None,
    ) -> tuple[LatLng, str]:
        cands = await self.suggest_places(address, city=city, bias_coords=bias_coords, limit=5)
        if cands:
            top = cands[0]
            label = f"{top.name}（{top.subtext}）" if top.subtext else top.name
            return top.coords, label

        async with httpx.AsyncClient(timeout=10.0) as client:
            geo_resp = await client.get(
                "https://restapi.amap.com/v3/geocode/geo",
                params={"key": self.api_key, "address": address.strip(), "city": city},
            )
            geo_resp.raise_for_status()
            g_data = geo_resp.json()
            _raise_if_amap_key_error(g_data)
            geocodes = g_data.get("geocodes") or []
            if not geocodes:
                raise ValueError(f"高德未找到「{address}」，请点击输入框右侧搜地点从列表选定")
            top = geocodes[0]
            lng_s, lat_s = top["location"].split(",", 1)
            w_lat, w_lng = gcj02_to_wgs84(float(lat_s), float(lng_s))
            return LatLng(lat=w_lat, lng=w_lng), top.get("formatted_address", address)

    async def search_places(
        self,
        center: LatLng,
        radius_m: int,
        keywords: list[str],
        category: str = "restaurant",
        city: str = "",
    ) -> list[VenueCandidate]:
        g_lat, g_lng = wgs84_to_gcj02(center.lat, center.lng)
        _AMAP_TYPES = {
            "any": "150500|060100|110100|050000",
            "station": "150500|150200|150700",
            "landmark": "060100|110100|140000",
            "cafe": "050500|050400",
            "bar": "050000|080304",
            "restaurant": "050000",
            "hotpot": "050000",
            "japanese": "050000",
            "western": "050000",
        }
        kw = "|".join(keywords[:3]) if keywords else ""
        type_code = _AMAP_TYPES.get(category, "150500|060100|050000")

        async with httpx.AsyncClient(timeout=12.0) as client:
            # Try v5/place/around first, fallback to v3/place/around for universal WebService key compatibility
            v5_params = {
                "key": self.api_key,
                "keywords": kw,
                "location": f"{g_lng},{g_lat}",
                "radius": str(max(800, min(50000, radius_m))),
                "sortrule": "weight",
                "page_size": "18",
                "show_fields": "business",
                "types": type_code,
            }
            resp = await client.get("https://restapi.amap.com/v5/place/around", params=v5_params)
            data = resp.json() if resp.status_code == 200 else {}
            _raise_if_amap_key_error(data)
            pois = data.get("pois") or []

            if not pois:
                v3_params = {
                    "key": self.api_key,
                    "keywords": kw,
                    "location": f"{g_lng},{g_lat}",
                    "radius": str(max(800, min(50000, radius_m))),
                    "sortrule": "weight",
                    "offset": "18",
                    "page": "1",
                    "extensions": "all",
                    "types": type_code,
                }
                r3 = await client.get("https://restapi.amap.com/v3/place/around", params=v3_params)
                d3 = r3.json() if r3.status_code == 200 else {}
                _raise_if_amap_key_error(d3)
                pois = d3.get("pois") or []

        candidates: list[VenueCandidate] = []
        for p in pois:
            loc_str = p.get("location", "")
            if not isinstance(loc_str, str) or "," not in loc_str:
                continue
            lng_s, lat_s = loc_str.split(",", 1)
            w_lat, w_lng = gcj02_to_wgs84(float(lat_s), float(lng_s))
            biz = p.get("business") or p.get("biz_ext") or {}
            rating_raw = biz.get("rating")
            rating = float(rating_raw) if isinstance(rating_raw, (str, int, float)) and str(rating_raw) not in ("", "[]") else None
            cost_raw = biz.get("cost")
            price_str = f"¥{cost_raw}/人" if isinstance(cost_raw, (str, int, float)) and str(cost_raw) not in ("", "[]") else ""
            parking_type = biz.get("parking_type", "")
            parking_str = parking_type if isinstance(parking_type, str) else ""
            tag_raw = biz.get("tag", "")
            tag_str = tag_raw if isinstance(tag_raw, str) else ""
            type_parts = [t for t in str(p.get("type", "")).split(";") if t]
            addr_raw = p.get("address", "")
            addr_str = addr_raw if isinstance(addr_raw, str) else ""
            city_prefix = f"{p.get('cityname', '')}{p.get('adname', '')}"

            is_subway = any("地铁" in t or "车站" in t for t in type_parts)
            subway_hint = addr_str if (is_subway and "线" in addr_str) else ""

            candidates.append(
                VenueCandidate(
                    place_id=f"amap_{p.get('id', '')}",
                    name=p.get("name", "未知地点"),
                    address=f"{city_prefix}{addr_str}",
                    coords=LatLng(lat=w_lat, lng=w_lng),
                    rating=rating,
                    review_count=200 if rating else 60,
                    price_level=price_str,
                    categories=type_parts[-2:] if type_parts else [],
                    highlights=[t.strip() for t in tag_str.split(",") if t.strip()][:3],
                    subway_hint=subway_hint,
                    parking_hint=f"停车：{parking_str}" if parking_str else "",
                    provider="amap",
                )
            )
        return candidates

    async def _single_route(
        self,
        client: httpx.AsyncClient,
        sem: asyncio.Semaphore,
        p: ResolvedParticipant,
        v: VenueCandidate,
        city: str,
    ) -> tuple[float, float, str]:
        o_lat, o_lng = wgs84_to_gcj02(p.coords.lat, p.coords.lng)
        d_lat, d_lng = wgs84_to_gcj02(v.coords.lat, v.coords.lng)
        origin_s = f"{o_lng},{o_lat}"
        dest_s = f"{d_lng},{d_lat}"
        city1 = infer_amap_city_code(p.coords, city)
        city2 = infer_amap_city_code(v.coords, city)

        async with sem:
            try:
                if p.transport_mode == "driving":
                    r = await client.get(
                        "https://restapi.amap.com/v3/direction/driving",
                        params={"key": self.api_key, "origin": origin_s, "destination": dest_s, "extensions": "base"},
                    )
                    paths = (r.json().get("route") or {}).get("paths") or []
                    if paths:
                        dist_km = float(paths[0].get("distance", 0)) / 1000.0
                        dur_s = float(paths[0].get("duration") or 0)
                        if dur_s <= 0:
                            dur_s = (dist_km / 28.0) * 3600.0
                        return round(max(2.0, dur_s / 60.0), 1), round(dist_km, 2), f"驾车 {dist_km:.1f} km"

                elif p.transport_mode == "transit":
                    r = await client.get(
                        "https://restapi.amap.com/v3/direction/transit/integrated",
                        params={
                            "key": self.api_key,
                            "origin": origin_s,
                            "destination": dest_s,
                            "city": city1,
                            "cityd": city2,
                        },
                    )
                    transits = (r.json().get("route") or {}).get("transits") or []
                    if transits:
                        t0 = transits[0]
                        dist_km = float(t0.get("distance", 0)) / 1000.0
                        dur_s = float(t0.get("duration") or 0)
                        lines: list[str] = []
                        for seg in t0.get("segments") or []:
                            buslines = (seg.get("bus") or {}).get("buslines") or []
                            if buslines and buslines[0].get("name"):
                                lines.append(buslines[0]["name"].split("(")[0])
                        line_summary = " → ".join(lines[:3]) if lines else "地铁公交"
                        if dur_s <= 0:
                            dur_s = (dist_km / 22.0) * 3600.0
                        return round(max(3.0, dur_s / 60.0), 1), round(dist_km, 2), line_summary

                elif p.transport_mode == "walking":
                    r = await client.get(
                        "https://restapi.amap.com/v3/direction/walking",
                        params={"key": self.api_key, "origin": origin_s, "destination": dest_s},
                    )
                    paths = (r.json().get("route") or {}).get("paths") or []
                    if paths:
                        dist_km = float(paths[0].get("distance", 0)) / 1000.0
                        dur_s = float(paths[0].get("duration", 0))
                        return round(max(1.0, dur_s / 60.0), 1), round(dist_km, 2), f"步行 {dist_km:.1f} km"

                else:  # cycling (Amap v4 bicycling)
                    r = await client.get(
                        "https://restapi.amap.com/v4/direction/bicycling",
                        params={"key": self.api_key, "origin": origin_s, "destination": dest_s},
                    )
                    paths = (r.json().get("data") or {}).get("paths") or []
                    if paths:
                        dist_km = float(paths[0].get("distance", 0)) / 1000.0
                        dur_s = float(paths[0].get("duration", 0))
                        return round(max(2.0, dur_s / 60.0), 1), round(dist_km, 2), f"骑行 {dist_km:.1f} km"
            except Exception:
                pass

        d_km = haversine_km(p.coords, v.coords) * 1.32
        speed = {"driving": 28.0, "transit": 22.0, "cycling": 13.5, "walking": 4.6}[p.transport_mode]
        return round(max(3.0, (d_km / speed) * 60.0), 1), round(d_km, 2), "路网估算"

    async def compute_travel_matrix(
        self,
        participants: list[ResolvedParticipant],
        venues: list[VenueCandidate],
        city: str = "",
    ) -> list[list[tuple[float, float, str]]]:
        sem = asyncio.Semaphore(6)
        async with httpx.AsyncClient(timeout=12.0) as client:
            rows = []
            for p in participants:
                row_tasks = [self._single_route(client, sem, p, v, city) for v in venues]
                rows.append(await asyncio.gather(*row_tasks))
            return rows
