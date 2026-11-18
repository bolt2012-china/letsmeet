from __future__ import annotations

import json
import re
from lets_meet.config import RuntimeConfig
from lets_meet.llm.free_web import FreeWebLLMCascade
from lets_meet.models import ParsedIntent, ParticipantInput, RankedVenue


_CATEGORY_DEFAULTS: dict[str, list[str]] = {
    "any": ["地铁站", "商场", "广场"],
    "station": ["地铁站", "车站"],
    "landmark": ["广场", "商场", "公园"],
    "restaurant": ["餐厅", "商场"],
    "cafe": ["咖啡馆", "茶室"],
    "bar": ["清吧", "小酒馆"],
    "hotpot": ["火锅"],
    "japanese": ["日料", "居酒屋"],
    "western": ["西餐", "Bistro"],
}

_KNOWN_KEYWORDS = [
    "地铁站", "火车站", "高铁站", "车站", "广场", "公园", "商场", "图书馆", "博物馆", "书店", "江边",
    "火锅", "日料", "寿司", "居酒屋", "烤肉", "川菜", "湘菜", "粤菜", "茶餐厅", "江浙菜", "本帮菜",
    "云南菜", "泰餐", "西餐", "意大利菜", "Bistro", "咖啡", "清吧", "Brunch",
    "station", "subway", "park", "plaza", "mall", "library", "ramen", "sushi", "italian", "coffee", "cafe",
]


def _extract_json_obj(text: str) -> dict | None:
    text = text.strip()
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    if fence:
        text = fence.group(1)
    else:
        brace = re.search(r"(\{.*\})", text, flags=re.DOTALL)
        if brace:
            text = brace.group(1)
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


class MeetingLLMAgent:
    """
    Parses multi-party meetup requirements (transit hubs, public landmarks, or dining spots)
    and writes natural, plain-spoken reasons for the top candidate places.
    """

    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config
        self.cascade = FreeWebLLMCascade(config)

    @property
    def is_enabled(self) -> bool:
        return self.config.llm_provider != "disabled"

    async def _chat_json(self, system_prompt: str, user_prompt: str, timeout: float = 12.0) -> tuple[dict | None, str]:
        if not self.is_enabled:
            return None, ""
        raw_text, source_label = await self.cascade.chat_completion(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            timeout=timeout,
        )
        if not raw_text:
            return None, ""
        parsed = _extract_json_obj(raw_text)
        return (parsed, source_label) if parsed else (None, "")

    def _heuristic_intent(
        self,
        participants: list[ParticipantInput],
        venue_category: str,
        shared_prompt: str,
    ) -> ParsedIntent:
        combined = " ".join([shared_prompt] + [p.preference_note for p in participants]).strip()
        lower = combined.lower()

        need_parking = any(p.transport_mode == "driving" for p in participants) or any(
            k in lower for k in ("停车", "好停", "开车", "自驾", "车库", "parking", "drive")
        )
        near_subway = (
            venue_category == "station"
            or any(p.transport_mode == "transit" for p in participants)
            or any(k in lower for k in ("地铁", "公交", "直达", "站", "subway", "metro", "bart", "transit"))
        )
        quiet_vibe = any(k in lower for k in ("安静", "聊天", "包间", "不吵", "图书馆", "quiet", "cozy", "chat"))

        matched_kws = [c for c in _KNOWN_KEYWORDS if c.lower() in lower]
        filtered_kws: list[str] = []
        for c in matched_kws:
            neg_pattern = rf"(不吃|不想吃|不要|不去|忌口|avoid|no\s+){re.escape(c.lower())}"
            if not re.search(neg_pattern, lower):
                filtered_kws.append(c)

        budget_match = re.search(r"(人均\s*\d+|[¥$]\s*\d+|\d+\s*元)", combined)
        budget_hint = budget_match.group(1) if budget_match else ""

        keywords = filtered_kws[:3] if filtered_kws else list(
            _CATEGORY_DEFAULTS.get(venue_category, [venue_category or "地标"])
        )

        notes: list[str] = []
        if venue_category == "station":
            notes.append("按换乘车站和交通枢纽筛选，方便碰头后一起走")
        elif venue_category == "landmark":
            notes.append("按商场、广场、公园等好认的公共地标筛选")
        elif filtered_kws:
            notes.append(f"优先找{'/'.join(filtered_kws[:2])}")

        if "不辣" in combined or "清淡" in combined or "忌辣" in combined:
            notes.append("避开重辣")
        if need_parking and near_subway:
            notes.append("同时照顾开车停车和地铁出站")
        elif need_parking:
            notes.append("优先带车库的地方")
        elif near_subway and venue_category != "station":
            notes.append("靠近地铁口")
        if quiet_vibe:
            notes.append("找相对安静的地方")

        summary = "，".join(notes) if notes else "按双方实际路程时间找折中碰面点"
        return ParsedIntent(
            search_keywords=keywords,
            cuisine_tags=filtered_kws,
            need_parking=need_parking,
            near_subway=near_subway,
            quiet_vibe=quiet_vibe,
            budget_hint=budget_hint,
            compromise_summary=summary,
            used_llm=False,
            llm_source="本地规则",
        )

    async def parse_joint_intent(
        self,
        participants: list[ParticipantInput],
        venue_category: str,
        shared_prompt: str,
        city: str = "",
    ) -> ParsedIntent:
        baseline = self._heuristic_intent(participants, venue_category, shared_prompt)
        if not self.is_enabled or not self.cascade.has_dedicated_channel():
            return baseline

        sys_prompt = (
            "你帮几个人找折中碰面地点（可以是地铁站、火车站、商场、广场、公园、图书馆，也可以是餐厅或咖啡馆）。"
            "只输出纯 JSON，不要解释，不要用套话。字段："
            '{"search_keywords": ["1到3个地图搜索词，如地铁站、公园、日料"], '
            '"cuisine_tags": ["具体标签"], '
            '"need_parking": true, "near_subway": true, "quiet_vibe": false, '
            '"budget_hint": "预算或空串", '
            '"compromise_summary": "用大白话一句话说明怎么同时顾及几个人，别用赋能/协同/平衡带这类词"}'
        )
        p_lines = [
            f"- {p.name}（交通：{p.transport_mode}，起点：{p.address}）：{p.preference_note or '无特别说明'}"
            for p in participants
        ]
        user_prompt = (
            f"城市：{city or '自动'}\n"
            f"地点类型：{venue_category}\n"
            f"补充说明：{shared_prompt or '无'}\n"
            f"同行人：\n" + "\n".join(p_lines)
        )
        llm_json, source_label = await self._chat_json(sys_prompt, user_prompt, timeout=16.0)
        if not llm_json:
            return baseline

        kws = [str(x).strip() for x in llm_json.get("search_keywords", []) if str(x).strip()][:3]
        return ParsedIntent(
            search_keywords=kws or baseline.search_keywords,
            cuisine_tags=[str(x).strip() for x in llm_json.get("cuisine_tags", baseline.cuisine_tags)][:5],
            need_parking=bool(llm_json.get("need_parking", baseline.need_parking)),
            near_subway=bool(llm_json.get("near_subway", baseline.near_subway)),
            quiet_vibe=bool(llm_json.get("quiet_vibe", baseline.quiet_vibe)),
            budget_hint=str(llm_json.get("budget_hint", baseline.budget_hint)),
            compromise_summary=str(llm_json.get("compromise_summary", baseline.compromise_summary)),
            used_llm=True,
            llm_source=source_label,
        )

    async def rerank_and_explain(
        self,
        ranked: list[RankedVenue],
        participants: list[ParticipantInput],
        intent: ParsedIntent,
        shared_prompt: str,
    ) -> tuple[list[RankedVenue], bool, str]:
        if not self.is_enabled or not ranked:
            return ranked, False, ""

        top_slice = ranked[:6]
        candidates_digest = []
        for item in top_slice:
            v = item.venue
            candidates_digest.append(
                {
                    "place_id": v.place_id,
                    "name": v.name,
                    "address": v.address,
                    "categories": v.categories,
                    "rating": v.rating,
                    "price_level": v.price_level,
                    "subway_hint": v.subway_hint,
                    "parking_hint": v.parking_hint,
                    "legs": [
                        {"name": l.participant_name, "mode": l.transport_mode, "minutes": l.duration_minutes}
                        for l in item.legs
                    ],
                }
            )

        sys_prompt = (
            "你帮朋友挑碰面地点（可能是车站、广场、公园、商场或餐厅）。"
            "必须严格针对每条候选数据自身的 name、address 和双方耗时来写，严禁张冠李戴提及其他不在此条里的站名或店名。"
            "说话像本地熟人指路，实在、简短，禁止使用“赋能、契合、优选、平衡带、闭环”等AI腔词汇，也不要用破折号。"
            "只输出纯 JSON："
            '{"evaluations": [{"place_id": "...", "semantic_score": 88, '
            '"ai_reason": "35字内大白话说明为什么约这儿合适", '
            '"transport_tip": "20字内碰头口或停车提醒"}]}'
        )
        user_prompt = (
            f"诉求：{intent.compromise_summary} | 备注：{shared_prompt}\n"
            f"参与者：{[{'name': p.name, 'mode': p.transport_mode, 'note': p.preference_note} for p in participants]}\n"
            f"候选地点：{json.dumps(candidates_digest, ensure_ascii=False)}"
        )
        llm_json, source_label = await self._chat_json(sys_prompt, user_prompt, timeout=20.0)
        if not llm_json or "evaluations" not in llm_json:
            return ranked, False, ""

        eval_map = {
            str(e.get("place_id")): e
            for e in llm_json.get("evaluations", [])
            if isinstance(e, dict) and e.get("place_id")
        }
        if not eval_map:
            return ranked, False, ""

        for item in top_slice:
            ev = eval_map.get(item.venue.place_id)
            if not ev:
                continue
            try:
                llm_sem = float(ev.get("semantic_score", item.semantic_score))
                item.semantic_score = round(max(30.0, min(99.0, 0.35 * item.semantic_score + 0.65 * llm_sem)), 1)
            except (TypeError, ValueError):
                pass
            if ev.get("ai_reason"):
                item.ai_reason = str(ev["ai_reason"]).strip()
            if ev.get("transport_tip"):
                item.transport_tip = str(ev["transport_tip"]).strip()
            item.total_score = round(
                0.52 * item.fairness_score + 0.23 * item.quality_score + 0.25 * item.semantic_score,
                1,
            )

        ranked.sort(key=lambda r: (-r.total_score, r.max_duration_minutes, r.duration_gap_minutes))
        for idx, r in enumerate(ranked, start=1):
            r.rank = idx
        return ranked, True, source_label
