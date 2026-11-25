"""Kafka 원천 — 토픽의 메시지를 **구독해 읽는다**(쓰지 않는다). 원천 시스템이 변경을 토픽으로
내보낼 때(이벤트 · CDC 를 원천 쪽이 낸 것).

    {"kind": "kafka", "bootstrap": "kafka1.corp:9093,kafka2.corp:9093", "topic": "mes.lot",
     "group_id": "datarelay-mes-lot", "key": "LotId", "columns": {"LOT_ID": "LotId"},
     "auth": {"kind": "sasl", "mechanism": "SCRAM-SHA-512", "user": "svc_relay",
              "token_env": "MES_KAFKA_PASSWORD"}, "tls": true}

- **실행마다 끝이 있다** — 시작할 때 파티션마다 끝 위치(high watermark)를 재고, 소비
  그룹(`group_id`)이 확정한 위치부터 거기까지만 읽는다(그 사이 새로 온 것은 다음 실행).
  `max_messages` 를 넘으면 거기서 멈추고 나머지는 다음 실행이 읽는다.
- **확정(commit)은 미러에 반영한 뒤에만**(`Fetched.done`) — 받기 점검이 멈추면 확정하지 않아
  다음 실행이 같은 메시지를 다시 읽는다(보내 주는 원천의 받을 함과 같다). 자동 확정은 끈다.
- 증분이다 — 안 온 것은 그대로(`full` 아님). 지운 것은 삭제 표시(`deleted`) 또는 **비석
  (tombstone — 값이 빈 메시지)**: 식별자가 메시지 키(`_kafka.key`)일 때만 그 키를 지운 것으로
  본다.
- 메시지 값의 꼴(`format`) — `json`(객체 하나 · 목록 · `rows_path` 자리의 목록), `avro`(스키마
  레지스트리의 Confluent 꼴 — 앞 1바이트 0 · 스키마 번호 4바이트, `schema_registry`), `text`
  (`{"value": 글}`). 항목마다 `_kafka`(key · topic · partition · offset · timestamp)를 붙인다 —
  열 · 식별자 · 변경 시각으로 읽을 수 있다.
- 처음 읽는 그룹(확정한 위치가 없음)은 `start` — `earliest`(남은 것 전부) · `latest`
  (지금부터). 확정한 위치가 보존 기간(retention)이 지나 지워졌으면 남은 맨 앞부터 읽고
  **건너뛴 수를 알린다**.
- 인증 — `none`(평문) · `sasl`(`mechanism`: PLAIN · SCRAM-SHA-256 · SCRAM-SHA-512, `user` ·
  `token_env`) · `oauth2`(OAUTHBEARER — `token_url` · `user`(client_id) · `token_env`(secret)
  · `scope`). TLS 는 `tls: true` 또는 클라이언트 인증서(`auth.cert`, mTLS)가 있으면 켠다. 사내
  CA 는 `RELAY_CA_FILE` 이 건 묶음(`SSL_CERT_FILE`)을 쓴다. Kerberos(GSSAPI)는 이 빌드에 없다.
"""

from __future__ import annotations

import io
import json
import logging
import os
import struct
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx

from relay_core.adapters.base import Fetched
from relay_core.adapters.parquet import plain
from relay_core.adapters.rest import (
    Auth,
    SourceError,
    check_shape,
    records_of,
    scrub,
    value_at,
)
from relay_core.records import Records, SourceRecord

FORMATS = ("json", "avro", "text")
STARTS = ("earliest", "latest")
MECHANISMS = ("PLAIN", "SCRAM-SHA-256", "SCRAM-SHA-512")
#: 메시지 키를 읽는 자리 — 비석(tombstone)은 식별자가 이것일 때만 지운 것으로 본다.
META = "_kafka"
TOMBSTONE_KEY = f"{META}.key"


@dataclass(frozen=True)
class SchemaRegistry:
    url: str
    auth: Auth = field(default_factory=Auth)


@dataclass(frozen=True)
class KafkaSource:
    bootstrap: str
    """브로커 주소들(`host:port`, 쉼표로) — 모두 울타리 안이어야 한다."""
    topic: str
    key: str | Sequence[str]
    columns: Mapping[str, str]
    group_id: str = ""
    """소비 그룹 — 확정한 위치가 여기에 남는다. 파이프라인마다 따로(`datarelay-<slug>`).
    다른 시스템이 쓰는 그룹을 적으면 그쪽이 읽을 메시지를 이쪽이 가져간다."""
    format: str = "json"
    """json · avro(스키마 레지스트리) · text."""
    rows_path: str = ""
    """json: 메시지 값 안의 목록 자리 — 비우면 값이 항목 하나(객체) 또는 목록."""
    deleted: str | None = None
    updated_at: str | None = None
    explode: str = ""
    start: str = "earliest"
    """확정한 위치가 없을 때 — earliest(남은 것 전부) · latest(지금부터)."""
    max_messages: int = 200_000
    """한 실행에 읽는 메시지의 상한 — 나머지는 다음 실행."""
    tls: bool = False
    """TLS(SSL)로 붙는다 — 클라이언트 인증서(auth.cert)가 있으면 저절로 켠다."""
    schema_registry: SchemaRegistry | None = None
    """avro: `{"url": "http(s)://…", "auth": {"kind": "basic", "user", "token_env"}}`."""
    auth: Auth = field(default_factory=Auth)
    timeout: float = 30.0
    """브로커 · 레지스트리에 묻는 한 번의 상한(초). 메시지가 이만큼 안 오면 멈춘다."""
    retries: int = 3


def endpoints(bootstrap: str) -> list[str]:
    """`a:9092,b:9092` → `["kafka://a:9092", "kafka://b:9092"]` — 울타리 · 묶인 비밀이 본다."""
    out = []
    for one in str(bootstrap or "").split(","):
        one = one.strip()
        if not one:
            continue
        if "://" in one:
            one = one.split("://", 1)[1]
        out.append(f"kafka://{one}")
    return out


def config(source: KafkaSource, *, group: str | None = None) -> dict[str, Any]:
    """librdkafka 설정 — 비밀(`sasl.password` 따위)이 든다. 남에게 보이지 않는다."""
    auth = source.auth
    hosts = [one.removeprefix("kafka://") for one in endpoints(source.bootstrap)]
    if not hosts:
        raise SourceError("adapter.bootstrap에 브로커 주소(host:port)가 없습니다.")
    conf: dict[str, Any] = {
        "bootstrap.servers": ",".join(hosts),
        "group.id": group or source.group_id or "datarelay-explore",
        "client.id": "datarelay",
        "enable.auto.commit": False,
        "enable.auto.offset.store": False,
        "enable.partition.eof": False,
        "auto.offset.reset": source.start,
        "isolation.level": "read_committed",
        "socket.timeout.ms": int(source.timeout * 1000),
        "session.timeout.ms": 45_000,
        "log_level": 3,
    }
    tls = source.tls or bool(auth.cert)
    if auth.kind == "sasl":
        if auth.mechanism not in MECHANISMS:
            raise SourceError(
                f"auth.mechanism은 {' · '.join(MECHANISMS)} 중 하나여야 합니다: "
                f"{auth.mechanism!r}"
            )
        conf.update(
            {
                "sasl.mechanism": auth.mechanism,
                "sasl.username": auth.user,
                "sasl.password": auth.token,
            }
        )
    elif auth.kind == "oauth2":
        conf.update(
            {
                "sasl.mechanism": "OAUTHBEARER",
                "sasl.oauthbearer.method": "oidc",
                "sasl.oauthbearer.client.id": auth.user,
                "sasl.oauthbearer.client.secret": auth.token,
                "sasl.oauthbearer.token.endpoint.url": auth.token_url,
            }
        )
        if auth.scope:
            conf["sasl.oauthbearer.scope"] = auth.scope
    elif auth.kind != "none":
        raise SourceError(
            "Kafka의 auth.kind는 none · sasl · oauth2 중 하나입니다(클라이언트 인증서는 "
            f"auth.cert와 함께 지정): {auth.kind!r}"
        )
    sasl = auth.kind in ("sasl", "oauth2")
    conf["security.protocol"] = (
        ("SASL_SSL" if tls else "SASL_PLAINTEXT") if sasl else ("SSL" if tls else "PLAINTEXT")
    )
    if tls:
        # 사내 CA — `RELAY_CA_FILE` 이 공개 CA 와 합쳐 건 묶음(앱 · 워커가 뜰 때 건다).
        bundle = os.environ.get("SSL_CERT_FILE")
        if bundle:
            conf["ssl.ca.location"] = bundle
        if auth.cert:
            # PEM 하나에 인증서와 키 — OpenSSL 은 맞는 묶음만 골라 읽는다.
            conf["ssl.certificate.location"] = auth.cert
            conf["ssl.key.location"] = auth.cert
            if auth.cert_password:
                conf["ssl.key.password"] = auth.cert_password
    return conf


class _Registry:
    """스키마 레지스트리 — 번호 → Avro 스키마(한 번 읽으면 기억한다)."""

    def __init__(self, registry: SchemaRegistry | None, timeout: float, client: Any) -> None:
        self.registry = registry
        self.timeout = timeout
        self._client = client
        self.cache: dict[int, Any] = {}

    @property
    def client(self) -> Any:
        if self._client is None:
            self._client = httpx.Client(timeout=self.timeout)
        return self._client

    def schema(self, number: int) -> Any:
        if number in self.cache:
            return self.cache[number]
        if self.registry is None:
            raise SourceError(
                "Avro 메시지이지만 adapter.schema_registry(주소)가 없습니다. 스키마 "
                "레지스트리 주소를 입력하세요."
            )
        from fastavro import parse_schema

        url = f"{self.registry.url.rstrip('/')}/schemas/ids/{number}"
        auth = self.registry.auth
        try:
            got = self.client.get(url, headers=auth.headers(), timeout=self.timeout)
        except httpx.HTTPError as failure:
            raise SourceError(f"스키마 레지스트리에 연결할 수 없습니다: {failure}") from None
        if got.status_code != 200:
            raise SourceError(
                f"스키마 레지스트리에서 스키마 {number}의 조회에 "
                f"실패했습니다({got.status_code})."
            )
        try:
            parsed = parse_schema(json.loads(got.json()["schema"]))
        except (ValueError, KeyError, TypeError) as failure:
            raise SourceError(
                f"스키마 {number}의 형식을 읽을 수 없습니다: {failure}"
            ) from None
        self.cache[number] = parsed
        return parsed

    def decode(self, raw: bytes) -> Any:
        if len(raw) < 5 or raw[0] != 0:
            raise SourceError(
                "Avro 메시지가 스키마 레지스트리 형식(앞 1바이트 0, 스키마 번호 4바이트)이 "
                "아닙니다."
            )
        from fastavro import schemaless_reader

        (number,) = struct.unpack(">I", raw[1:5])
        try:
            return schemaless_reader(io.BytesIO(raw[5:]), self.schema(number), None)
        except SourceError:
            raise
        except Exception as failure:
            raise SourceError(f"Avro 메시지를 읽지 못했습니다: {failure}") from None


@dataclass
class _Read:
    """한 번 읽은 것 — 파티션마다 다음에 읽을 위치 · 건너뛴 것."""

    records: Records
    next: dict[int, int] = field(default_factory=dict)
    messages: int = 0
    tombstones: int = 0
    capped: bool = False
    stalled: bool = False


ConsumerFactory = Callable[[dict[str, Any]], Any]

#: 잠깐 기다리면 되는 오류 — 소비 그룹의 담당 브로커(coordinator)가 정해지는 · 올라오는 중.
#: 처음 쓰는 그룹 · 브로커가 막 뜬 때 흔하다(2026-11-18 진짜 브로커에서 NOT_COORDINATOR).
_COORDINATOR = {14, 15, 16}  # LOAD_IN_PROGRESS · NOT_AVAILABLE · NOT_COORDINATOR
#: 인증이 틀렸다 — librdkafka 는 이것을 오류 알림(error_cb)으로만 주고, 묻던 것은 「닿지
#: 못함」 으로 끝난다(2026-11-18 진짜 브로커). 알림을 모아 까닭을 바로 말한다.
_AUTH = {-169, 58}  # _AUTHENTICATION · SASL_AUTHENTICATION_FAILED

log = logging.getLogger("relay_core.kafka")


def _transient(failure: Exception) -> bool:
    error = failure.args[0] if failure.args else None
    code = getattr(error, "code", lambda: None)()
    retriable = getattr(error, "retriable", lambda: False)()
    return bool(retriable) or code in _COORDINATOR


def _default_consumer(conf: dict[str, Any]) -> Any:
    from confluent_kafka import Consumer

    return Consumer(conf)


class KafkaAdapter:
    def __init__(
        self,
        source: KafkaSource,
        *,
        consumer: ConsumerFactory | None = None,
        client: Any = None,
    ) -> None:
        self.source = source
        self._consumer = consumer or _default_consumer
        self._registry = _Registry(source.schema_registry, source.timeout, client)
        self._errors: list[Any] = []

    def __reduce__(self) -> Any:
        """작업 프로세스로 넘길 때 — 설정만. 연결은 거기서 새로 연다(`relay_core.isolate`)."""
        return KafkaAdapter, (self.source,)

    def _open(self, group: str | None = None) -> Any:
        """소비자 하나 — librdkafka 의 오류 알림을 모으고 로그를 앱의 로그로 보낸다."""
        conf = config(self.source, group=group)
        self._errors = []
        conf["error_cb"] = self._errors.append
        conf["logger"] = log
        return self._consumer(conf)

    def _why(self, what: str, failure: Exception, consumer: Any = None) -> SourceError:
        """오류 글 — 인증 알림이 있었으면 그것을 먼저, 비밀은 지운다. 알림은 `poll` 이 넘겨
        준다(묻다 실패한 그때는 아직 쌓여만 있다) — 소비자를 주면 한 번 받아 온다."""
        if consumer is not None:
            try:
                consumer.poll(0.5)
            except Exception:  # 받아 오기는 덤이다 — 실패해도 원래 까닭을 말한다
                log.debug("Kafka 오류 알림을 받아 오지 못했습니다", exc_info=True)
        secrets = [self.source.auth.token, self.source.auth.cert_password]
        denied = [one for one in self._errors if getattr(one, "code", lambda: None)() in _AUTH]
        if denied:
            said = getattr(denied[-1], "str", lambda: str(denied[-1]))()
            text = (
                "Kafka 인증에 실패했습니다. 계정(auth.user), 비밀번호(인증 정보), "
                f"방식(auth.mechanism)을 확인하세요: {said}"
            )
        else:
            text = f"{what}: {failure}"
        return SourceError(scrub(text, {}, secrets))

    # --- 메시지 → 항목 ---------------------------------------------------------------------

    def _meta(self, message: Any) -> dict[str, Any]:
        raw_key = message.key()
        key: Any = None
        if raw_key is not None:
            if self.source.format == "avro" and raw_key[:1] == b"\x00" and len(raw_key) > 5:
                key = plain(self._registry.decode(raw_key))
            else:
                key = raw_key.decode("utf-8", errors="replace")
        _kind, stamp = message.timestamp()
        return {
            "key": key,
            "topic": message.topic(),
            "partition": message.partition(),
            "offset": message.offset(),
            "timestamp": stamp if stamp and stamp > 0 else None,
        }

    def _items(self, message: Any) -> list[Any] | None:
        """메시지 → 항목들. 값이 빈 것(비석)은 None."""
        raw = message.value()
        if raw is None:
            return None
        meta = self._meta(message)
        source = self.source
        if source.format == "text":
            return [{"value": raw.decode("utf-8", errors="replace"), META: meta}]
        if source.format == "avro":
            body = plain(self._registry.decode(raw))
        else:
            try:
                body = json.loads(raw)
            except ValueError as failure:
                raise SourceError(
                    f"JSON 형식이 아닌 메시지입니다(파티션 {meta['partition']}, 오프셋 "
                    f"{meta['offset']}): {failure}. format을 확인하세요."
                ) from None
        items = value_at(body, source.rows_path) if source.rows_path else body
        if isinstance(items, dict):
            items = [items]
        if not isinstance(items, list):
            raise SourceError(
                f"메시지 값의 {source.rows_path or '최상위'} 항목이 객체나 목록이 "
                f"아닙니다(파티션 {meta['partition']}, 오프셋 {meta['offset']})."
            )
        return [{**one, META: meta} if isinstance(one, dict) else one for one in items]

    def _records(self, message: Any) -> tuple[list[SourceRecord], bool]:
        """(행들, 비석이었나)."""
        items = self._items(message)
        source = self.source
        if items is None:
            key = message.key()
            if source.key == TOMBSTONE_KEY and key is not None:
                text = self._meta(message)["key"]
                return [SourceRecord(key=str(text), values={}, deleted=True)], True
            return [], True
        out: list[SourceRecord] = []
        for item in items:
            out += records_of(
                item,
                key=source.key,
                columns=source.columns,
                deleted=source.deleted,
                updated_at=source.updated_at,
                explode=source.explode,
            )
        return out, False

    # --- 읽기 --------------------------------------------------------------------------------

    def _partitions(self, consumer: Any) -> list[int]:
        try:
            meta = consumer.list_topics(self.source.topic, timeout=self.source.timeout)
        except Exception as failure:
            raise self._why("Kafka에 연결할 수 없습니다", failure, consumer) from None
        topic = meta.topics.get(self.source.topic)
        if topic is None or topic.error is not None or not topic.partitions:
            why = f" ({topic.error})" if topic is not None and topic.error is not None else ""
            raise SourceError(f"토픽이 없습니다: {self.source.topic}{why}")
        return sorted(topic.partitions)

    def _call(self, what: str, run: Callable[[], Any]) -> Any:
        """브로커에 한 번 묻기 — 잠깐 기다리면 되는 오류는 `timeout` 안에서 다시, 아니면
        `SourceError`(다른 원천과 같은 오류로 실행 기록에 남게)."""
        from confluent_kafka import KafkaException

        deadline = time.monotonic() + self.source.timeout
        wait = 0.2
        while True:
            try:
                return run()
            except KafkaException as failure:
                if not _transient(failure) or time.monotonic() + wait > deadline:
                    raise self._why(what, failure) from None
            time.sleep(wait)
            wait = min(wait * 2, 2.0)

    def _marks(self, consumer: Any, parts: list[int]) -> dict[int, tuple[int, int]]:
        from confluent_kafka import TopicPartition

        def marks(part: int) -> Callable[[], Any]:
            return lambda: consumer.get_watermark_offsets(
                TopicPartition(self.source.topic, part), timeout=self.source.timeout
            )

        out = {}
        for part in parts:
            low, high = self._call(
                f"파티션 {part}의 마지막 오프셋을 조회하지 못했습니다", marks(part)
            )
            out[part] = (int(low), int(high))
        return out

    def _committed(self, consumer: Any, parts: list[int]) -> dict[int, int]:
        from confluent_kafka import TopicPartition

        got = self._call(
            "컨슈머 그룹의 커밋된 오프셋을 조회하지 못했습니다",
            lambda: consumer.committed(
                [TopicPartition(self.source.topic, part) for part in parts],
                timeout=self.source.timeout,
            ),
        )
        return {one.partition: int(one.offset) for one in got}

    def _read(
        self, consumer: Any, starts: dict[int, int], ends: dict[int, int], most: int
    ) -> _Read:
        from confluent_kafka import KafkaError, TopicPartition

        read = _Read(records=Records(), next=dict(starts))
        todo = {part for part in starts if starts[part] < ends[part]}
        if not todo:
            return read
        consumer.assign(
            [TopicPartition(self.source.topic, part, starts[part]) for part in sorted(todo)]
        )
        quiet_since = time.monotonic()
        while todo:
            batch = consumer.consume(num_messages=500, timeout=1.0)
            if not batch:
                if time.monotonic() - quiet_since > self.source.timeout:
                    read.stalled = True
                    break
                continue
            quiet_since = time.monotonic()
            for message in batch:
                error = message.error()
                if error is not None:
                    if error.code() == KafkaError._PARTITION_EOF:
                        continue
                    raise SourceError(f"Kafka가 메시지 대신 오류를 반환했습니다: {error}")
                part = message.partition()
                if part not in todo or message.offset() >= ends[part]:
                    continue  # 시작할 때의 끝 뒤 — 다음 실행
                rows, tomb = self._records(message)
                read.records.extend(rows)
                read.tombstones += int(tomb)
                read.messages += 1
                read.next[part] = message.offset() + 1
                if read.next[part] >= ends[part]:
                    todo.discard(part)
                if read.messages >= most:
                    read.capped = True
                    return read
        return read

    def _plan(self, consumer: Any) -> tuple[dict[int, int], dict[int, int], dict[int, int]]:
        """(시작, 끝, 확정한 위치 — 없으면 -1) — 파티션마다. 확정한 위치가 보존 기간으로
        지워졌으면 남은 맨 앞부터."""
        parts = self._partitions(consumer)
        marks = self._marks(consumer, parts)
        committed = self._committed(consumer, parts)
        starts, ends, done = {}, {}, {}
        for part in parts:
            low, high = marks[part]
            done[part] = committed.get(part, -1)
            if done[part] < 0:
                start = low if self.source.start == "earliest" else high
            else:
                start = min(max(done[part], low), high)
            starts[part], ends[part] = start, high
        return starts, ends, done

    def fetch(self, since: str | None, *, limit: int | None = None) -> Fetched:
        """시작할 때의 끝까지(또는 `max_messages` · `limit` 까지) — `since` 는 보지 않는다
        (확정한 위치는 소비 그룹이 든다). `limit` 이면 시험이라 확정하지 않는다."""
        check_shape(self.source.key, self.source.explode)
        if not self.source.group_id:
            raise SourceError(
                "adapter.group_id(컨슈머 그룹)가 필요합니다. 예: datarelay-<slug>"
            )
        consumer = self._open()
        try:
            starts, ends, committed = self._plan(consumer)
            most = min(limit, self.source.max_messages) if limit else self.source.max_messages
            read = self._read(consumer, starts, ends, most)
        finally:
            consumer.close()
        mark = ",".join(f"{part}:{read.next[part]}" for part in sorted(read.next))
        notes = self._notes(read, starts, ends, committed)
        if limit:
            return Fetched(records=read.records, watermark=mark, complete=False, notes=notes)
        # 확정할 것 — 읽어서 나아간 파티션, 그리고 **처음 읽는 파티션**(확정한 위치가 없음:
        # `latest` 면 지금의 끝을 확정해야 다음 실행이 그 사이 온 것을 읽는다), 보존 기간으로
        # 건너뛴 파티션.
        moved = {
            part: off for part, off in read.next.items() if off != committed.get(part, -1)
        }

        def done() -> None:
            self.commit(moved)

        return Fetched(
            records=read.records,
            watermark=mark,
            complete=True,
            full=False,
            notes=notes,
            done=done if moved else None,
        )

    def _notes(
        self,
        read: _Read,
        starts: Mapping[int, int],
        ends: Mapping[int, int],
        committed: Mapping[int, int],
    ) -> list[str]:
        notes = [f"Kafka 메시지 {read.messages:,}건."]
        lost = {
            part: starts[part] - done
            for part, done in committed.items()
            if 0 <= done < starts[part]
        }
        if lost:
            where = ", ".join(
                f"파티션 {part} {count:,}건" for part, count in sorted(lost.items())
            )
            notes.append(
                f"보존 기간이 지나 원천에서 삭제된 메시지를 건너뛰었습니다({where}). 해당 "
                "기간의 변경이 누락되었을 수 있습니다. 원천 담당자에게 재전송 등 전체 재수신 "
                "방법을 확인하세요."
            )
        left = sum(max(ends[part] - read.next.get(part, starts[part]), 0) for part in ends)
        if read.capped and left:
            notes.append(
                f"실행당 상한(max_messages)에 도달했습니다. 남은 {left:,}건은 다음 실행에서 "
                "수신합니다."
            )
        if read.stalled and left:
            notes.append(
                f"{self.source.timeout:g}초 동안 메시지가 수신되지 않아 수신을 중단했습니다. "
                f"남은 {left:,}건은 다음 실행에서 수신합니다."
            )
        if read.tombstones and self.source.key != TOMBSTONE_KEY:
            notes.append(
                f"삭제 메시지(값이 빈 메시지) {read.tombstones:,}건을 건너뛰었습니다. "
                f"식별자가 메시지 키({TOMBSTONE_KEY})일 때만 삭제로 처리합니다."
            )
        return notes

    def commit(self, offsets: Mapping[int, int]) -> None:
        """미러에 넣은 뒤 — 다음에 읽을 위치를 소비 그룹에 확정한다."""
        if not offsets:
            return
        from confluent_kafka import TopicPartition

        consumer = self._open()
        try:
            self._call(
                "오프셋을 커밋하지 못했습니다",
                lambda: consumer.commit(
                    offsets=[
                        TopicPartition(self.source.topic, part, off)
                        for part, off in sorted(offsets.items())
                    ],
                    asynchronous=False,
                ),
            )
        finally:
            consumer.close()

    # --- 닿나 · 탐색 -----------------------------------------------------------------------

    def lag(self) -> dict[str, Any]:
        """파티션마다 맨 앞 · 끝 · 확정한 위치 · 남은 수."""
        consumer = self._open()
        try:
            parts = self._partitions(consumer)
            marks = self._marks(consumer, parts)
            committed = (
                self._committed(consumer, parts)
                if self.source.group_id
                else dict.fromkeys(parts, -1)
            )
        finally:
            consumer.close()
        rows = []
        for part in parts:
            low, high = marks[part]
            done = committed.get(part, -1)
            base = done if done >= 0 else (low if self.source.start == "earliest" else high)
            rows.append(
                {
                    "partition": part,
                    "low": low,
                    "high": high,
                    "committed": done if done >= 0 else None,
                    "waiting": max(high - max(base, low), 0),
                }
            )
        return {
            "topic": self.source.topic,
            "group_id": self.source.group_id,
            "partitions": rows,
            "waiting": sum(one["waiting"] for one in rows),
        }

    def probe(self) -> str:
        found = self.lag()
        return (
            f"토픽 {found['topic']} · 파티션 {len(found['partitions'])}개 · 그룹 "
            f"{found['group_id'] or '(없음)'} · 미수신 {found['waiting']:,}건"
        )

    def topics(self) -> list[dict[str, Any]]:
        consumer = self._open()
        try:
            meta = consumer.list_topics(timeout=self.source.timeout)
        except Exception as failure:
            raise self._why("Kafka에 연결할 수 없습니다", failure, consumer) from None
        finally:
            consumer.close()
        return sorted(
            (
                {"topic": name, "partitions": len(one.partitions)}
                for name, one in meta.topics.items()
                if not name.startswith("__")
            ),
            key=lambda one: str(one["topic"]),
        )

    def discover(self, sample: int = 20) -> dict[str, Any]:
        """파티션마다 **맨 뒤의 몇 건**을 읽어 본다 — 확정하지 않는다(그룹의 위치는 그대로).
        토픽을 안 적었으면 토픽 목록부터."""
        from relay_core.adapters.explore import suggest

        if not self.source.topic:
            return {"topics": self.topics(), "note": "topic을 선택한 후 다시 탐색하세요."}
        found = self.lag()
        consumer = self._open(group="datarelay-explore")
        items: list[Any] = []
        try:
            from confluent_kafka import TopicPartition

            each = max(1, sample // max(1, len(found["partitions"])))
            starts = {
                one["partition"]: max(one["low"], one["high"] - each)
                for one in found["partitions"]
            }
            ends = {one["partition"]: one["high"] for one in found["partitions"]}
            todo = {part for part in starts if starts[part] < ends[part]}
            if todo:
                consumer.assign(
                    [TopicPartition(self.source.topic, p, starts[p]) for p in sorted(todo)]
                )
            quiet = time.monotonic()
            while todo and len(items) < sample:
                batch = consumer.consume(num_messages=50, timeout=1.0)
                if not batch:
                    if time.monotonic() - quiet > self.source.timeout:
                        break
                    continue
                quiet = time.monotonic()
                for message in batch:
                    if message.error() is not None:
                        continue
                    part = message.partition()
                    if message.offset() >= ends.get(part, 0):
                        continue
                    got = self._items(message)
                    if got:
                        items += got
                    if message.offset() + 1 >= ends[part]:
                        todo.discard(part)
        finally:
            consumer.close()
        return {
            **found,
            "messages_read": len(items),
            **stream_hints(suggest(items[:sample]), items),
        }


def stream_hints(suggested: dict[str, Any], items: list[Any]) -> dict[str, Any]:
    """이벤트 흐름에 맞춘 탐색의 단서 — 같은 키가 고칠 때마다 다시 오므로 「값이 모두 다른 칸」
    은 진짜 식별자를 떨어뜨린다(2026-11-18 진짜 브로커: model_id 대신 model_name 을 골랐다).

    - **메시지 키와 값이 같은 칸**을 식별자 후보의 맨 앞에. 그런 칸이 없고 키가 있으면
      `_kafka.key` 를.
    - 꼬리표(`_kafka.*` — 위치 · 시각 · 파티션)는 열 · 단서에서 뺀다 — 같은 내용이 다시 와도
      바뀌어 미러가 「변경」 으로 센다. 필요하면 정의에 손으로 적는다.
    """
    keyed = [
        one
        for one in items
        if isinstance(one, dict)
        and isinstance(one.get(META), dict)
        and one[META].get("key") not in (None, "")
    ]
    same = []
    if keyed:
        for entry in suggested.get("paths") or []:
            path = str(entry.get("path") or "")
            if not path or path.startswith(f"{META}."):
                continue
            if all(str(value_at(one, path)) == str(one[META]["key"]) for one in keyed):
                same.append(path)
    hints = {
        name: [p for p in values if not str(p).startswith(f"{META}.")]
        for name, values in (suggested.get("hints") or {}).items()
    }
    rest = [p for p in hints.get("key", []) if p not in same]
    hints["key"] = same + rest if same or not keyed else [TOMBSTONE_KEY, *rest]
    columns = {
        col: path
        for col, path in (suggested.get("columns") or {}).items()
        if not str(path).startswith(f"{META}.")
        or (path == TOMBSTONE_KEY and keyed and not same)
    }
    return {**suggested, "hints": hints, "columns": columns}


def check_kafka(body: Mapping[str, Any]) -> list[str]:
    """정의의 모양 — 잘못이면 그 까닭들(정의 검사가 `DefinitionError` 로 올린다)."""
    problems = []
    fmt = str(body.get("format") or "json")
    if fmt not in FORMATS:
        problems.append(f"adapter.format은 {' · '.join(FORMATS)} 중 하나여야 합니다: {fmt!r}")
    start = str(body.get("start") or "earliest")
    if start not in STARTS:
        problems.append(f"adapter.start는 {' · '.join(STARTS)} 중 하나여야 합니다: {start!r}")
    if fmt == "avro" and not body.get("schema_registry"):
        problems.append("format avro에는 adapter.schema_registry(주소)가 필요합니다.")
    most = body.get("max_messages", 200_000)
    if isinstance(most, bool) or not isinstance(most, int) or not 1 <= most <= 5_000_000:
        problems.append("adapter.max_messages는 1 이상 5,000,000 이하의 정수여야 합니다.")
    hosts = endpoints(str(body.get("bootstrap") or ""))
    if body.get("bootstrap") and not hosts:
        problems.append("adapter.bootstrap은 host:port를 쉼표로 구분해 입력합니다.")
    for one in hosts:
        host = one.removeprefix("kafka://")
        if ":" not in host:
            problems.append(f"adapter.bootstrap의 {host}에 포트가 없습니다.")
    group = str(body.get("group_id") or "")
    if group and not all(ch.isalnum() or ch in "._-" for ch in group):
        problems.append("adapter.group_id에는 영문, 숫자, . _ -만 사용할 수 있습니다.")
    return problems


def registry_of(spec: Any) -> Iterable[str]:
    """정의의 `schema_registry` 주소(있으면)."""
    if isinstance(spec, Mapping) and spec.get("url"):
        yield str(spec["url"])
