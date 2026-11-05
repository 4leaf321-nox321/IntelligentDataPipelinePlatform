"""파일 원천 — CSV · Excel(xlsx) · JSON. 서버의 폴더, **사내 공유 폴더(SMB)**, URL 로 받는
파일.

    {"kind": "file", "location": "/data/relay/drop/mes/lots-*.csv", "key": "LOT_ID"}
    {"kind": "file", "location": "smb://mesfs01/export/daily/lots-*.csv", "key": "LOT_ID",
     "auth": {"kind": "basic", "user": "CORP\\svc_relay", "token_env": "MES_SHARE_PASSWORD"}}

- 공유 폴더는 `smb://<서버>[:포트]/<공유>/<폴더…>/<파일>` — 서버에 마운트하지 않고 직접 읽는다
  (SMB2/3, NTLM). 계정은 `auth.user`, 비밀번호는 `auth.token_env` 의 환경 변수. `*` 는 마지막
  자리(파일 이름)에만 — 폴더 이름에는 못 쓴다.

- 파일은 **언제나 그때의 전부**다 — 증분이 없다. 그래서 늘 전량으로 읽고(`full`), 이번에 안 온
  행은 미러에서 빠져 다음 비교에서 무덤이 된다(지운 것). 삭제 칸(`deleted`)이 있으면 그것도
  본다.
- `location` 에 `*` 가 있으면 맞는 파일 중 **가장 늦게 바뀐 것** 하나를 읽는다(날짜가 붙는
  내보내기).
- `columns: "*"` 면 머리글 그대로 열이 된다. 이름을 바꾸려면 `{"열": "머리글"}`(JSON 은 점
  자리).
- 인코딩 `auto` 는 BOM 붙은 UTF-8 → UTF-8 → CP949 차례(사내 엑셀 CSV 가 흔히 CP949 다).
- 워터마크는 내용의 sha256 — 같은 파일이면 같은 값이다(상태 화면에서 「어느 파일을 읽었나」).
"""

from __future__ import annotations

import csv
import fnmatch
import glob
import hashlib
import io
import json
import os
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import unquote, urlsplit

import httpx

from relay_core.adapters.base import Fetched
from relay_core.adapters.explore import suggest
from relay_core.adapters.rest import Auth, SourceError, as_text, truthy, value_at
from relay_core.records import SourceRecord


@dataclass(frozen=True)
class FileSource:
    location: str
    """파일 경로(`*` 로 여럿 중 최신) · `smb://서버/공유/…/파일` · `http(s)://` 주소."""
    key: str
    columns: Mapping[str, str] | str = "*"
    format: str = "auto"
    """csv · xlsx · json · auto(확장자로)."""
    encoding: str = "auto"
    delimiter: str = ","
    sheet: str = ""
    """xlsx 의 시트 이름 — 비우면 첫 시트."""
    header_row: int = 1
    rows_path: str = ""
    """json: 목록이 있는 자리(점으로). 비우면 문서 자체가 목록."""
    deleted: str | None = None
    updated_at: str | None = None
    auth: Auth = field(default_factory=Auth)
    timeout: float = 60.0
    retries: int = 3


def _cell(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return as_text(value)


class SmbFiles(Protocol):
    """공유 폴더를 읽는 길 — 시험은 가짜를 끼운다."""

    def listdir(self, folder: str) -> list[tuple[str, float]]:
        """`\\\\서버\\공유\\폴더` 안의 **파일** `(이름, 바뀐 시각)`."""
        ...

    def read(self, path: str) -> bytes: ...


class _SmbClient:
    """`smbprotocol` 의 `smbclient` — 부를 때마다 계정을 넘기고, 세션은 그쪽이 묶어 둔다."""

    def __init__(self, server: str, port: int, source: FileSource) -> None:
        auth = source.auth
        self._kwargs: dict[str, Any] = {
            "port": port,
            "connection_timeout": int(source.timeout),
        }
        if auth.kind == "basic":
            self._kwargs.update(username=auth.user, password=auth.token)
        elif auth.kind != "none":
            raise SourceError(
                "공유 폴더의 auth.kind 는 basic(계정 · 비밀번호) 또는 none 입니다."
            )
        self._server = server

    def listdir(self, folder: str) -> list[tuple[str, float]]:
        import smbclient

        return [
            (entry.name, entry.stat().st_mtime)
            for entry in smbclient.scandir(folder, **self._kwargs)
            if entry.is_file()
        ]

    def read(self, path: str) -> bytes:
        import smbclient

        with smbclient.open_file(path, mode="rb", **self._kwargs) as handle:
            body: bytes = handle.read()
        return body


class FileAdapter:
    def __init__(
        self,
        source: FileSource,
        *,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        roots: Sequence[Path] | None = None,
        smb: SmbFiles | None = None,
    ) -> None:
        self.source = source
        self._client = client or httpx.Client(timeout=source.timeout)
        self._sleep = sleep
        self.roots = tuple(one.resolve() for one in roots) if roots is not None else None
        """읽어도 되는 폴더 — 주면 **실제로 고른 파일**(링크를 따라간 끝)이 그 안이어야 한다.
        `None` 이면 안 본다(CLI · 시험)."""
        self._smb = smb

    # --- 읽기 ---------------------------------------------------------------------------

    def _resolve(self) -> tuple[str, bytes]:
        where = self.source.location
        if where.startswith(("http://", "https://")):
            last = ""
            for attempt in range(self.source.retries):
                try:
                    got = self._client.get(where, headers=self.source.auth.headers())
                except httpx.HTTPError as failure:
                    last = f"닿지 않습니다: {failure}"
                else:
                    if got.status_code == 200:
                        return where.rsplit("/", 1)[-1].split("?")[0], got.content
                    last = f"{got.status_code}"
                    if got.status_code < 500:
                        raise SourceError(f"파일을 못 받았습니다({where}): {last}")
                self._sleep(min(2**attempt, 10))
            raise SourceError(
                f"파일을 {self.source.retries}번 받으려 했지만 실패했습니다: {last}"
            )
        if where.startswith("smb://"):
            return self._from_share(where)
        matches = (
            sorted(glob.glob(where), key=os.path.getmtime)
            if any(c in where for c in "*?[")
            else [where]
        )
        if not matches or not Path(matches[-1]).is_file():
            raise SourceError(f"파일이 없습니다: {where}")
        path = Path(matches[-1])
        if self.roots is not None:
            real = path.resolve()
            if not any(real.is_relative_to(root) for root in self.roots):
                raise SourceError(
                    f"읽어도 되는 폴더(RELAY_FILE_ROOTS) 밖의 파일입니다: {path}"
                )
        return path.name, path.read_bytes()

    def _from_share(self, where: str) -> tuple[str, bytes]:
        """공유 폴더에서 — 폴더를 훑어 이름이 맞는 것 중 가장 늦게 바뀐 것 하나."""
        parts = urlsplit(where)
        if parts.username or parts.password:
            raise SourceError(
                "smb 주소에 계정을 적지 않습니다 — auth.user · auth.token_env 를 쓰세요."
            )
        segments = [unquote(one) for one in parts.path.strip("/").split("/") if one]
        if not parts.hostname or len(segments) < 2:
            raise SourceError(f"smb://<서버>/<공유>/…/<파일> 이어야 합니다: {where}")
        folder, pattern = segments[:-1], segments[-1]
        if any(c in one for one in folder for c in "*?["):
            raise SourceError(f"폴더 이름에는 * 를 못 씁니다 — 파일 이름에만: {where}")
        unc = "\\\\" + parts.hostname + "\\" + "\\".join(folder)
        files = self._smb or _SmbClient(parts.hostname, parts.port or 445, self.source)
        last = ""
        for attempt in range(max(1, self.source.retries)):
            try:
                found = [
                    (name, mtime)
                    for name, mtime in files.listdir(unc)
                    if fnmatch.fnmatchcase(name.lower(), pattern.lower())
                ]
                if not found:
                    raise SourceError(f"파일이 없습니다: {where}")
                name = max(found, key=lambda one: one[1])[0]
                return name, files.read(f"{unc}\\{name}")
            except SourceError:
                raise
            except Exception as failure:  # smbprotocol 은 여러 갈래의 예외를 던진다
                last = f"{type(failure).__name__}: {failure}"
            self._sleep(min(2**attempt, 10))
        raise SourceError(
            f"공유 폴더를 {self.source.retries}번 읽으려 했지만 실패했습니다: {last}"
        )

    def _format(self, name: str) -> str:
        if self.source.format != "auto":
            return self.source.format
        suffix = name.lower().rsplit(".", 1)[-1] if "." in name else ""
        if suffix in ("csv", "txt", "tsv"):
            return "csv"
        if suffix in ("xlsx", "xlsm"):
            return "xlsx"
        if suffix == "json":
            return "json"
        raise SourceError(
            f"형식을 모릅니다({name}) — format 을 csv · xlsx · json 으로 적으세요."
        )

    def _decode(self, raw: bytes) -> str:
        tries = (
            ("utf-8-sig", "utf-8", "cp949")
            if self.source.encoding == "auto"
            else (self.source.encoding,)
        )
        for one in tries:
            try:
                return raw.decode(one)
            except UnicodeDecodeError:
                continue
        raise SourceError(
            f"인코딩을 못 읽었습니다({', '.join(tries)}) — encoding 을 적으세요."
        )

    def rows(self) -> tuple[str, str, list[dict[str, Any]]]:
        """(파일 이름, sha256, 행들) — 행은 머리글 → 값(JSON 은 항목 그대로)."""
        name, raw = self._resolve()
        digest = hashlib.sha256(raw).hexdigest()
        kind = self._format(name)
        skip = max(self.source.header_row - 1, 0)
        if kind == "csv":
            lines = io.StringIO(self._decode(raw), newline="")
            for _ in range(skip):
                lines.readline()
            delimiter = "\t" if name.lower().endswith(".tsv") else self.source.delimiter
            reader = csv.DictReader(lines, delimiter=delimiter)
            return (
                name,
                digest,
                [
                    {str(k).strip(): v for k, v in row.items() if k is not None}
                    for row in reader
                ],
            )
        if kind == "xlsx":
            from openpyxl import load_workbook

            book = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
            sheet = book[self.source.sheet] if self.source.sheet else book.worksheets[0]
            values = list(sheet.iter_rows(values_only=True))[skip:]
            if not values:
                return name, digest, []
            header = [str(one).strip() if one is not None else "" for one in values[0]]
            out = [
                {
                    header[i]: _cell(cell)
                    for i, cell in enumerate(row)
                    if i < len(header) and header[i]
                }
                for row in values[1:]
                if any(cell not in (None, "") for cell in row)
            ]
            return name, digest, out
        if kind == "json":
            body = json.loads(self._decode(raw))
            items = value_at(body, self.source.rows_path) if self.source.rows_path else body
            if not isinstance(items, list):
                raise SourceError(
                    f"JSON 의 행 자리({self.source.rows_path or '문서'})가 목록이 아닙니다."
                )
            return name, digest, items
        raise SourceError(f"모르는 형식입니다: {kind}")

    def _columns(self, sample: dict[str, Any]) -> Mapping[str, str]:
        if self.source.columns == "*":
            return {
                str(key): str(key)
                for key in sample
                if not isinstance(sample[key], (dict, list))
            }
        assert isinstance(self.source.columns, Mapping)
        return self.source.columns

    def _record(self, row: Any, columns: Mapping[str, str]) -> SourceRecord:
        if not isinstance(row, dict):
            raise SourceError(f"행이 객체가 아닙니다: {row!r:.200}")
        key = _cell(value_at(row, self.source.key)).strip()
        if not key:
            raise SourceError(f"식별자({self.source.key})가 빈 행이 있습니다: {row!r:.200}")
        deleted = bool(self.source.deleted) and truthy(
            value_at(row, self.source.deleted or "")
        )
        values = (
            {}
            if deleted
            else {out: _cell(value_at(row, path)) for out, path in columns.items()}
        )
        updated = (
            _cell(value_at(row, self.source.updated_at)) if self.source.updated_at else ""
        )
        return SourceRecord(
            key=key, values=values, updated_at=updated or None, deleted=deleted
        )

    # --- 약속 ----------------------------------------------------------------------------

    def fetch(self, since: str | None, *, limit: int | None = None) -> Fetched:
        """늘 전량이다 — `since` 는 보지 않는다(파일에는 「그 뒤로 바뀐 것」 이 없다). `limit`
        이면 앞 그만큼만(시험용 — `complete=False`)."""
        _name, digest, rows = self.rows()
        columns = self._columns(rows[0]) if rows else {}
        if limit and len(rows) > limit:
            records = [self._record(row, columns) for row in rows[:limit]]
            return Fetched(records=records, complete=False)
        records = [self._record(row, columns) for row in rows]
        return Fetched(
            records=records, watermark=f"sha256:{digest[:16]}", complete=True, full=True
        )

    def probe(self) -> str:
        name, digest, rows = self.rows()
        heads = list(rows[0])[:8] if rows and isinstance(rows[0], dict) else []
        return f"{name} · {len(rows)}행 · sha256 {digest[:12]} · 열 {', '.join(heads)}"

    def discover(self) -> dict[str, Any]:
        name, digest, rows = self.rows()
        return {"file": name, "sha256": digest[:16], "rows": len(rows), **suggest(rows)}
