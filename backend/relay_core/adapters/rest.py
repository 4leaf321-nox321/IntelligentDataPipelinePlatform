"""REST JSON 원천 — 쪽 넘김 · 증분 · 삭제 표시 · 중첩 펴기 · 인증.

SP 의 데이터 소스(`rest`)가 읽는 것과 같은 축(쪽 넘김 · 인증)을 받되, 그쪽이 못 하는 일을
한다: **중첩 JSON 을 평평한 열로 편다**(`task.code` → `TASK_CODE`). 레시피(`sp-table/1`)는
평평한 표만 읽으므로 펴는 규칙은 여기 — 어댑터 설정의 `columns` — 에 한 번만 적는다.

- **목록 펴기**(`explode`) — 항목 안의 목록(`lines`)의 원소마다 한 행. `columns` 의 자리가
  `lines.` 로 시작하면 그 원소에서, 아니면 항목에서 읽는다. 식별자는 원소를 가려야 한다
  (`"key": ["id", "lines.no"]`).
- **여러 칸 식별자** — `key` 를 목록으로 적으면 그 값들을 `|` 로 잇는다.
- 쪽 넘김: `page` · `offset` · `cursor`(값을 질의에) · `next_url`(응답의 다음 주소를 그대로 —
  같은 호스트만) · `link`(`Link: <…>; rel="next"` 머리글) · `none`.
- **POST 조회**(`method: "POST"` + `body`) — 검색형 API · GraphQL. 본문의 `{since}` ·
  `{page}` · `{page_size}` · `{offset}` · `{cursor}` 자리에 값이 든다. 읽기만 한다 — 다른
  방식은 없다.
- **목록 → 상세**(`detail_path: "/models/{id}"`) — 항목마다 상세를 받아 `detail.` 아래에
  붙인다.
- `min_interval` — 요청 사이의 최소 간격(초). 초당 몇 건으로 막힌 API.
- 인증: none · bearer · header · basic · query(질의 문자열의 키) · oauth2(client credentials —
  토큰을 받아 만료 전까지 다시 쓴다).

재시도는 SP 연동 지침서 §8 을 따른다: 5xx · 네트워크 오류만 다시, 4xx 는 다시 보내도 같다(429
만 기다렸다 한 번 더).
"""

from __future__ import annotations

import base64
import contextlib
import re
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urljoin, urlsplit

import httpx

from relay_core.adapters.base import Fetched
from relay_core.records import SourceRecord

#: 여러 값을 한 칸에 — SP 규약(§7)이 `;` 로 잇는다.
MULTI_JOIN = ";"


class SourceError(RuntimeError):
    """원천이 거절했거나 모양이 다르다 — **멈춘다.** 덜 받은 채 넘어가면 부재 대조가 아직 안
    받은
    것을 지운다."""


#: oauth2 의 받은 토큰 — (토큰 주소, client_id, scope) → (토큰, 만료 시각). 프로세스 안에서만.
_TOKENS: dict[tuple[str, str, str], tuple[str, float]] = {}
#: 토큰 주소에 묻는 길 — 시험은 가짜를 끼운다.
TOKEN_POST: Callable[..., httpx.Response] = httpx.post
AUTH_KINDS = ("none", "bearer", "header", "basic", "query", "oauth2")


@dataclass(frozen=True)
class Auth:
    kind: str = "none"
    """none · bearer · header · basic · query · oauth2."""
    token: str = field(default="", repr=False)
    """비밀 — 정의에는 `token_env`(환경 변수의 이름)로 적는다. oauth2 면 client secret."""
    header: str = "Authorization"
    """header: 비밀을 실을 머리글 이름."""
    user: str = ""
    """basic: 계정 · oauth2: client_id."""
    param: str = "api_key"
    """query: 비밀을 실을 질의 이름(`?api_key=…`)."""
    token_url: str = ""
    """oauth2: 토큰을 받는 주소(client credentials). 그 주소도 울타리 안이어야 한다."""
    scope: str = ""
    """oauth2: 요청할 범위(공백으로 여럿)."""

    def headers(self) -> dict[str, str]:
        if self.kind == "bearer":
            return {"Authorization": f"Bearer {self.token}"}
        if self.kind == "header":
            return {self.header: self.token}
        if self.kind == "basic":
            raw = base64.b64encode(f"{self.user}:{self.token}".encode()).decode()
            return {"Authorization": f"Basic {raw}"}
        if self.kind == "oauth2":
            return {"Authorization": f"Bearer {self.access_token()}"}
        if self.kind in ("none", "query"):
            return {}
        raise ValueError(f"모르는 인증 방식입니다: {self.kind}")

    def query(self) -> dict[str, str]:
        """질의 문자열에 실을 비밀 — query 일 때만."""
        return {self.param: self.token} if self.kind == "query" and self.token else {}

    def secrets(self) -> list[str]:
        """오류 글에서 지울 값 — 비밀과 oauth2 로 받은 토큰."""
        out = [self.token] if self.token else []
        cached = _TOKENS.get((self.token_url, self.user, self.scope))
        if cached:
            out.append(cached[0])
        return out

    def access_token(self) -> str:
        """oauth2 client credentials — 받은 토큰을 만료 1분 전까지 다시 쓴다."""
        cache = (self.token_url, self.user, self.scope)
        cached = _TOKENS.get(cache)
        if cached and cached[1] > time.time():
            return cached[0]
        if not self.token_url or not self.user:
            raise SourceError(
                "oauth2 에는 auth.token_url 과 auth.user(client_id)가 필요합니다."
            )
        form = {
            "grant_type": "client_credentials",
            "client_id": self.user,
            "client_secret": self.token,
        }
        if self.scope:
            form["scope"] = self.scope
        try:
            answer = TOKEN_POST(self.token_url, data=form, timeout=30.0)
        except httpx.HTTPError as failure:
            raise SourceError(
                scrub(
                    f"토큰 주소에 닿지 않습니다({self.token_url}): {failure}", {}, [self.token]
                )
            ) from failure
        if answer.status_code != 200:
            said = scrub(answer.text[:300], {}, [self.token])
            raise SourceError(f"토큰 주소가 거절했습니다({answer.status_code}): {said}")
        try:
            body = answer.json()
            access = str(body["access_token"])
            life = float(body.get("expires_in") or 3600)
        except (ValueError, KeyError, TypeError) as failure:
            raise SourceError("토큰 주소의 답에 access_token 이 없습니다.") from failure
        _TOKENS[cache] = (access, time.time() + max(life - 60, 30))
        return access


@dataclass(frozen=True)
class RestSource:
    base_url: str
    path: str
    key: str | Sequence[str]
    """항목에서 **안 바뀌는 식별자**의 자리(점으로 잇는다 — `id`, `model.id`). 여러 칸이면
    목록(`["plant", "lot"]` — 값을 `|` 로 잇는다)."""
    columns: Mapping[str, str]
    """내보낼 열 → 항목 안의 자리. 레시피가 읽는 열 이름이 여기서 정해진다."""
    rows_path: str = "items"
    paging: str = "page"
    """page · offset · cursor · next_url · link · none."""
    page_size: int = 200
    page_param: str = "page"
    size_param: str = "page_size"
    offset_param: str = "offset"
    cursor_param: str = "cursor"
    next_path: str = "next"
    start_page: int = 1
    since_param: str | None = None
    """증분을 받는 질의 이름(`updated_since`). 없으면 늘 전량이다."""
    updated_at: str | None = "updated_at"
    deleted: str | None = "deleted"
    """참이면 지운 것. **원천은 지운 행을 빼지 말고 이 표시로 알려야 한다**(지침서 §3.2)."""
    watermark_path: str | None = None
    """응답이 주는 「여기까지」 (`as_of`). 없으면 받은 행의 `updated_at` 최댓값."""
    params: Mapping[str, str] = field(default_factory=dict)
    auth: Auth = field(default_factory=Auth)
    timeout: float = 30.0
    retries: int = 3
    max_pages: int = 10_000
    explode: str = ""
    """항목 안의 목록 — 원소마다 한 행(`lines`). 식별자에 그 원소의 칸이 있어야 한다."""
    method: str = "GET"
    """GET · POST(검색형 · GraphQL — `body` 가 있어야 한다)."""
    body: Mapping[str, Any] | None = None
    """POST 본문(JSON) — `{since}` · `{page}` · `{page_size}` · `{offset}` · `{cursor}`
    자리."""
    detail_path: str = ""
    """목록 → 상세 — 항목의 칸을 `{자리}` 로 넣은 상세 주소(`/models/{id}`). 비우면 안 한다."""
    detail_rows_path: str = ""
    """상세 응답에서 객체가 있는 자리 — 비우면 응답 자체."""
    detail_as: str = "detail"
    """상세를 항목의 어느 칸 아래에 붙이나 — `columns` 가 `detail.…` 로 읽는다."""
    min_interval: float = 0.0
    """요청 사이의 최소 간격(초) — 0 이면 쉬지 않는다."""


def value_at(item: Any, path: str) -> Any:
    """점으로 이은 자리의 값. 목록을 지나면 그 원소마다 — `tags.name` 은 이름 목록이 된다."""
    current: Any = item
    for part in path.split("."):
        if isinstance(current, list):
            current = [one.get(part) if isinstance(one, dict) else None for one in current]
        elif isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def as_text(value: Any) -> str:
    """표의 한 칸 — 빈 것은 빈 글, 목록은 `;` 로, 불리언은 소문자."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return MULTI_JOIN.join(as_text(one) for one in value if one not in (None, ""))
    if isinstance(value, dict):
        raise SourceError(
            f"평평하지 않은 값입니다 — columns 에 더 깊은 자리를 적으세요: {value!r}"
        )
    return str(value)


def scrub(text: str, headers: Mapping[str, str], extra: Iterable[str] = ()) -> str:
    """원천의 오류 글에서 그 요청에 실은 비밀을 지운다 — 받은 머리글을 그대로 되돌려 찍는
    서버가 있고, 그 글은 작업 오류 · 알림 · MCP 로 퍼진다. `extra` 는 질의에 실은 키 ·
    oauth2 의 비밀처럼 머리글 밖의 비밀."""
    secrets: set[str] = {one for one in extra if one}
    for value in headers.values():
        tail = value.rsplit(" ", 1)[-1]
        secrets |= {value, tail}
        if value.startswith("Basic "):
            with contextlib.suppress(ValueError):  # UnicodeDecodeError · binascii.Error
                secrets.add(base64.b64decode(tail).decode().split(":", 1)[-1])
    for one in sorted((one for one in secrets if len(one) >= 6), key=len, reverse=True):
        text = text.replace(one, "***")
    return text


def request_json(
    client: httpx.Client,
    url: str,
    *,
    params: Mapping[str, Any] | None,
    auth: Auth,
    retries: int,
    sleep: Callable[[float], None],
    method: str = "GET",
    body: Any = None,
    headers: Mapping[str, str] | None = None,
) -> tuple[Any, httpx.Headers]:
    """한 번 묻기 — 5xx · 네트워크 오류 · 429 만 다시 묻고, 그 밖의 4xx 는 바로 멈춘다(§8).
    인증(머리글 · 질의)은 `auth` 가 싣고, 오류 글에서 그 비밀을 지운다. (본문, 머리글)."""
    sent = {"Accept": "application/json", **auth.headers(), **(headers or {})}
    keys = auth.query()
    secrets = [*auth.secrets(), *keys.values()]
    # **주소의 질의를 지키며 합친다** — httpx(0.28)는 `params` 를 주면 빈 것이어도 주소에 있던
    # 질의를 통째로 바꾼다. 응답이 준 다음 쪽 주소(`nextLink` · `next`)의 질의가 사라지면 첫
    # 쪽을 끝없이 다시 받는다.
    # 합칠 것이 없으면 주소 그대로 — 다시 쓰면 서버가 준 질의(`$skiptoken`)가 다시 인코딩된다.
    merged = {**(params or {}), **keys}
    target = httpx.URL(url).copy_merge_params(merged) if merged else httpx.URL(url)
    last = ""
    for attempt in range(retries):
        try:
            answer = client.request(method, target, json=body, headers=sent)
        except httpx.HTTPError as failure:
            last = f"닿지 않습니다: {failure}"
            if "CERTIFICATE_VERIFY_FAILED" in last:
                # 다시 물어도 같다 — 사내 CA 를 알려 줘야 한다(`app/modules/relay/network.py`).
                raise SourceError(
                    f"원천의 인증서를 믿을 수 없습니다({url}): {failure} — 사내 인증서로 "
                    "서명된 주소면 서버의 RELAY_CA_FILE 에 사내 CA(PEM)를 적고 재시작하세요."
                ) from None
        else:
            if answer.status_code == 200:
                return answer.json(), answer.headers
            last = f"{answer.status_code} {answer.text[:2000]}"
            if answer.status_code < 500 and answer.status_code != 429:
                said = scrub(last, sent, secrets)[:300]
                raise SourceError(f"원천이 거절했습니다({url}): {said}")
        sleep(min(2**attempt, 10))
    said = scrub(last, sent, secrets)[:300]
    raise SourceError(f"원천에 {retries}번 물었지만 실패했습니다({url}): {said}")


def get_json(
    client: httpx.Client,
    url: str,
    *,
    params: Mapping[str, Any] | None,
    auth: Auth,
    retries: int,
    sleep: Callable[[float], None],
) -> Any:
    """GET 한 번 — `request_json` 의 본문만."""
    got, _headers = request_json(
        client, url, params=params, auth=auth, retries=retries, sleep=sleep
    )
    return got


def key_paths(key: str | Sequence[str]) -> list[str]:
    """식별자의 자리들 — 하나면 그것만, 여러 칸이면 차례대로."""
    if isinstance(key, str):
        return [key] if key else []
    return [str(one) for one in key if str(one)]


def _under(path: str, explode: str) -> str | None:
    """`path` 가 펼친 목록 안의 자리면 원소 기준의 나머지(원소 자체면 빈 글)."""
    if not explode:
        return None
    if path == explode:
        return ""
    return path[len(explode) + 1 :] if path.startswith(explode + ".") else None


def pick(item: Any, element: Any, path: str, explode: str) -> Any:
    """한 칸의 값 — 펼친 목록 안의 자리면 그 원소에서, 아니면 항목에서."""
    rest = _under(path, explode)
    if rest is None or element is _NONE:
        return value_at(item, path)
    return element if rest == "" else value_at(element, rest)


_NONE = object()
"""펼치지 않을 때의 원소 자리."""


def check_shape(key: str | Sequence[str], explode: str) -> None:
    """펼치면 식별자가 원소를 가려야 한다 — 아니면 한 항목의 원소들이 같은 식별자로 겹쳐
    마지막 것만 남는다(조용히 사라진다)."""
    paths = key_paths(key)
    if explode and not any(_under(one, explode) is not None for one in paths):
        raise SourceError(
            f"explode({explode}) 하면 식별자(key)에 그 원소의 칸이 있어야 합니다 — "
            f"예: {[*paths, explode + '.no']}"
        )


def records_of(
    item: Any,
    *,
    key: str | Sequence[str],
    columns: Mapping[str, str],
    deleted: str | None,
    updated_at: str | None,
    explode: str = "",
    cell: Callable[[Any], str] | None = None,
) -> list[SourceRecord]:
    """항목 하나 → 미러의 행들 — 펼치면 원소마다, 아니면 하나. 삭제 표시는 항목의 것이다
    (펼친 행이면 그 항목의 원소 전부가 지운 것)."""
    if not isinstance(item, dict):
        raise SourceError(f"항목이 객체가 아닙니다: {item!r:.300}")
    text = cell or as_text
    paths = key_paths(key)
    gone = bool(deleted) and truthy(value_at(item, deleted or ""))
    updated = text(value_at(item, updated_at)) if updated_at else ""
    if explode:
        inner = value_at(item, explode)
        elements: list[Any] = inner if isinstance(inner, list) else []
    else:
        elements = [_NONE]
    out: list[SourceRecord] = []
    for element in elements:
        parts = [text(pick(item, element, one, explode)).strip() for one in paths]
        if not parts or not all(parts):
            shown = " · ".join(paths)
            raise SourceError(f"식별자({shown})가 빈 항목입니다: {item!r:.300}")
        values = (
            {}
            if gone
            else {
                out_name: text(pick(item, element, path, explode))
                for out_name, path in columns.items()
            }
        )
        out.append(
            SourceRecord(
                key=KEY_JOIN.join(parts),
                values=values,
                updated_at=updated or None,
                deleted=gone,
            )
        )
    return out


#: 여러 칸 식별자를 잇는 글자.
KEY_JOIN = "|"
#: POST 본문의 자리.
_SLOT = re.compile(r"\{(since|page|page_size|offset|cursor)\}")


def fill(template: Any, values: Mapping[str, Any]) -> Any:
    """POST 본문 틀 — 값 하나뿐인 자리(`"{page}"`)는 그 값 그대로(숫자 · null), 글 안의 자리는
    글로."""
    if isinstance(template, dict):
        return {key: fill(one, values) for key, one in template.items()}
    if isinstance(template, list):
        return [fill(one, values) for one in template]
    if isinstance(template, str):
        whole = _SLOT.fullmatch(template)
        if whole:
            return values.get(whole.group(1))
        return _SLOT.sub(lambda found: as_text(values.get(found.group(1))), template)
    return template


def same_place(url: str, base: str) -> bool:
    """다음 쪽 · 상세 주소가 원천과 같은 곳(scheme · host · 포트)인가 — 응답이 가리킨 다른
    곳으로 비밀을 실어 보내지 않는다."""
    one, two = urlsplit(url), urlsplit(base)
    return (one.scheme, one.netloc.lower()) == (two.scheme, two.netloc.lower())


def next_link(headers: httpx.Headers) -> str:
    """`Link: <…>; rel="next"` 의 주소 — 없으면 빈 글."""
    for part in headers.get("link", "").split(","):
        if re.search(r'rel\s*=\s*"?next"?', part):
            found = re.search(r"<([^>]+)>", part)
            if found:
                return found.group(1)
    return ""


def truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "y", "yes", "예", "참", "o"}


class RestAdapter:
    def __init__(
        self,
        source: RestSource,
        *,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.source = source
        self._client = client or httpx.Client(timeout=source.timeout)
        self._sleep = sleep
        self._clock = clock
        self._last = 0.0

    @property
    def url(self) -> str:
        return self.source.base_url.rstrip("/") + "/" + self.source.path.lstrip("/")

    def _ask(
        self,
        url: str,
        params: Mapping[str, Any] | None,
        *,
        body: Any = None,
        method: str = "GET",
    ) -> tuple[Any, httpx.Headers]:
        source = self.source
        if source.min_interval > 0:
            wait = self._last + source.min_interval - self._clock()
            if wait > 0:
                self._sleep(wait)
            self._last = self._clock()
        return request_json(
            self._client,
            url,
            params=params,
            auth=source.auth,
            retries=source.retries,
            sleep=self._sleep,
            method=method,
            body=body,
        )

    def _first(self, params: dict[str, Any], slots: Mapping[str, Any]) -> tuple[Any, Any]:
        """목록 주소에 한 번 — POST 면 본문 틀에 자리를 채운다."""
        source = self.source
        if source.method.upper() == "POST":
            return self._ask(
                self.url, params, body=fill(source.body or {}, slots), method="POST"
            )
        return self._ask(self.url, params)

    def _items(self, body: Any) -> list[Any]:
        rows_path = self.source.rows_path
        items = value_at(body, rows_path) if rows_path else body
        if not isinstance(items, list):
            top = sorted(body) if isinstance(body, dict) else []
            raise SourceError(f"행 자리({rows_path})에 목록이 없습니다 — 응답의 칸: {top}")
        return items

    def _with_detail(self, item: Any) -> Any:
        """목록 → 상세 — 항목의 칸을 상세 주소에 넣어 받아 `detail_as` 아래에 붙인다."""
        source = self.source
        if not source.detail_path or not isinstance(item, dict):
            return item

        def slot(found: re.Match[str]) -> str:
            value = as_text(value_at(item, found.group(1))).strip()
            if not value:
                raise SourceError(
                    f"상세 주소의 자리({found.group(1)})가 빈 항목입니다: {item!r:.200}"
                )
            return quote(value, safe="")

        path = re.sub(r"\{([^{}]+)\}", slot, source.detail_path)
        url = (
            urljoin(self.url.rstrip("/") + "/", path)
            if not path.startswith("/")
            else (source.base_url.rstrip("/") + path)
        )
        if not same_place(url, source.base_url):
            raise SourceError(f"상세 주소가 원천과 다른 곳입니다: {url}")
        got, _headers = self._ask(url, None)
        if source.detail_rows_path:
            got = value_at(got, source.detail_rows_path)
        return {**item, source.detail_as: got}

    def _records(self, item: Any) -> list[SourceRecord]:
        source = self.source
        return records_of(
            self._with_detail(item),
            key=source.key,
            columns=source.columns,
            deleted=source.deleted,
            updated_at=source.updated_at,
            explode=source.explode,
        )

    def _record(self, item: Any) -> SourceRecord:
        """펼치지 않는 원천의 행 하나 — 옛 부르는 쪽(시험)이 쓴다."""
        return self._records(item)[0]

    def _sized(self, size: int) -> tuple[dict[str, Any], dict[str, Any]]:
        """한 쪽에 `size` 줄 — (질의, 본문 자리)."""
        source = self.source
        params: dict[str, Any] = dict(source.params)
        slots: dict[str, Any] = {
            "since": None,
            "page": source.start_page,
            "page_size": size,
            "offset": 0,
            "cursor": None,
        }
        if source.method.upper() != "POST" and source.paging in (
            "page",
            "offset",
            "cursor",
            "next_url",
            "link",
        ):
            params[source.size_param] = size
        if source.method.upper() != "POST" and source.paging == "page":
            params[source.page_param] = source.start_page
        return params, slots

    def probe(self) -> str:
        """닿나 — 한 줄만 묻는다(`relay check`). 실패면 `SourceError`."""
        params, slots = self._sized(1)
        body, _headers = self._first(params, slots)
        items = self._items(body)
        first = ""
        if items:
            first = ", 식별자 " + KEY_JOIN.join(
                as_text(value_at(items[0], one)) for one in key_paths(self.source.key)
            )
        return f"응답 정상 — 첫 쪽 {len(items)}줄{first}"

    def discover(self) -> dict[str, Any]:
        """첫 쪽 몇 줄 → 칸의 자리 · 보기 값 · 열 이름 제안(MCP `source_discover`). 상세를
        받는 원천이면 앞 두 줄은 상세까지 붙여 보인다."""
        from relay_core.adapters.explore import suggest

        params, slots = self._sized(5)
        body, _headers = self._first(params, slots)
        items = self._items(body)
        if self.source.detail_path:
            items = [self._with_detail(one) for one in items[:2]] + items[2:]
        return suggest(items)

    def fetch(self, since: str | None, *, limit: int | None = None) -> Fetched:
        """`since` 가 있으면 그 뒤에 바뀐 것만, 없으면 전량. 마지막 쪽까지 못 가면 예외다.
        `limit` 이면 그만큼만 받고 멈춘다(시험용 — 덜 받은 것이라 `complete=False`)."""
        source = self.source
        check_shape(source.key, source.explode)
        post = source.method.upper() == "POST"
        if post and not source.body:
            raise SourceError("method POST 에는 body(본문 틀)가 있어야 합니다.")
        records: list[SourceRecord] = []
        marks: list[str] = []
        page, offset, cursor, follow = source.start_page, 0, "", ""
        watermark: str | None = None
        for _ in range(source.max_pages):
            if follow:
                # 응답이 준 다음 주소 그대로 — 질의는 그 안에 있다(인증만 다시 싣는다).
                body, headers = self._ask(follow, None)
            else:
                params: dict[str, Any] = dict(source.params)
                if since and source.since_param:
                    params[source.since_param] = since
                slots = {
                    "since": since,
                    "page": page,
                    "page_size": source.page_size,
                    "offset": offset,
                    "cursor": cursor or None,
                }
                if not post:
                    if source.paging == "page":
                        params.update(
                            {source.page_param: page, source.size_param: source.page_size}
                        )
                    elif source.paging == "offset":
                        params.update(
                            {source.offset_param: offset, source.size_param: source.page_size}
                        )
                    elif source.paging in ("cursor", "next_url", "link"):
                        params[source.size_param] = source.page_size
                        if cursor:
                            params[source.cursor_param] = cursor
                if source.paging not in PAGINGS:
                    raise ValueError(f"모르는 쪽 넘김입니다: {source.paging}")
                body, headers = self._first(params, slots)
            items = self._items(body)
            for item in items:
                for one in self._records(item):
                    records.append(one)
                    if one.updated_at:
                        marks.append(one.updated_at)
                if limit and len(records) >= limit:
                    return Fetched(records=records[:limit], complete=False)
            if source.watermark_path:
                given = value_at(body, source.watermark_path)
                watermark = str(given) if given else watermark
            if source.paging == "none":
                break
            if source.paging == "cursor":
                cursor = as_text(value_at(body, source.next_path))
                if not cursor:
                    break
            elif source.paging in ("next_url", "link"):
                given = (
                    as_text(value_at(body, source.next_path))
                    if source.paging == "next_url"
                    else next_link(headers)
                )
                if not given or not items:
                    break
                follow = urljoin(follow or self.url, given)
                if not same_place(follow, source.base_url):
                    raise SourceError(
                        f"다음 쪽 주소가 원천과 다른 곳입니다 — 따라가지 않습니다: {follow}"
                    )
            elif len(items) < source.page_size:
                break
            page += 1
            offset += len(items)
        else:
            raise SourceError(f"쪽이 {source.max_pages}개를 넘었습니다 — 끝을 알 수 없습니다.")
        if watermark is None:
            watermark = max(marks) if marks else since
        return Fetched(records=records, watermark=watermark, complete=True, full=not since)


#: 쪽 넘김의 방식.
PAGINGS = ("page", "offset", "cursor", "next_url", "link", "none")
