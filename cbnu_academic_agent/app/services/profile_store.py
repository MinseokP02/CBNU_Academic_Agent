from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.schemas import ProfileSettings


def profile_path() -> Path:
    return get_settings().data_dir / "profiles.json"


def load_profiles() -> dict[str, dict[str, Any]]:
    path = profile_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_profiles(profiles: dict[str, dict[str, Any]]) -> None:
    path = profile_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(profiles, ensure_ascii=False, indent=2), encoding="utf-8")


def get_profile(session_id: str) -> ProfileSettings:
    profiles = load_profiles()
    data = profiles.get(session_id, {"session_id": session_id})
    return ProfileSettings(**data)


def upsert_profile(profile: ProfileSettings) -> ProfileSettings:
    profiles = load_profiles()
    data = profile.model_dump()
    data["updated_at"] = datetime.now().isoformat(timespec="seconds")
    profiles[profile.session_id] = data
    save_profiles(profiles)
    return ProfileSettings(**data)


def format_profile_context(profile: ProfileSettings | dict[str, Any] | None) -> str:
    if profile is None:
        return "설정된 사용자 프로필 없음"
    if isinstance(profile, ProfileSettings):
        data = profile.model_dump()
    else:
        data = profile

    parts = []
    labels = {
        "name": "이름",
        "college": "단과대학",
        "department": "학과",
        "grade": "학년",
        "student_type": "학적/구분",
        "memo": "메모",
    }
    for key, label in labels.items():
        value = data.get(key)
        if value:
            parts.append(f"{label}: {value}")
    interests = data.get("interests") or []
    if interests:
        parts.append(f"관심 항목: {', '.join(interests)}")
    return "\n".join(parts) if parts else "설정된 사용자 프로필 없음"
