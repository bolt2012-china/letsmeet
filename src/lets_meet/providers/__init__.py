from __future__ import annotations

from lets_meet.config import RuntimeConfig
from lets_meet.geo import is_in_mainland_china
from lets_meet.models import LatLng
from lets_meet.providers.amap import AmapProvider
from lets_meet.providers.base import MapProvider
from lets_meet.providers.demo import DemoMapProvider
from lets_meet.providers.google_maps import GoogleMapsProvider
from lets_meet.providers.osm import OpenStreetMapProvider


def _looks_chinese_context(text: str, coords: list[LatLng | None]) -> bool:
    for c in coords:
        if c is not None:
            return is_in_mainland_china(c.lat, c.lng)
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def select_map_provider(
    config: RuntimeConfig,
    context_text: str = "",
    known_coords: list[LatLng | None] | None = None,
) -> tuple[MapProvider, list[str]]:
    """
    Unified multi-provider selector:
      - Explicit "google" -> GoogleMapsProvider (or fallback to OSM live engine if key missing)
      - Explicit "amap"   -> AmapProvider (or fallback to OSM live engine if key missing)
      - Explicit "osm"    -> OpenStreetMapProvider (free global real geocoding + POI + OSRM road routing)
      - Explicit "demo"   -> DemoMapProvider (offline deterministic preset simulator)
      - "auto"            -> Uses Amap (China) or Google Maps if API keys are configured;
                             otherwise uses real-world OpenStreetMap + OSRM live engine (zero API key needed!).
    """
    warnings: list[str] = []
    choice = config.map_provider
    is_cn = _looks_chinese_context(context_text, known_coords or [])

    if choice == "demo":
        return DemoMapProvider(), warnings

    if choice == "osm":
        return OpenStreetMapProvider(), warnings

    if choice == "amap":
        if config.amap_api_key:
            return AmapProvider(config.amap_api_key), warnings
        warnings.append("未配置 AMAP_API_KEY，已自动切换为免密钥全球真实地图引擎（OpenStreetMap + OSRM）。")
        return OpenStreetMapProvider(), warnings

    if choice == "google":
        if config.google_maps_api_key:
            return GoogleMapsProvider(config.google_maps_api_key), warnings
        warnings.append("未配置 GOOGLE_MAPS_API_KEY，已自动切换为免密钥全球真实地图引擎（OpenStreetMap + OSRM）。")
        return OpenStreetMapProvider(), warnings

    # Auto mode: prefer commercial API if configured, otherwise real OpenStreetMap + OSRM live engine!
    if is_cn:
        if config.amap_api_key:
            return AmapProvider(config.amap_api_key), warnings
        if config.google_maps_api_key:
            return GoogleMapsProvider(config.google_maps_api_key), warnings
    else:
        if config.google_maps_api_key:
            return GoogleMapsProvider(config.google_maps_api_key), warnings
        if config.amap_api_key:
            return AmapProvider(config.amap_api_key), warnings

    return OpenStreetMapProvider(), warnings
