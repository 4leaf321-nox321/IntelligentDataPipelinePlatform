"""표 글을 행으로 — **파일이든 붙여 넣기든 같은 규칙.**

CSV(쉼표)·탭 구분(엑셀에서 복사한 것)·JSON(객체 배열 또는 `{"rows": [...]}`) 을 받는다.
BOM 을 벗긴다 — 엑셀이 붙이는 것이라 안 벗기면 첫 열 이름이 `\\ufeffkey` 가 되어 「모르는
열」 로 거절된다. 세미콜론은 구분자로 안 본다 — 여러 값의 구분자(`;`)와 겹친다.
글자는 UTF-8 이 먼저, 안 되면 CP949(`decode_text`).

객체 일괄 입력·부서 붙여 넣기·표에서 타입 만들기가 전부 이것을 쓴다.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any


class TabularError(ValueError):
    """읽을 수 없는 표 — 부르는 쪽이 자기 오류 코드로 감싼다."""


def decode_text(raw: bytes) -> str:
    """바이트 → 글. UTF-8(BOM 이 있으면 벗긴다)이 먼저, 안 되면 CP949.

    한국어 Windows 엑셀의 「CSV (쉼표로 분리)」 는 CP949 로 저장된다 — 사람이 가장 흔히
    만드는 CSV 가 그것이다. UTF-8 만 받았더니 일괄 입력 작업이 `UnicodeDecodeError` 한 줄로
    실패하고, 표에서 타입 추론은 500 이 났다. 정제 키트(`pipeline/`)도 같은 순서로 읽는다.
    잘못 짚어 글자가 깨져도 넣기 전에 계획에서 사람 눈에 보인다.
    """
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    try:
        return raw.decode("cp949")
    except UnicodeDecodeError as caught:
        raise TabularError(
            "글자를 읽을 수 없습니다 — UTF-8 도 CP949 도 아닙니다"
            f"({caught.start}번째 바이트). 엑셀이면 「CSV UTF-8 (쉼표로 분리)」 로 "
            "저장해 다시 올리세요."
        ) from None


def parse_rows(name: str, raw: bytes) -> list[dict[str, Any]]:
    text = decode_text(raw)
    stripped = text.lstrip()
    if name.lower().endswith(".json") or stripped.startswith(("{", "[")):
        try:
            data = json.loads(text)
        except json.JSONDecodeError as caught:
            raise TabularError(f"JSON 을 읽을 수 없습니다: {caught}") from None
        rows = data.get("rows") if isinstance(data, dict) else data
        if not isinstance(rows, list) or not all(isinstance(one, dict) for one in rows):
            raise TabularError('JSON 은 객체의 배열이거나 {"rows": [...]} 여야 합니다.')
        return rows
    # 구분자 — 첫 줄에 탭이 있으면 탭(엑셀 복사), 아니면 쉼표.
    first = text.split("\n", 1)[0]
    delimiter = "\t" if "\t" in first else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    out: list[dict[str, Any]] = []
    for row in reader:
        # 열 이름의 앞뒤 공백은 사람 눈에 안 보이는 오타다.
        out.append({(k or "").strip(): v for k, v in row.items() if k is not None})
    return out
