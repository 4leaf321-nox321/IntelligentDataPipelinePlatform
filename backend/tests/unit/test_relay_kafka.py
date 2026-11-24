"""Kafka 원천 — 가짜 브로커(파티션 · 메시지 · 소비 그룹의 확정 위치)로 본다.

- 실행마다 시작할 때의 끝까지만, **확정은 미러에 넣은 뒤에만**(`done`) — 안 부르면 다시 읽는다.
- 처음 읽는 그룹: earliest 는 남은 것 전부, latest 는 지금의 끝을 확정해 두고 다음부터.
- 보존 기간으로 지워진 위치 · 상한(max_messages) · 비석(tombstone)은 실행 기록에 말한다.
- Avro(스키마 레지스트리의 꼴) · 인증 설정 · 정의 검사 · 울타리(브로커마다 · 레지스트리).
"""

from __future__ import annotations

import io
import json
import struct
from dataclasses import dataclass, field, replace
from typing import Any

import httpx
import pytest
from confluent_kafka import TopicPartition
from fastavro import parse_schema, schemaless_writer

from relay_core.adapters import kafka
from relay_core.adapters.kafka import KafkaAdapter, KafkaSource, SchemaRegistry, config
from relay_core.adapters.rest import Auth, SourceError
from relay_core.definition import DefinitionError, build_adapter, check_adapter

TOPIC = "mes.lot"


@dataclass
class Msg:
    part: int
    off: int
    val: bytes | None
    k: bytes | None = None
    ts: int = 1_763_000_000_000

    def error(self) -> None:
        return None

    def value(self) -> bytes | None:
        return self.val

    def key(self) -> bytes | None:
        return self.k

    def topic(self) -> str:
        return TOPIC

    def partition(self) -> int:
        return self.part

    def offset(self) -> int:
        return self.off

    def timestamp(self) -> tuple[int, int]:
        return (1, self.ts)


@dataclass
class Broker:
    """파티션마다 메시지 목록(자리 = 위치) · 지워진 앞부분(low) · 그룹마다 확정한 위치."""

    parts: dict[int, list[Msg]] = field(default_factory=lambda: {0: [], 1: []})
    low: dict[int, int] = field(default_factory=lambda: {0: 0, 1: 0})
    committed: dict[tuple[str, int], int] = field(default_factory=dict)
    confs: list[dict[str, Any]] = field(default_factory=list)

    def send(
        self, part: int, value: Any, key: str | None = None, raw: bytes | None = None
    ) -> None:
        body = (
            raw if raw is not None else (None if value is None else json.dumps(value).encode())
        )
        msgs = self.parts[part]
        msgs.append(Msg(part, len(msgs), body, key.encode() if key else None))

    def consumer(self, conf: dict[str, Any]) -> FakeConsumer:
        self.confs.append(conf)
        return FakeConsumer(self, conf)


class FakeConsumer:
    def __init__(self, broker: Broker, conf: dict[str, Any]) -> None:
        self.broker, self.group = broker, conf["group.id"]
        self.pos: dict[int, int] = {}

    def list_topics(self, topic: str | None = None, timeout: float = 0) -> Any:
        meta = type("T", (), {"partitions": dict.fromkeys(self.broker.parts), "error": None})
        return type("M", (), {"topics": {TOPIC: meta}})

    def get_watermark_offsets(self, tp: TopicPartition, timeout: float = 0) -> tuple[int, int]:
        return self.broker.low[tp.partition], len(self.broker.parts[tp.partition])

    def committed(self, tps: list[TopicPartition], timeout: float = 0) -> list[TopicPartition]:
        return [
            TopicPartition(
                TOPIC,
                tp.partition,
                self.broker.committed.get((self.group, tp.partition), -1001),
            )
            for tp in tps
        ]

    def assign(self, tps: list[TopicPartition]) -> None:
        self.pos = {tp.partition: tp.offset for tp in tps}

    def consume(self, num_messages: int = 1, timeout: float = 0) -> list[Msg]:
        out: list[Msg] = []
        for part in sorted(self.pos):
            msgs = self.broker.parts[part]
            while self.pos[part] < len(msgs) and len(out) < num_messages:
                out.append(msgs[self.pos[part]])
                self.pos[part] += 1
        return out

    def commit(self, offsets: list[TopicPartition], asynchronous: bool = True) -> None:
        for tp in offsets:
            self.broker.committed[(self.group, tp.partition)] = tp.offset

    def close(self) -> None:
        pass


def _source(**over: Any) -> KafkaSource:
    base = KafkaSource(
        bootstrap="kafka1.corp:9093,kafka2.corp:9093",
        topic=TOPIC,
        key="LotId",
        columns={"LOT_ID": "LotId", "QTY": "Qty", "PART": "_kafka.partition"},
        group_id="relay-mes-lot",
        deleted="Gone",
        timeout=1.0,
    )
    return replace(base, **over)


def _adapter(broker: Broker, **over: Any) -> KafkaAdapter:
    return KafkaAdapter(_source(**over), consumer=broker.consumer)


def test_시작할_때의_끝까지_읽고_미러에_넣은_뒤에만_확정한다() -> None:
    broker = Broker()
    broker.send(0, {"LotId": "L1", "Qty": 1})
    broker.send(1, {"LotId": "L2", "Qty": 2})
    broker.send(0, {"LotId": "L1", "Qty": 5})  # 같은 키 — 늦은 것이 이긴다(받은 차례)
    broker.send(1, [{"LotId": "L3", "Qty": 3}, {"LotId": "L4", "Gone": True}])  # 목록 하나
    first = _adapter(broker).fetch(None)
    assert first.complete and not first.full and first.done is not None
    rows = {one.key: one for one in first.records.in_key_order()}  # type: ignore[union-attr]
    assert rows["L1"].values == {"LOT_ID": "L1", "QTY": "5", "PART": "0"}
    assert rows["L4"].deleted and first.watermark == "0:2,1:2"
    assert "Kafka 메시지 4건." in first.notes

    # 받기 점검에 걸렸다 — 확정하지 않았으니 다음 실행이 같은 것을 다시 읽는다.
    again = _adapter(broker).fetch(None)
    assert len(again.records) == len(first.records)
    again.done()  # type: ignore[misc]
    assert broker.committed == {("relay-mes-lot", 0): 2, ("relay-mes-lot", 1): 2}

    broker.send(0, {"LotId": "L9", "Qty": 9})
    later = _adapter(broker).fetch(None)
    assert [one.key for one in later.records] == ["L9"] and later.watermark == "0:3,1:2"
    later.done()  # type: ignore[misc]
    empty = _adapter(broker).fetch(None)
    assert len(empty.records) == 0 and empty.done is None  # 나아간 것이 없다 — 확정할 것도
    assert broker.confs[0]["enable.auto.commit"] is False


def test_처음_읽는_그룹_latest_는_지금의_끝을_확정해_두고_다음부터() -> None:
    broker = Broker()
    broker.send(0, {"LotId": "OLD", "Qty": 1})
    first = _adapter(broker, start="latest").fetch(None)
    assert len(first.records) == 0 and first.done is not None
    first.done()
    broker.send(0, {"LotId": "NEW", "Qty": 2})
    second = _adapter(broker, start="latest").fetch(None)
    assert [one.key for one in second.records] == ["NEW"]


def test_보존_기간으로_지워진_위치_상한_비석을_말한다() -> None:
    broker = Broker()
    for n in range(6):
        broker.send(0, {"LotId": f"L{n}", "Qty": n})
    broker.committed[("relay-mes-lot", 0)] = 1
    broker.low[0] = 3  # 위치 1 · 2 는 보존 기간이 지나 지워졌다
    capped = _adapter(broker, max_messages=2).fetch(None)
    assert [one.key for one in capped.records] == ["L3", "L4"]
    said = " ".join(capped.notes)
    assert "보존 기간이 지나" in said and "파티션 0 2건" in said
    assert "max_messages" in said and "1건은 다음 실행" in said
    capped.done()  # type: ignore[misc]
    assert broker.committed[("relay-mes-lot", 0)] == 5

    broker.send(1, None, key="L7")  # 비석 — 식별자가 메시지 키가 아니면 건너뛴다
    skipped = _adapter(broker).fetch(None)
    assert [one.key for one in skipped.records] == ["L5"]
    assert any("삭제 메시지" in one for one in skipped.notes)

    keyed = Broker()
    keyed.send(0, {"Qty": 1}, key="K1")
    keyed.send(0, None, key="K1")  # 같은 키의 비석 — 지웠다
    keyed.send(1, {"Qty": 2}, key="K2")
    by_key = _adapter(keyed, key="_kafka.key", columns={"K": "_kafka.key"}).fetch(None)
    last = {one.key: one for one in by_key.records.in_key_order()}  # type: ignore[union-attr]
    assert last["K1"].deleted and not last["K2"].deleted
    assert not any("삭제 메시지" in one for one in by_key.notes)


def test_시험은_앞_몇_건만_읽고_확정하지_않는다() -> None:
    broker = Broker()
    for n in range(5):
        broker.send(n % 2, {"LotId": f"L{n}", "Qty": n})
    cut = _adapter(broker).fetch(None, limit=3)
    assert len(cut.records) == 3 and not cut.complete and cut.done is None
    assert broker.committed == {}
    found = _adapter(broker).discover()
    assert found["waiting"] == 5 and found["messages_read"] >= 2
    assert "LotId" in {one["path"] for one in found["paths"]}
    assert "미수신 5건" in _adapter(broker).probe()


def test_Avro_는_스키마_레지스트리에서_스키마를_읽는다() -> None:
    schema = {
        "type": "record",
        "name": "Lot",
        "fields": [
            {"name": "LotId", "type": "string"},
            {"name": "Qty", "type": "long"},
            {
                "name": "Price",
                "type": {
                    "type": "bytes",
                    "logicalType": "decimal",
                    "precision": 8,
                    "scale": 2,
                },
            },
        ],
    }
    seen: list[httpx.Request] = []

    def registry(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/schemas/ids/7":
            return httpx.Response(200, json={"schema": json.dumps(schema)})
        return httpx.Response(404)

    from decimal import Decimal

    buf = io.BytesIO()
    schemaless_writer(
        buf, parse_schema(schema), {"LotId": "A1", "Qty": 4, "Price": Decimal("12.50")}
    )
    broker = Broker()
    broker.send(0, None, raw=b"\x00" + struct.pack(">I", 7) + buf.getvalue())
    broker.send(0, None, raw=b"\x00" + struct.pack(">I", 7) + buf.getvalue())
    source = _source(
        format="avro",
        columns={"LOT_ID": "LotId", "QTY": "Qty", "PRICE": "Price"},
        deleted=None,
        schema_registry=SchemaRegistry(
            url="https://registry.corp:8081",
            auth=Auth(kind="basic", user="reader", token="r3g-s3cret"),
        ),
    )
    client = httpx.Client(transport=httpx.MockTransport(registry))
    got = KafkaAdapter(source, consumer=broker.consumer, client=client).fetch(None)
    first, second = list(got.records)  # 같은 메시지 둘 — 접는 것은 미러가 한다
    assert first.values == second.values == {"LOT_ID": "A1", "QTY": "4", "PRICE": "12.50"}
    assert (
        len(seen) == 1
    )  # 스키마는 한 번 읽고 기억한다 and seen[0].headers["authorization"].startswith("Basic ")
    plain = Broker()
    plain.send(0, {"LotId": "X"})
    with pytest.raises(SourceError, match="스키마 레지스트리 형식"):
        KafkaAdapter(source, consumer=plain.consumer, client=client).fetch(None)


def test_인증_설정() -> None:
    scram = config(
        _source(
            tls=True,
            auth=Auth(kind="sasl", mechanism="SCRAM-SHA-512", user="svc", token="pw"),
        )
    )
    assert scram["security.protocol"] == "SASL_SSL"
    assert (scram["sasl.mechanism"], scram["sasl.username"], scram["sasl.password"]) == (
        "SCRAM-SHA-512",
        "svc",
        "pw",
    )
    assert scram["bootstrap.servers"] == "kafka1.corp:9093,kafka2.corp:9093"
    mtls = config(_source(auth=Auth(kind="none", cert="/certs/c.pem", cert_password="k")))
    assert mtls["security.protocol"] == "SSL" and mtls["ssl.key.location"] == "/certs/c.pem"
    oauth = config(
        _source(auth=Auth(kind="oauth2", user="cid", token="sec", token_url="https://idp/t"))
    )
    assert oauth["sasl.mechanism"] == "OAUTHBEARER" and oauth["security.protocol"] == (
        "SASL_PLAINTEXT"
    )
    assert config(_source())["security.protocol"] == "PLAINTEXT"
    with pytest.raises(SourceError, match="none · sasl · oauth2"):
        config(_source(auth=Auth(kind="bearer", token="t")))


def test_정의_검사() -> None:
    good = {
        "kind": "kafka",
        "bootstrap": "kafka1.corp:9093",
        "topic": TOPIC,
        "group_id": "relay-mes-lot",
        "key": "LotId",
        "columns": {"LOT_ID": "LotId"},
        "auth": {
            "kind": "sasl",
            "mechanism": "SCRAM-SHA-256",
            "user": "svc",
            "token_env": "MES_KAFKA_PASSWORD",
        },
    }
    assert check_adapter(good)[0] == "kafka"
    built = build_adapter(good, {"MES_KAFKA_PASSWORD": "pw"})
    assert isinstance(built, KafkaAdapter) and built.source.auth.mechanism == "SCRAM-SHA-256"
    for bad, said in (
        ({**good, "group_id": ""}, "group_id"),
        ({**good, "bootstrap": "kafka1.corp"}, "포트가 없습니다"),
        ({**good, "format": "xml"}, "format"),
        ({**good, "format": "avro"}, "schema_registry"),
        ({**good, "auth": {"kind": "bearer", "token_env": "X"}}, "none · sasl · oauth2"),
        ({**good, "auth": {"kind": "sasl", "user": "svc", "token_env": "X"}}, "mechanism"),
        ({**good, "schema_registry": {"url": "ftp://r"}}, "schema_registry"),
        ({**good, "password": "pw"}, "인증 정보"),
    ):
        with pytest.raises(DefinitionError, match=said):
            check_adapter(bad)
    rest = {
        "kind": "rest",
        "base_url": "https://x",
        "path": "/a",
        "key": "id",
        "columns": {"ID": "id"},
    }
    with pytest.raises(DefinitionError, match="Kafka 원천에만"):
        check_adapter(
            {
                **rest,
                "auth": {"kind": "sasl", "mechanism": "PLAIN", "user": "u", "token_env": "X"},
            }
        )
    with_registry = {
        **good,
        "format": "avro",
        "schema_registry": {
            "url": "https://registry.corp:8081",
            "auth": {"kind": "basic", "user": "reader", "token_env": "MES_REGISTRY_TOKEN"},
        },
    }
    made = build_adapter(
        with_registry, {"MES_KAFKA_PASSWORD": "pw", "MES_REGISTRY_TOKEN": "r"}
    )
    assert isinstance(made, KafkaAdapter)
    assert made.source.schema_registry and made.source.schema_registry.auth.token == "r"
    assert kafka.endpoints(" a:1, kafka://b:2 ,") == ["kafka://a:1", "kafka://b:2"]


def test_울타리는_브로커마다와_레지스트리를_본다() -> None:
    from app.modules.relay import guard
    from app.shared.errors import Forbidden

    spec = {
        "bootstrap": "kafka1.corp:9093,kafka2.corp:9093",
        "schema_registry": {"url": "https://registry.corp:8081"},
        "auth": {"kind": "sasl", "token_env": "MES_KAFKA_PASSWORD"},
    }
    inside = guard.Fence(hosts=frozenset({"kafka1.corp", "kafka2.corp", "registry.corp"}))
    guard.check_source("kafka", spec, inside)
    half = guard.Fence(hosts=frozenset({"kafka1.corp", "registry.corp"}))
    with pytest.raises(Forbidden, match=r"kafka2\.corp"):
        guard.check_source("kafka", spec, half)
    bound = guard.Fence(
        hosts=inside.hosts,
        bound={"MES_KAFKA_PASSWORD": frozenset({("원천", "plm.corp")})},
    )
    with pytest.raises(Forbidden, match="MES_KAFKA_PASSWORD"):
        guard.check_source("kafka", spec, bound)
    assert guard.source_url("kafka", spec) == "kafka://kafka1.corp:9093"


def test_탐색의_단서는_메시지_키와_같은_칸을_식별자로_꼬리표_열은_뺀다() -> None:
    from relay_core.adapters.kafka import stream_hints

    broker = Broker()
    for n, name in ((1, "A"), (2, "B"), (1, "A2")):  # 1 이 두 번 — 고친 것
        broker.send(n % 2, {"model_id": f"M{n}", "model_name": name}, key=f"M{n}")
    found = _adapter(broker, key="", columns={}).discover()
    assert found["hints"]["key"][0] == "model_id"
    assert not [p for p in found["columns"].values() if p.startswith("_kafka.")]
    bare = stream_hints(
        {
            "paths": [{"path": "qty"}],
            "hints": {"key": []},
            "columns": {"KAFKA_KEY": "_kafka.key"},
        },
        [{"qty": 1, "_kafka": {"key": "K1"}}],
    )
    assert bare["hints"]["key"] == ["_kafka.key"] and bare["columns"] == {
        "KAFKA_KEY": "_kafka.key"
    }


def test_인증이_틀리면_그렇게_말하고_비밀은_글에_없다() -> None:
    from confluent_kafka import KafkaError, KafkaException

    class Denied(FakeConsumer):
        def __init__(self, broker: Broker, conf: dict[str, Any]) -> None:
            super().__init__(broker, conf)
            self.conf = conf

        def list_topics(self, topic: str | None = None, timeout: float = 0) -> Any:
            raise KafkaException(KafkaError(KafkaError._TRANSPORT))

        def poll(self, timeout: float = 0) -> None:
            self.conf["error_cb"](
                KafkaError(
                    KafkaError._AUTHENTICATION, "SASL authentication error: bad pw-s3cret"
                )
            )

    broker = Broker()
    auth = Auth(kind="sasl", mechanism="PLAIN", user="svc", token="pw-s3cret")
    adapter = KafkaAdapter(_source(auth=auth), consumer=lambda conf: Denied(broker, conf))
    with pytest.raises(SourceError, match="Kafka 인증에 실패했습니다") as caught:
        adapter.fetch(None)
    assert "pw-s3cret" not in str(caught.value)
