from __future__ import annotations

import asyncio
import httpx
from lets_meet.geo import haversine_km, resolve_city_center
from lets_meet.models import LatLng, PlaceSuggestion, ResolvedParticipant, TransportMode, VenueCandidate
from lets_meet.providers.base import MapProvider

_PRICE_MAP = {
    "PRICE_LEVEL_FREE": "免费",
    "PRICE_LEVEL_INEXPENSIVE": "$",
    "PRICE_LEVEL_MODERATE": "$$",
    "PRICE_LEVEL_EXPENSIVE": "$$$",
    "PRICE_LEVEL_VERY_EXPENSIVE": "$$$$",
    1: "$",
    2: "$$",
    3: "$$$",
    4: "$$$$",
}

_GMODE_MAP: dict[TransportMode, str] = {
    "driving": "driving",
    "transit": "transit",
    "walking": "walking",
    "cycling": "bicycling",
}

_ROUTES_V2_MODE: dict[TransportMode, str] = {
    "driving": "DRIVE",
    "transit": "TRANSIT",
    "walking": "WALK",
    "cycling": "BICYCLE",
}


class GoogleMapsProvider(MapProvider):
    """
    Google Maps Platform adapter (dual-compatible with New v1/v2 APIs & Legacy APIs):
      - Places API (New) v1 `places:searchText` + Legacy `place/textsearch` & `geocode/json` fallback
      - Distance Matrix API + Routes API v2 `computeRouteMatrix` fallback
    """

    name = "google"

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
                            provider="google",
                        )
                    ]
            except ValueError:
                pass

        eff_bias = bias_coords or resolve_city_center(city)
        text_q = f"{city} {raw}".strip() if city and city.lower() not in raw.lower() else raw
        suggestions: list[PlaceSuggestion] = []
        last_err = ""

        async with httpx.AsyncClient(timeout=10.0) as client:
            # 1. Try Places API (New) v1 searchText
            v1_headers = {
                "Content-Type": "application/json",
                "X-Goog-Api-Key": self.api_key,
                "X-Goog-FieldMask": "places.id,places.displayName,places.formattedAddress,places.location,places.types",
            }
            v1_payload: dict = {
                "textQuery": text_q,
                "maxResultCount": min(12, max(5, limit)),
                "languageCode": "zh-CN",
            }
            if eff_bias:
                v1_payload["locationBias"] = {
                    "circle": {
                        "center": {"latitude": eff_bias.lat, "longitude": eff_bias.lng},
                        "radius": 38000.0,
                    }
                }
            r_v1 = await client.post("https://places.googleapis.com/v1/places:searchText", headers=v1_headers, json=v1_payload)
            if r_v1.status_code == 200:
                for p in r_v1.json().get("places") or []:
                    loc = p.get("location") or {}
                    if "latitude" not in loc or "longitude" not in loc:
                        continue
                    pt = LatLng(lat=round(float(loc["latitude"]), 6), lng=round(float(loc["longitude"]), 6))
                    name = (p.get("displayName") or {}).get("text") or raw
                    addr = p.get("formattedAddress") or ""
                    types = [t.replace("_", " ") for t in (p.get("types") or [])[:2]]
                    suggestions.append(
                        PlaceSuggestion(
                            place_id=p.get("id") or f"google_{pt.lat}_{pt.lng}",
                            name=name,
                            subtext=addr,
                            category=" · ".join(types),
                            coords=pt,
                            provider="google",
                        )
                    )
            else:
                try:
                    err_obj = r_v1.json().get("error") or {}
                    last_err = err_obj.get("message") or f"HTTP {r_v1.status_code}"
                except Exception:
                    last_err = f"HTTP {r_v1.status_code}"

            # 2. Fallback to Legacy Places Text Search or Geocoding API
            if not suggestions:
                ts_params: dict[str, str] = {"query": text_q, "key": self.api_key, "language": "zh-CN"}
                if eff_bias:
                    ts_params["location"] = f"{eff_bias.lat},{eff_bias.lng}"
                    ts_params["radius"] = "38000"
                r_leg = await client.get("https://maps.googleapis.com/maps/api/place/textsearch/json", params=ts_params)
                if r_leg.status_code == 200:
                    d_leg = r_leg.json()
                    if d_leg.get("status") == "OK":
                        for p in (d_leg.get("results") or [])[:limit]:
                            loc = (p.get("geometry") or {}).get("location") or {}
                            if "lat" not in loc:
                                continue
                            pt = LatLng(lat=round(float(loc["lat"]), 6), lng=round(float(loc["lng"]), 6))
                            suggestions.append(
                                PlaceSuggestion(
                                    place_id=p.get("place_id") or f"google_{pt.lat}_{pt.lng}",
                                    name=p.get("name") or raw,
                                    subtext=p.get("formatted_address") or "",
                                    coords=pt,
                                    provider="google",
                                )
                            )
                    elif d_leg.get("error_message"):
                        last_err = d_leg["error_message"]

            if not suggestions:
                r_geo = await client.get(
                    "https://maps.googleapis.com/maps/api/geocode/json",
                    params={"address": text_q, "key": self.api_key, "language": "zh-CN"},
                )
                if r_geo.status_code == 200:
                    d_geo = r_geo.json()
                    if d_geo.get("status") == "OK":
                        for g in (d_geo.get("results") or [])[:limit]:
                            loc = (g.get("geometry") or {}).get("location") or {}
                            if "lat" not in loc:
                                continue
                            pt = LatLng(lat=round(float(loc["lat"]), 6), lng=round(float(loc["lng"]), 6))
                            fmt = g.get("formatted_address") or raw
                            comps = g.get("address_components") or []
                            short_name = comps[0].get("long_name") if comps else fmt.split(",")[0]
                            suggestions.append(
                                PlaceSuggestion(
                                    place_id=g.get("place_id") or f"google_{pt.lat}_{pt.lng}",
                                    name=short_name or raw,
                                    subtext=fmt,
                                    coords=pt,
                                    provider="google",
                                )
                            )
                    elif d_geo.get("status") in ("REQUEST_DENIED", "OVER_DAILY_LIMIT", "INVALID_REQUEST"):
                        msg = d_geo.get("error_message") or d_geo.get("status")
                        raise ValueError(
                            f"Google Maps API 调用被拒绝（{msg}）：请确认已在 Google Cloud 启用 Places API (New) 或 Geocoding API 并绑定结算账号。"
                        )

        if not suggestions and last_err and ("API key" in last_err or "has not been used" in last_err or "disabled" in last_err):
            raise ValueError(f"Google Maps API 未就绪：{last_err}")

        if eff_bias and suggestions:
            suggestions.sort(key=lambda item: 0.0 if haversine_km(eff_bias, item.coords) <= 45.0 else haversine_km(eff_bias, item.coords))

        return suggestions[:limit]

    async def geocode(
        self,
        address: str,
        city: str = "",
        bias_coords: LatLng | None = None,
    ) -> tuple[LatLng, str]:
        cands = await self.suggest_places(address, city=city, bias_coords=bias_coords, limit=5)
        if not cands:
            raise ValueError(f"Google Maps 未找到地址：{address}")
        top = cands[0]
        label = f"{top.name}（{top.subtext}）" if top.subtext and top.name not in top.subtext else (top.subtext or top.name)
        return top.coords, label

    async def search_places(
        self,
        center: LatLng,
        radius_m: int,
        keywords: list[str],
        category: str = "restaurant",
        city: str = "",
    ) -> list[VenueCandidate]:
        _GOOGLE_DEFAULTS = {
            "any": "transit station mall plaza cafe",
            "station": "subway station train station transit hub",
            "landmark": "shopping mall plaza park library landmark",
            "cafe": "coffee shop cafe",
            "restaurant": "restaurant",
        }
        text_query = " ".join(keywords[:2]) if keywords else _GOOGLE_DEFAULTS.get(category, category)
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": self.api_key,
            "X-Goog-FieldMask": (
                "places.id,places.displayName,places.formattedAddress,places.location,"
                "places.rating,places.userRatingCount,places.priceLevel,places.types,"
                "places.currentOpeningHours.openNow,places.editorialSummary,places.parkingOptions"
            ),
        }
        payload = {
            "textQuery": text_query,
            "maxResultCount": 14,
            "languageCode": "zh-CN",
            "locationBias": {
                "circle": {
                    "center": {"latitude": center.lat, "longitude": center.lng},
                    "radius": float(max(500, min(50000, radius_m))),
                }
            },
        }
        candidates: list[VenueCandidate] = []
        max_dist_km = (radius_m / 1000.0) * 2.2 + 1.5

        async with httpx.AsyncClient(timeout=12.0) as client:
            resp = await client.post(
                "https://places.googleapis.com/v1/places:searchText",
                headers=headers,
                json=payload,
            )
            if resp.status_code == 200:
                for p in resp.json().get("places") or []:
                    loc = p.get("location") or {}
                    if "latitude" not in loc or "longitude" not in loc:
                        continue
                    pt = LatLng(lat=round(float(loc["latitude"]), 6), lng=round(float(loc["longitude"]), 6))
                    if haversine_km(center, pt) > max_dist_km:
                        continue
                    parking_opts = p.get("parkingOptions") or {}
                    has_parking = any(bool(v) for v in parking_opts.values())
                    editorial = (p.get("editorialSummary") or {}).get("text", "")
                    candidates.append(
                        VenueCandidate(
                            place_id=p.get("id", ""),
                            name=(p.get("displayName") or {}).get("text", "未知地点"),
                            address=p.get("formattedAddress", ""),
                            coords=pt,
                            rating=p.get("rating"),
                            review_count=int(p.get("userRatingCount") or 0),
                            price_level=_PRICE_MAP.get(p.get("priceLevel", ""), ""),
                            categories=[t.replace("_", " ") for t in (p.get("types") or [])[:3]],
                            open_now=(p.get("currentOpeningHours") or {}).get("openNow"),
                            highlights=[editorial] if editorial else [],
                            parking_hint="提供停车位" if has_parking else "",
                            provider="google",
                        )
                    )
            else:
                # Fallback to Legacy Places Text Search
                r_leg = await client.get(
                    "https://maps.googleapis.com/maps/api/place/textsearch/json",
                    params={
                        "query": text_query,
                        "location": f"{center.lat},{center.lng}",
                        "radius": str(max(500, min(50000, radius_m))),
                        "language": "zh-CN",
                        "key": self.api_key,
                    },
                )
                if r_leg.status_code == 200 and r_leg.json().get("status") == "OK":
                    for p in r_leg.json().get("results") or []:
                        loc = (p.get("geometry") or {}).get("location") or {}
                        if "lat" not in loc:
                            continue
                        pt = LatLng(lat=round(float(loc["lat"]), 6), lng=round(float(loc["lng"]), 6))
                        if haversine_km(center, pt) > max_dist_km:
                            continue
                        candidates.append(
                            VenueCandidate(
                                place_id=p.get("place_id", ""),
                                name=p.get("name", "未知地点"),
                                address=p.get("formatted_address", ""),
                                coords=pt,
                                rating=p.get("rating"),
                                review_count=int(p.get("user_ratings_total") or 0),
                                price_level=_PRICE_MAP.get(p.get("price_level", ""), ""),
                                categories=[t.replace("_", " ") for t in (p.get("types") or [])[:3]],
                                open_now=(p.get("opening_hours") or {}).get("open_now"),
                                provider="google",
                            )
                        )
        return candidates

    async def _fetch_routes_v2_matrix(
        self,
        client: httpx.AsyncClient,
        mode: TransportMode,
        origins: list[LatLng],
        destinations: list[LatLng],
    ) -> list[list[tuple[float, float, str]]] | None:
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": self.api_key,
            "X-Goog-FieldMask": "originIndex,destinationIndex,duration,distanceMeters,condition",
        }
        body = {
            "origins": [{"waypoint": {"location": {"latLng": {"latitude": o.lat, "longitude": o.lng}}}} for o in origins],
            "destinations": [{"waypoint": {"location": {"latLng": {"latitude": d.lat, "longitude": d.lng}}}} for d in destinations],
            "travelMode": _ROUTES_V2_MODE.get(mode, "TRANSIT"),
        }
        resp = await client.post("https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix", headers=headers, json=body)
        if resp.status_code != 200:
            return None
        entries = resp.json()
        if not isinstance(entries, list) or not entries:
            return None

        grid: list[list[tuple[float, float, str] | None]] = [[None for _ in destinations] for _ in origins]
        for el in entries:
            oi = int(el.get("originIndex", 0))
            di = int(el.get("destinationIndex", 0))
            dur_str = str(el.get("duration") or "")
            if dur_str.endswith("s") and el.get("condition") == "ROUTE_EXISTS":
                sec = float(dur_str[:-1])
                km = float(el.get("distanceMeters") or 0) / 1000.0
                mins = round(max(1.0, sec / 60.0), 1)
                grid[oi][di] = (mins, round(km, 2), f"{round(mins)} 分钟 ({km:.1f} km)")

        final_grid: list[list[tuple[float, float, str]]] = []
        for i, o in enumerate(origins):
            row: list[tuple[float, float, str]] = []
            for j, d in enumerate(destinations):
                cell = grid[i][j]
                if cell is not None:
                    row.append(cell)
                else:
                    d_km = haversine_km(o, d) * 1.32
                    row.append((round(max(3.0, (d_km / 22.0) * 60.0), 1), round(d_km, 2), "路网估算"))
            final_grid.append(row)
        return final_grid

    async def _fetch_mode_rows(
        self,
        client: httpx.AsyncClient,
        mode: TransportMode,
        origins: list[LatLng],
        destinations: list[LatLng],
    ) -> list[list[tuple[float, float, str]]]:
        origins_str = "|".join(f"{o.lat},{o.lng}" for o in origins)
        chunk_size = min(25, max(1, 100 // max(1, len(origins))))
        matrix: list[list[tuple[float, float, str]]] = [[] for _ in origins]

        for start in range(0, len(destinations), chunk_size):
            chunk = destinations[start : start + chunk_size]
            dest_str = "|".join(f"{d.lat},{d.lng}" for d in chunk)
            resp = await client.get(
                "https://maps.googleapis.com/maps/api/distancematrix/json",
                params={
                    "origins": origins_str,
                    "destinations": dest_str,
                    "mode": _GMODE_MAP.get(mode, "transit"),
                    "key": self.api_key,
                    "language": "zh-CN",
                },
            )
            data = resp.json() if resp.status_code == 200 else {}
            if data.get("status") != "OK":
                v2_chunk = await self._fetch_routes_v2_matrix(client, mode, origins, chunk)
                if v2_chunk is not None:
                    for i in range(len(origins)):
                        matrix[i].extend(v2_chunk[i])
                    continue

            rows = data.get("rows") or []
            for i, origin in enumerate(origins):
                elements = rows[i].get("elements", []) if i < len(rows) else []
                for j, dest in enumerate(chunk):
                    el = elements[j] if j < len(elements) else {}
                    if el.get("status") == "OK":
                        sec = float((el.get("duration_in_traffic") or el.get("duration") or {}).get("value", 0))
                        meters = float((el.get("distance") or {}).get("value", 0))
                        dur_text = (el.get("duration") or {}).get("text", "")
                        dist_text = (el.get("distance") or {}).get("text", "")
                        matrix[i].append((round(max(1.0, sec / 60.0), 1), round(meters / 1000.0, 2), f"{dur_text} ({dist_text})"))
                    else:
                        d_km = haversine_km(origin, dest) * 1.32
                        est_min = round(max(3.0, (d_km / 22.0) * 60.0), 1)
                        matrix[i].append((est_min, round(d_km, 2), "路网估算"))
        return matrix

    async def compute_travel_matrix(
        self,
        participants: list[ResolvedParticipant],
        venues: list[VenueCandidate],
        city: str = "",
    ) -> list[list[tuple[float, float, str]]]:
        if not venues:
            return [[] for _ in participants]
        dest_coords = [v.coords for v in venues]
        by_mode: dict[TransportMode, list[int]] = {}
        for idx, p in enumerate(participants):
            by_mode.setdefault(p.transport_mode, []).append(idx)

        full_matrix: list[list[tuple[float, float, str]]] = [[] for _ in participants]
        async with httpx.AsyncClient(timeout=14.0) as client:
            tasks = [
                self._fetch_mode_rows(client, mode, [participants[i].coords for i in idxs], dest_coords)
                for mode, idxs in by_mode.items()
            ]
            mode_results = await asyncio.gather(*tasks)
            for (_, idxs), rows in zip(by_mode.items(), mode_results):
                for local_i, global_i in enumerate(idxs):
                    full_matrix[global_i] = rows[local_i]
        return full_matrix
