from __future__ import annotations

import json
import secrets
import string
import threading
from pathlib import Path

from lets_meet.models import ParticipantInput, RecommendResponse, RoomCreateRequest, RoomState

_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_DEFAULT_STORE = Path(__file__).resolve().parents[2] / "data" / "rooms.json"


class RoomStore:
    """Collaborative session store with disk persistence so room links survive restarts."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _DEFAULT_STORE
        self._lock = threading.RLock()
        self._rooms: dict[str, RoomState] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return
            for code, payload in raw.items():
                try:
                    self._rooms[str(code).upper()] = RoomState.model_validate(payload)
                except Exception:
                    continue
        except Exception:
            self._rooms = {}

    def _persist(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        payload = {code: room.model_dump(mode="json") for code, room in self._rooms.items()}
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._path)

    def _new_code(self) -> str:
        for _ in range(20):
            code = "".join(secrets.choice(_ALPHABET) for _ in range(6))
            if code not in self._rooms:
                return code
        return "".join(secrets.choice(string.ascii_uppercase) for _ in range(8))

    def create(self, req: RoomCreateRequest) -> RoomState:
        with self._lock:
            code = self._new_code()
            host = req.host.model_copy(update={"id": req.host.id or "p1"})
            state = RoomState(
                room_code=code,
                title=req.title,
                city=req.city,
                venue_category=req.venue_category,
                fairness_mode=req.fairness_mode,
                meetup_time=req.meetup_time,
                shared_prompt=req.shared_prompt,
                participants=[host],
            )
            self._rooms[code] = state
            self._persist()
            return state

    def get(self, code: str) -> RoomState | None:
        with self._lock:
            return self._rooms.get(code.strip().upper())

    def upsert_participant(
        self,
        code: str,
        participant: ParticipantInput,
        city: str | None = None,
        venue_category: str | None = None,
        shared_prompt: str | None = None,
        fairness_mode: str | None = None,
        meetup_time: str | None = None,
    ) -> RoomState:
        with self._lock:
            room = self._rooms.get(code.strip().upper())
            if room is None:
                raise KeyError(f"房间 {code} 不存在或已过期")

            replaced = False
            for idx, existing in enumerate(room.participants):
                if existing.id == participant.id or existing.name == participant.name:
                    room.participants[idx] = participant
                    replaced = True
                    break
            if not replaced:
                if not participant.id or participant.id == "p1":
                    participant = participant.model_copy(update={"id": f"p{len(room.participants) + 1}"})
                room.participants.append(participant)

            if city is not None:
                room.city = city
            if venue_category is not None:
                room.venue_category = venue_category
            if shared_prompt is not None:
                room.shared_prompt = shared_prompt
            if fairness_mode is not None:
                room.fairness_mode = fairness_mode  # type: ignore[assignment]
            if meetup_time is not None:
                room.meetup_time = meetup_time
            self._persist()
            return room

    def save_result(self, code: str, result: RecommendResponse) -> RoomState:
        with self._lock:
            room = self._rooms.get(code.strip().upper())
            if room is None:
                raise KeyError(f"房间 {code} 不存在")
            room.last_result = result
            self._persist()
            return room

    def toggle_vote(self, code: str, participant_id: str, place_id: str) -> RoomState:
        with self._lock:
            room = self._rooms.get(code.strip().upper())
            if room is None:
                raise KeyError(f"房间 {code} 不存在")
            voters = room.votes.setdefault(place_id, [])
            if participant_id in voters:
                voters.remove(participant_id)
            else:
                voters.append(participant_id)
            self._persist()
            return room
