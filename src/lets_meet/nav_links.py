from __future__ import annotations

from urllib.parse import quote
from lets_meet.geo import wgs84_to_gcj02
from lets_meet.models import LatLng, TransportMode


_GOOGLE_MODE: dict[TransportMode, str] = {
    "driving": "driving",
    "transit": "transit",
    "walking": "walking",
    "cycling": "bicycling",
}

_APPLE_DIRFLG: dict[TransportMode, str] = {
    "driving": "d",
    "transit": "r",
    "walking": "w",
    "cycling": "w",
}

_AMAP_MODE: dict[TransportMode, str] = {
    "driving": "car",
    "transit": "bus",
    "walking": "walk",
    "cycling": "ride",
}

_BAIDU_MODE: dict[TransportMode, str] = {
    "driving": "driving",
    "transit": "transit",
    "walking": "walking",
    "cycling": "riding",
}


def build_navigation_links(
    origin: LatLng,
    origin_name: str,
    dest: LatLng,
    dest_name: str,
    place_id: str,
    mode: TransportMode,
    provider: str = "google",
) -> dict[str, str]:
    """
    Build cross-platform mobile navigation Universal Links / URI bridges.
    All input `origin` and `dest` coordinates MUST be canonical WGS-84.
    - Google Maps Universal Link opens native Google Maps App on iOS & Android.
    - Apple Maps Link opens native Apple Maps on iOS/macOS.
    - Amap (Gaode) Link converts WGS-84 -> GCJ-02 and sets callnative=1 to wake native Amap App.
    - Baidu Maps Link explicitly passes coord_type=wgs84 so Baidu converts server-side without offset.
    - Waze Universal Link launches native Waze turn-by-turn navigation.
    """
    g_mode = _GOOGLE_MODE.get(mode, "transit")
    google_url = (
        f"https://www.google.com/maps/dir/?api=1"
        f"&origin={origin.lat},{origin.lng}"
        f"&destination={dest.lat},{dest.lng}"
        f"&travelmode={g_mode}"
    )
    # Attach official Google Place ID only when sourced from Google Places
    if provider == "google" and place_id and not place_id.startswith(("demo_", "amap_")):
        google_url += f"&destination_place_id={quote(place_id)}"

    apple_flg = _APPLE_DIRFLG.get(mode, "r")
    apple_url = (
        f"https://maps.apple.com/?saddr={origin.lat},{origin.lng}"
        f"&daddr={dest.lat},{dest.lng}"
        f"&q={quote(dest_name)}"
        f"&dirflg={apple_flg}"
    )

    orig_gcj_lat, orig_gcj_lng = wgs84_to_gcj02(origin.lat, origin.lng)
    dest_gcj_lat, dest_gcj_lng = wgs84_to_gcj02(dest.lat, dest.lng)
    amap_mode = _AMAP_MODE.get(mode, "bus")
    amap_url = (
        f"https://uri.amap.com/navigation"
        f"?from={orig_gcj_lng},{orig_gcj_lat},{quote(origin_name or '出发地')}"
        f"&to={dest_gcj_lng},{dest_gcj_lat},{quote(dest_name)}"
        f"&mode={amap_mode}&policy=1&src=lets-meet&coordinate=gaode&callnative=1"
    )

    baidu_mode = _BAIDU_MODE.get(mode, "transit")
    baidu_url = (
        f"https://api.map.baidu.com/direction"
        f"?origin=latlng:{origin.lat},{origin.lng}|name:{quote(origin_name or '出发地')}"
        f"&destination=latlng:{dest.lat},{dest.lng}|name:{quote(dest_name)}"
        f"&mode={baidu_mode}&coord_type=wgs84&output=html&src=webapp.letsmeet"
    )

    waze_url = f"https://waze.com/ul?ll={dest.lat},{dest.lng}&navigate=yes"

    return {
        "google_maps": google_url,
        "apple_maps": apple_url,
        "amap": amap_url,
        "baidu_maps": baidu_url,
        "waze": waze_url,
    }
