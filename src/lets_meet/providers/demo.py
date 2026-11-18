from __future__ import annotations

from lets_meet.geo import haversine_km, URBAN_SPEED_KMH
from lets_meet.models import LatLng, PlaceSuggestion, ResolvedParticipant, VenueCandidate
from lets_meet.providers.base import MapProvider

# Canonical WGS-84 coordinates for out-of-the-box zero-key interactive demo
_DEMO_PLACES_GAZETTEER: dict[str, tuple[float, float, str]] = {
    # Beijing Landmarks
    "望京": (39.9982, 116.4742, "北京市朝阳区望京地铁站"),
    "望京soho": (39.9968, 116.4808, "北京市朝阳区望京SOHO"),
    "中关村": (39.9839, 116.3164, "北京市海淀区中关村地铁站"),
    "五道口": (39.9929, 116.3378, "北京市海淀区五道口地铁站"),
    "北京大学": (39.9919, 116.3059, "北京市海淀区北京大学东门"),
    "清华大学": (40.0028, 116.3266, "北京市海淀区清华大学南门"),
    "国贸": (39.9087, 116.4605, "北京市朝阳区国贸CBD"),
    "三里屯": (39.9348, 116.4542, "北京市朝阳区三里屯太古里"),
    "西单": (39.9078, 116.3742, "北京市西城区西单大悦城"),
    "东直门": (39.9412, 116.4337, "北京市东城区东直门交通枢纽"),
    "西直门": (39.9405, 116.3554, "北京市西城区西直门地铁站"),
    "回龙观": (40.0708, 116.3362, "北京市昌平区回龙观地铁站"),
    "西二旗": (40.0531, 116.3061, "北京市海淀区西二旗地铁站"),
    "双井": (39.8936, 116.4616, "北京市朝阳区双井富力广场"),
    "朝阳大悦城": (39.9242, 116.5186, "北京市朝阳区朝阳大悦城"),
    # San Francisco / Bay Area Landmarks (Overseas Google Maps style scenario)
    "mission district": (37.7599, -122.4148, "Mission District, San Francisco, CA"),
    "mission": (37.7599, -122.4148, "16th St Mission BART, San Francisco, CA"),
    "marina": (37.8015, -122.4368, "Marina District, San Francisco, CA"),
    "soma": (37.7785, -122.4056, "SoMa, San Francisco, CA"),
    "embarcadero": (37.7929, -122.3968, "Embarcadero Station, San Francisco, CA"),
    "berkeley": (37.8703, -122.2680, "Downtown Berkeley BART, CA"),
    "sunset": (37.7535, -122.4947, "Inner Sunset, San Francisco, CA"),
    "hayes valley": (37.7759, -122.4245, "Hayes Valley, San Francisco, CA"),
}

_DEMO_VENUES: list[VenueCandidate] = [
    VenueCandidate(
        place_id="demo_bj_olympic_station",
        name="奥林匹克公园地铁站（8号线/15号线换乘厅）",
        address="北京市朝阳区大屯路与景观大道交汇处",
        coords=LatLng(lat=40.0012, lng=116.3928),
        rating=4.7,
        review_count=1650,
        price_level="",
        categories=["轨道车站", "换乘枢纽"],
        open_now=True,
        highlights=["15号线与8号线站内同台汇合，出站即奥森广场"],
        subway_hint="8号线/15号线站厅层B口服务台碰头",
        parking_hint="新奥购物中心地面与地下P2停车场",
        provider="demo",
    ),
    # Beijing Corridor Venues
    VenueCandidate(
        place_id="demo_bj_taiyanggong_sushi",
        name="山葵家精致料理（太阳宫凯德MALL店）",
        address="北京市朝阳区太阳宫中路12号凯德MALL 4层",
        coords=LatLng(lat=39.9726, lng=116.4472),
        rating=4.7,
        review_count=1840,
        price_level="¥148/人",
        categories=["日料", "寿司", "融合料理"],
        open_now=True,
        highlights=["海淀与朝阳折中王牌点", "包间安静好聊天", "清淡与炙烤兼顾"],
        subway_hint="地铁10号线太阳宫站C口直通商场",
        parking_hint="凯德MALL地下车库1200车位，消费积分抵扣停车费",
        provider="demo",
    ),
    VenueCandidate(
        place_id="demo_bj_olympic_yunnan",
        name="一坐一忘丽江主题餐厅（奥森北辰店）",
        address="北京市朝阳区北辰东路8号北辰时代大厦2层",
        coords=LatLng(lat=39.9942, lng=116.4038),
        rating=4.8,
        review_count=2310,
        price_level="¥125/人",
        categories=["云南菜", "清淡可选", "餐厅"],
        open_now=True,
        highlights=["东西向五道口—望京正中间", "菌菇汤锅不辣鲜美", "环境雅致不嘈杂"],
        subway_hint="地铁8号线/15号线奥林匹克公园站步行380米",
        parking_hint="北辰时代地下停车场车位充裕（8元/小时）",
        provider="demo",
    ),
    VenueCandidate(
        place_id="demo_bj_shaoyaoju_bistro",
        name="梧桐Plus·花园融合餐厅（芍药居店）",
        address="北京市朝阳区文学馆路47号玻璃阳光房",
        coords=LatLng(lat=39.9781, lng=116.4325),
        rating=4.6,
        review_count=960,
        price_level="¥165/人",
        categories=["融合菜", "Bistro", "西餐"],
        open_now=True,
        highlights=["玻璃花房安静出片", "创意融合菜不挑忌口", "适合老友聚餐深聊"],
        subway_hint="地铁10号线/13号线芍药居站B口步行420米",
        parking_hint="院内专属地面停车场免费2小时",
        provider="demo",
    ),
    VenueCandidate(
        place_id="demo_bj_mudanyuan_hotpot",
        name="新京熹·北京涮肉（牡丹园环宇荟店）",
        address="北京市海淀区花园路2号中海环宇荟L4",
        coords=LatLng(lat=39.9768, lng=116.3702),
        rating=4.7,
        review_count=1520,
        price_level="¥138/人",
        categories=["火锅", "铜锅涮肉", "餐厅"],
        open_now=True,
        highlights=["景泰蓝一人一锅可辣可清汤", "商场换乘大站零风吹日晒"],
        subway_hint="地铁10号线/19号线牡丹园站无缝直达",
        parking_hint="环宇荟B2/B3智能寻车车库",
        provider="demo",
    ),
    VenueCandidate(
        place_id="demo_bj_dongzhimen_cafe",
        name="Metal Hands 铁手咖啡制造局（东直门来福士店）",
        address="北京市东城区东直门南大街1号来福士广场1层",
        coords=LatLng(lat=39.9403, lng=116.4319),
        rating=4.6,
        review_count=1190,
        price_level="¥46/人",
        categories=["咖啡馆", "安静咖啡", "Brunch"],
        open_now=True,
        highlights=["2号线/13号线/机场线三线交汇", "开心果脏咖啡招牌", "外摆露台舒适"],
        subway_hint="地铁东直门站E口直达",
        parking_hint="来福士地下停车场",
        provider="demo",
    ),
    VenueCandidate(
        place_id="demo_bj_liangmaqiao_izakaya",
        name="鸟屯·炭火烧鸟居酒屋（亮马桥官舍店）",
        address="北京市朝阳区东方东路19号官舍南区B1",
        coords=LatLng(lat=39.9498, lng=116.4622),
        rating=4.7,
        review_count=1430,
        price_level="¥175/人",
        categories=["日料", "居酒屋", "清吧"],
        open_now=True,
        highlights=["吃完可沿亮马河散步", "备长炭现烤烧鸟", "晚间氛围感极佳"],
        subway_hint="地铁10号线亮马桥站C口出站即到",
        parking_hint="官舍地下车库充足",
        provider="demo",
    ),
    VenueCandidate(
        place_id="demo_bj_gulou_tea",
        name="提督·TIDU 新京菜（安定门环宇荟店）",
        address="北京市东城区安定门外大街208号中海环宇荟5层",
        coords=LatLng(lat=39.9562, lng=116.4081),
        rating=4.8,
        review_count=2090,
        price_level="¥180/人",
        categories=["京菜", "融合菜", "粤菜"],
        open_now=True,
        highlights=["中轴线地理与地铁双中心", "包间与卡座间距宽敞", "烤鸭与鱼子酱招牌"],
        subway_hint="地铁2号线安定门站/8号线安德里北街站步行5分钟",
        parking_hint="商场地下3层停车场车位宽裕",
        provider="demo",
    ),
    # San Francisco Bay Area Corridor Venues
    VenueCandidate(
        place_id="demo_sf_hayes_nopa",
        name="a Mano",
        address="450 Hayes St, San Francisco, CA 94102",
        coords=LatLng(lat=37.7769, lng=-122.4239),
        rating=4.6,
        review_count=2480,
        price_level="$$",
        categories=["italian", "bistro", "restaurant"],
        open_now=True,
        highlights=["Central Hayes Valley hub", "Handmade pasta & lively patio", "Great vegetarian options"],
        subway_hint="6 min walk from Van Ness Muni / Civic Center BART",
        parking_hint="Performing Arts Garage 1 block north",
        provider="demo",
    ),
    VenueCandidate(
        place_id="demo_sf_ferry_hog_island",
        name="Hog Island Oyster Co.",
        address="One Ferry Building #11, San Francisco, CA 94111",
        coords=LatLng(lat=37.7958, lng=-122.3934),
        rating=4.7,
        review_count=4120,
        price_level="$$$",
        categories=["seafood", "bistro", "restaurant"],
        open_now=True,
        highlights=["Ideal East Bay + SF BART midpoint", "Bay Bridge waterfront views"],
        subway_hint="3 min walk from Embarcadero BART & Muni",
        parking_hint="Washington & Embarcadero surface lot across street",
        provider="demo",
    ),
    VenueCandidate(
        place_id="demo_sf_japantown_marufuku",
        name="Marufuku Ramen",
        address="1581 Webster St #235, San Francisco, CA 94115",
        coords=LatLng(lat=37.7851, lng=-122.4320),
        rating=4.7,
        review_count=3890,
        price_level="$$",
        categories=["ramen", "japanese", "restaurant"],
        open_now=True,
        highlights=["Equidistant between Marina & Mission", "Authentic Hakata tonkotsu & veggie ramen"],
        subway_hint="Muni 38R/22 bus hub right outside Japan Center",
        parking_hint="Japan Center Garage directly underneath (validated)",
        provider="demo",
    ),
    VenueCandidate(
        place_id="demo_sf_sightglass_coffee",
        name="Sightglass Coffee (SoMa Flagship)",
        address="270 7th St, San Francisco, CA 94103",
        coords=LatLng(lat=37.7771, lng=-122.4086),
        rating=4.5,
        review_count=2150,
        price_level="$",
        categories=["coffee", "cafe", "bakery"],
        open_now=True,
        highlights=["Two-story sunlit loft", "Easy meetup from BART & Caltrain"],
        subway_hint="6 min walk from Civic Center BART",
        parking_hint="Metered street parking on 7th & Folsom",
        provider="demo",
    ),
]


class DemoMapProvider(MapProvider):
    """
    Zero-configuration offline-capable simulator with realistic urban road detour
    and transit transfer penalties so users can test the full pipeline before adding API keys.
    """

    name = "demo"

    async def suggest_places(
        self,
        query: str,
        city: str = "",
        bias_coords: LatLng | None = None,
        limit: int = 8,
    ) -> list[PlaceSuggestion]:
        raw = query.strip()
        key = raw.lower()
        matches: list[PlaceSuggestion] = []
        for name, (lat, lng, full) in _DEMO_PLACES_GAZETTEER.items():
            if key in name or name in key or key in full.lower():
                matches.append(
                    PlaceSuggestion(
                        place_id=f"demo_{name}",
                        name=full.split("区")[-1] if "区" in full else name.title(),
                        subtext=full,
                        city=city,
                        coords=LatLng(lat=lat, lng=lng),
                        provider="demo",
                    )
                )
        if matches:
            return matches[:limit]
        coords, full = await self.geocode(address=raw, city=city, bias_coords=bias_coords)
        return [
            PlaceSuggestion(
                place_id=f"demo_{coords.lat}_{coords.lng}",
                name=raw,
                subtext=full,
                city=city,
                coords=coords,
                provider="demo",
            )
        ]

    async def geocode(
        self,
        address: str,
        city: str = "",
        bias_coords: LatLng | None = None,
    ) -> tuple[LatLng, str]:
        raw = address.strip()
        key = raw.lower()
        # Exact or substring gazetteer match
        for name, (lat, lng, full) in _DEMO_PLACES_GAZETTEER.items():
            if name in key or key in name:
                return LatLng(lat=lat, lng=lng), full

        # Support "lat,lng" raw coordinates input
        if "," in raw:
            parts = raw.split(",", 1)
            try:
                lat, lng = float(parts[0].strip()), float(parts[1].strip())
                return LatLng(lat=lat, lng=lng), f"自定义坐标 ({lat:.4f}, {lng:.4f})"
            except ValueError:
                pass

        # Deterministic hash offset around Beijing or SF center so arbitrary text still works in Demo mode
        h = sum((i + 1) * ord(ch) for i, ch in enumerate(raw))
        dlat = ((h % 140) - 70) * 0.0009
        dlng = (((h // 7) % 160) - 80) * 0.0011
        if any(ord(c) > 127 for c in raw) or "北京" in city:
            return LatLng(lat=round(39.9650 + dlat, 6), lng=round(116.4100 + dlng, 6)), f"北京市·{raw}（演示解析）"
        return LatLng(lat=round(37.7780 + dlat, 6), lng=round(-122.4180 + dlng, 6)), f"{raw}, San Francisco（Demo）"

    async def search_places(
        self,
        center: LatLng,
        radius_m: int,
        keywords: list[str],
        category: str = "restaurant",
        city: str = "",
    ) -> list[VenueCandidate]:
        max_km = max(12.0, (radius_m / 1000.0) * 3.0)
        nearby = [v for v in _DEMO_VENUES if haversine_km(center, v.coords) <= max_km]
        if not nearby:
            # Sort all demo venues by distance to center and return closest 6
            nearby = sorted(_DEMO_VENUES, key=lambda v: haversine_km(center, v.coords))[:6]
        return nearby

    async def compute_travel_matrix(
        self,
        participants: list[ResolvedParticipant],
        venues: list[VenueCandidate],
        city: str = "",
    ) -> list[list[tuple[float, float, str]]]:
        matrix: list[list[tuple[float, float, str]]] = []
        for p in participants:
            row: list[tuple[float, float, str]] = []
            base_speed = URBAN_SPEED_KMH.get(p.transport_mode, 22.0)
            for v in venues:
                straight_km = haversine_km(p.coords, v.coords)
                # Realistic Manhattan/ring-road detour factor
                detour = 1.34 if p.transport_mode == "driving" else 1.26
                dist_km = max(0.3, straight_km * detour)

                if p.transport_mode == "transit":
                    # Access walk + wait penalty (7 min) + hub bonus if subway_hint exists
                    hub_discount = 0.88 if v.subway_hint else 1.08
                    dur_min = 7.0 + (dist_km / base_speed) * 60.0 * hub_discount
                    summary = v.subway_hint.split("，")[0] if v.subway_hint else f"地铁公交 {dist_km:.1f} km"
                elif p.transport_mode == "driving":
                    # Parking/signal overhead (4.5 min) + mall garage bonus
                    park_overhead = 3.5 if v.parking_hint else 6.5
                    dur_min = park_overhead + (dist_km / base_speed) * 60.0
                    summary = f"驾车 {dist_km:.1f} km"
                elif p.transport_mode == "cycling":
                    dur_min = 1.5 + (dist_km / base_speed) * 60.0
                    summary = f"骑行 {dist_km:.1f} km"
                else:
                    dur_min = (dist_km / base_speed) * 60.0
                    summary = f"步行 {dist_km:.1f} km"

                row.append((round(max(3.0, dur_min), 1), round(dist_km, 2), summary))
            matrix.append(row)
        return matrix
