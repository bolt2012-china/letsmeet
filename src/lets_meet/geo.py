from __future__ import annotations

import math
from lets_meet.models import CorridorAnchor, LatLng, ResolvedParticipant, TransportMode

EARTH_RADIUS_KM = 6371.0088

# Effective urban door-to-door speed priors (km/h) used ONLY to place initial search anchors.
# Final ranking is always determined by real route travel times, not these priors.
URBAN_SPEED_KMH: dict[TransportMode, float] = {
    "driving": 30.0,
    "transit": 22.0,
    "cycling": 13.5,
    "walking": 4.6,
}


def is_in_mainland_china(lat: float, lng: float) -> bool:
    """Coarse bounding-box check for mainland China GCJ-02 applicability."""
    return 18.0 <= lat <= 53.6 and 73.5 <= lng <= 135.1


def _transform_lat(x: float, y: float) -> float:
    ret = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(y * math.pi) + 40.0 * math.sin(y / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (160.0 * math.sin(y / 12.0 * math.pi) + 320.0 * math.sin(y * math.pi / 30.0)) * 2.0 / 3.0
    return ret


def _transform_lng(x: float, y: float) -> float:
    ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(x * math.pi) + 40.0 * math.sin(x / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (150.0 * math.sin(x / 12.0 * math.pi) + 300.0 * math.sin(x / 30.0 * math.pi)) * 2.0 / 3.0
    return ret


def _delta(lat: float, lng: float) -> tuple[float, float]:
    a = 6378245.0
    ee = 0.00669342162296594323
    d_lat = _transform_lat(lng - 105.0, lat - 35.0)
    d_lng = _transform_lng(lng - 105.0, lat - 35.0)
    rad_lat = lat / 180.0 * math.pi
    magic = math.sin(rad_lat)
    magic = 1.0 - ee * magic * magic
    sqrt_magic = math.sqrt(magic)
    d_lat = (d_lat * 180.0) / ((a * (1.0 - ee)) / (magic * sqrt_magic) * math.pi)
    d_lng = (d_lng * 180.0) / (a / sqrt_magic * math.cos(rad_lat) * math.pi)
    return d_lat, d_lng


def wgs84_to_gcj02(lat: float, lng: float) -> tuple[float, float]:
    """Convert WGS-84 coordinates to GCJ-02 (Amap/Gaode coordinate frame)."""
    if not is_in_mainland_china(lat, lng):
        return lat, lng
    d_lat, d_lng = _delta(lat, lng)
    return round(lat + d_lat, 6), round(lng + d_lng, 6)


def gcj02_to_wgs84(lat: float, lng: float) -> tuple[float, float]:
    """Exact fixed-point inversion from GCJ-02 to WGS-84 (< 0.5m error)."""
    if not is_in_mainland_china(lat, lng):
        return lat, lng
    w_lat, w_lng = lat, lng
    for _ in range(3):
        g_lat, g_lng = wgs84_to_gcj02(w_lat, w_lng)
        w_lat += lat - g_lat
        w_lng += lng - g_lng
    return round(w_lat, 6), round(w_lng, 6)


def haversine_km(a: LatLng, b: LatLng) -> float:
    lat1, lon1 = math.radians(a.lat), math.radians(a.lng)
    lat2, lon2 = math.radians(b.lat), math.radians(b.lng)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = math.sin(dlat / 2.0) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2.0) ** 2
    return 2.0 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(h)))


def spherical_centroid(points: list[LatLng], weights: list[float] | None = None) -> LatLng:
    """Weighted Cartesian mean projected onto unit sphere."""
    if not points:
        raise ValueError("points must not be empty")
    if weights is None:
        weights = [1.0] * len(points)
    total_w = sum(weights) or 1.0
    x = y = z = 0.0
    for pt, w in zip(points, weights):
        lat_r = math.radians(pt.lat)
        lng_r = math.radians(pt.lng)
        x += w * math.cos(lat_r) * math.cos(lng_r)
        y += w * math.cos(lat_r) * math.sin(lng_r)
        z += w * math.sin(lat_r)
    x /= total_w
    y /= total_w
    z /= total_w
    hyp = math.hypot(x, y)
    if hyp < 1e-12 and abs(z) < 1e-12:
        return points[0]
    lat = math.degrees(math.atan2(z, hyp))
    lng = math.degrees(math.atan2(y, x))
    return LatLng(lat=round(lat, 6), lng=round(lng, 6))


def interpolate_great_circle(a: LatLng, b: LatLng, fraction: float) -> LatLng:
    """Interpolate fraction in [0, 1] from point A toward point B."""
    f = max(0.0, min(1.0, fraction))
    return spherical_centroid([a, b], [1.0 - f, f])


def compute_search_corridor(
    participants: list[ResolvedParticipant],
) -> tuple[LatLng, LatLng, list[CorridorAnchor], int]:
    """
    Compute:
      1. Pure geometric midpoint (reference baseline)
      2. Speed-weighted equal-time anchor (pulls closer to slower commuters!)
      3. Multi-point search corridor anchors (3 anchors covering road/transit hubs)
      4. Adaptive POI search radius in meters
    """
    coords = [p.coords for p in participants]
    geometric_mid = spherical_centroid(coords)

    # Slower traveler gets HIGHER pull weight w_i = 1 / speed_i so t = d / v equalizes:
    # For 2 participants: alpha (distance fraction from A toward B) = v_A / (v_A + v_B).
    inv_speed_weights = [1.0 / URBAN_SPEED_KMH.get(p.transport_mode, 22.0) for p in participants]
    time_weighted_mid = spherical_centroid(coords, inv_speed_weights)

    if len(participants) == 2:
        v_a = URBAN_SPEED_KMH.get(participants[0].transport_mode, 22.0)
        v_b = URBAN_SPEED_KMH.get(participants[1].transport_mode, 22.0)
        alpha = v_a / (v_a + v_b)
        alpha_low = max(0.15, min(0.85, alpha - 0.16))
        alpha_high = max(0.15, min(0.85, alpha + 0.16))
        anchors = [
            CorridorAnchor(
                label="等时核心区",
                coords=interpolate_great_circle(coords[0], coords[1], alpha),
                weight_from_first=round(alpha, 3),
            ),
            CorridorAnchor(
                label=f"偏向{participants[0].name}侧走廊",
                coords=interpolate_great_circle(coords[0], coords[1], alpha_low),
                weight_from_first=round(alpha_low, 3),
            ),
            CorridorAnchor(
                label=f"偏向{participants[1].name}侧走廊",
                coords=interpolate_great_circle(coords[0], coords[1], alpha_high),
                weight_from_first=round(alpha_high, 3),
            ),
        ]
        spread_km = haversine_km(coords[0], coords[1]) / 2.0
    else:
        anchors = [
            CorridorAnchor(label="等时核心区", coords=time_weighted_mid, weight_from_first=0.5),
            CorridorAnchor(label="几何重心区", coords=geometric_mid, weight_from_first=0.5),
        ]
        spread_km = max(haversine_km(time_weighted_mid, c) for c in coords)

    # Adaptive radius: 38% of half-spread, clamped between 900m and 7500m
    radius_m = int(max(900.0, min(7500.0, spread_km * 1000.0 * 0.38)))
    return geometric_mid, time_weighted_mid, anchors, radius_m


# Canonical WGS-84 city hubs + Amap citycodes for homonym disambiguation & transit routing
CITY_HUBS: dict[str, tuple[float, float, str]] = {
    "上海": (31.2304, 121.4737, "021"),
    "北京": (39.9042, 116.4074, "010"),
    "深圳": (22.5431, 114.0579, "0755"),
    "广州": (23.1291, 113.2644, "020"),
    "杭州": (30.2741, 120.1551, "0571"),
    "成都": (30.5728, 104.0668, "028"),
    "南京": (32.0603, 118.7969, "025"),
    "武汉": (30.5928, 114.3055, "027"),
    "重庆": (29.5630, 106.5516, "023"),
    "苏州": (31.2990, 120.5853, "0512"),
    "天津": (39.0842, 117.2010, "022"),
    "西安": (34.3416, 108.9398, "029"),
    "长沙": (28.2282, 112.9388, "0731"),
    "青岛": (36.0671, 120.3826, "0532"),
    "厦门": (24.4798, 118.0894, "0592"),
    "香港": (22.3193, 114.1694, "1852"),
    "台北": (25.0330, 121.5654, ""),
    "旧金山": (37.7749, -122.4194, ""),
    "san francisco": (37.7749, -122.4194, ""),
}


def resolve_city_center(city: str) -> LatLng | None:
    if not city:
        return None
    clean = city.strip().lower().rstrip("市")
    for name, (lat, lng, _) in CITY_HUBS.items():
        if name == clean or name in clean or clean in name:
            return LatLng(lat=lat, lng=lng)
    return None


def infer_amap_city_code(coords: LatLng | None, city: str = "") -> str:
    if city:
        clean = city.strip().rstrip("市")
        for name, (_, _, code) in CITY_HUBS.items():
            if code and (name == clean or name in clean):
                return code
        if clean:
            return clean
    if coords is not None:
        best_code = ""
        best_km = 95.0
        for _, (lat, lng, code) in CITY_HUBS.items():
            if not code:
                continue
            d = haversine_km(coords, LatLng(lat=lat, lng=lng))
            if d < best_km:
                best_km = d
                best_code = code
        if best_code:
            return best_code
    return "010"
