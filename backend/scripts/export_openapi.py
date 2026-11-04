"""OpenAPI 스키마를 파일로 뽑는다 — **프론트 타입의 원본이다.**

    python scripts/export_openapi.py
    cd ../frontend && npm run api:types

손으로 적은 프론트 타입은 반드시 서버와 어긋난다. 어긋난 날 화면은 아무 말도
안 하고 undefined 를 그린다.

버전 도장은 지운다(version.as_baseline). 안 지우면 버전을 올릴 때마다 이 파일이
바뀌어, "API 가 바뀌었나" 를 이 파일의 diff 로 볼 수 없게 된다.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# **틀의 기본 이름으로 만든다.** 개발 PC 의 .env 에 APP_NAME · EXTENSIONS 가 있으면 제목과
# 확장 라우트가 섞여 들어가고, CI(기본값)가 만든 것과 달라 「생성물 최신성」 검사가 깨진다
# (실측 — v0.4.5). 저장소의 openapi.json 은 설치가 아니라 코드의 산출물이다.
os.environ.update(
    APP_NAME="StandardPlatform", APP_SLUG="standardplatform", APP_TAGLINE="", EXTENSIONS=""
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import version
from app.main import app

OUT = Path(__file__).resolve().parents[1] / "openapi.json"


def main() -> int:
    schema = version.as_baseline(app.openapi())
    OUT.write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{OUT} 에 썼습니다 ({len(schema.get('paths', {}))} 경로).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
