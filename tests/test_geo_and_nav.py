from lets_meet.geo import (
    compute_search_corridor,
    gcj02_to_wgs84,
    haversine_km,
    wgs84_to_gcj02,
)
from lets_meet.models import LatLng, ResolvedParticipant
from lets_meet.nav_links import build_navigation_links


def test_wgs84_gcj02_roundtrip_in_beijing():
    w_lat, w_lng = 39.9929, 116.3378
    g_lat, g_lng = wgs84_to_gcj02(w_lat, w_lng)
    # In Beijing, GCJ-02 offset should be several hundred meters (~0.001-0.007 deg)
    assert abs(g_lat - w_lat) > 0.0005
    assert abs(g_lng - w_lng) > 0.004
    back_lat, back_lng = gcj02_to_wgs84(g_lat, g_lng)
    assert abs(back_lat - w_lat) < 1e-5
    assert abs(back_lng - w_lng) < 1e-5


def test_outside_china_coordinates_untouched():
    sf_lat, sf_lng = 37.7749, -122.4194
    assert wgs84_to_gcj02(sf_lat, sf_lng) == (sf_lat, sf_lng)
    assert gcj02_to_wgs84(sf_lat, sf_lng) == (sf_lat, sf_lng)


def test_speed_weighted_corridor_pulls_toward_slower_walker():
    walker = ResolvedParticipant(
        id="p1",
        name="Walker",
        input_address="A",
        resolved_address="A",
        coords=LatLng(lat=39.9929, lng=116.3378),
        transport_mode="walking",
    )
    driver = ResolvedParticipant(
        id="p2",
        name="Driver",
        input_address="B",
        resolved_address="B",
        coords=LatLng(lat=39.9968, lng=116.4808),
        transport_mode="driving",
    )
    geo_mid, time_mid, anchors, radius_m = compute_search_corridor([walker, driver])
    # Equal-time midpoint must be significantly closer to the walker than the driver
    d_to_walker = haversine_km(time_mid, walker.coords)
    d_to_driver = haversine_km(time_mid, driver.coords)
    assert d_to_walker < d_to_driver * 0.35
    assert len(anchors) == 3
    assert 900 <= radius_m <= 7500


def test_mobile_navigation_universal_links():
    orig = LatLng(lat=39.9929, lng=116.3378)
    dest = LatLng(lat=39.9726, lng=116.4472)
    links = build_navigation_links(
        origin=orig,
        origin_name="五道口",
        dest=dest,
        dest_name="山葵家",
        place_id="ChIJ12345",
        mode="transit",
        provider="google",
    )
    assert "travelmode=transit" in links["google_maps"]
    assert "destination_place_id=ChIJ12345" in links["google_maps"]
    assert "dirflg=r" in links["apple_maps"]
    assert "callnative=1" in links["amap"]
    assert "coordinate=gaode" in links["amap"]
    assert "coord_type=wgs84" in links["baidu_maps"]
    assert "navigate=yes" in links["waze"]
