"""진짜 Kafka 로 relay 의 Kafka 원천을 본다 — `devtools/kafka/up.sh` 가 띄운 것
(127.0.0.1:9192 평문 · 9193 SASL). 토픽은 돌릴 때마다 새로(이름 끝에 시각). 비밀은 출력하지
않는다.

    backend/.venv/bin/python devtools/kafka/check.py

읽고 확정 · 확정 전 다시 읽기 · SASL(SCRAM · PLAIN) · 틀린 비밀번호 · latest · 상한 · 비석 ·
Avro(스키마 레지스트리 흉내) · 보존 기간으로 지워진 위치 · 탐색 · 실행(미러에 넣은 뒤에만 확정,
받기 점검이 멈추면 안 함).
"""

from __future__ import annotations

import io
import json
import os
import shutil
import struct
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND))

from confluent_kafka import Producer, TopicPartition  # noqa: E402
from confluent_kafka.admin import AdminClient, NewTopic  # noqa: E402
from fastavro import parse_schema, schemaless_writer  # noqa: E402

from relay_core.adapters.kafka import KafkaAdapter, KafkaSource, SchemaRegistry  # noqa: E402
from relay_core.adapters.rest import Auth, SourceError  # noqa: E402

PLAIN_HOST, SASL_HOST = "127.0.0.1:9192", "127.0.0.1:9193"
SECRETS = json.loads((BACKEND.parent / ".work/kafka/test-secrets.json").read_text())
RUN = str(int(time.time()))
bad: list[str] = []


def check(cond: bool, what: str) -> None:
    print(("  ✔ " if cond else "  ✘ ") + what, flush=True)
    if not cond:
        bad.append(what)


admin = AdminClient({"bootstrap.servers": PLAIN_HOST})
producer = Producer({"bootstrap.servers": PLAIN_HOST, "linger.ms": 5})


def topic(name: str, parts: int) -> str:
    full = f"{name}.{RUN}"
    for fut in admin.create_topics(
        [NewTopic(full, num_partitions=parts, replication_factor=1)]
    ).values():
        fut.result(30)
    return full


def send(name: str, value: object, key: str | None = None, raw: bytes | None = None) -> None:
    body = raw if raw is not None else (None if value is None else json.dumps(value).encode())
    producer.produce(name, value=body, key=key.encode() if key else None)


def flush() -> None:
    producer.flush(30)


def lot(n: int, **over: object) -> dict[str, object]:
    return {"LotId": f"L{n:03d}", "Name": f"로트 {n}", "Kind": "A", "Base": "", **over}


def source(name: str, group: str, **over: object) -> KafkaSource:
    base: dict[str, object] = {
        "bootstrap": PLAIN_HOST,
        "topic": name,
        "group_id": f"relay-check-{group}-{RUN}",
        "key": "LotId",
        "columns": {"MODEL_ID": "LotId", "MODEL_NAME": "Name", "KIND": "Kind", "BASE": "Base"},
        "deleted": "Gone",
        "timeout": 10.0,
    }
    base.update(over)
    return KafkaSource(**base)  # type: ignore[arg-type]


def main() -> None:
    lots = topic("mes.lot", 3)
    for n in range(1, 31):
        send(lots, lot(n), key=f"L{n:03d}")
    send(
        lots, lot(5, Name="로트 5 고침"), key="L005"
    )  # 같은 키 → 같은 파티션, 늦은 것이 이긴다
    flush()

    print("A. 평문 — 읽고 확정 · 다시 읽기")
    adapter = KafkaAdapter(source(lots, "a"))
    t0 = time.monotonic()
    first = adapter.fetch(None)
    took = time.monotonic() - t0
    rows = {one.key: one for one in first.records.in_key_order()}
    check(
        len(first.records) == 31 and len(rows) == 30,
        f"메시지 31건 → 행 31 · 키 30 ({took:.1f}초)",
    )
    check(rows["L005"].values["MODEL_NAME"] == "로트 5 고침", "같은 키는 늦은 메시지가 이긴다")
    check(
        first.notes[0] == "Kafka 메시지 31건." and first.done is not None,
        f"기록: {first.notes}",
    )
    again = adapter.fetch(None)
    check(len(again.records) == 31, "확정 전이면 다음 실행이 같은 것을 다시 읽는다")
    again.done()
    lag = adapter.lag()
    check(
        lag["waiting"] == 0 and all(p["committed"] is not None for p in lag["partitions"]),
        "확정 뒤 남은 것 0 · 파티션 "
        + str([(p["partition"], p["committed"]) for p in lag["partitions"]]),
    )
    empty = adapter.fetch(None)
    check(
        len(empty.records) == 0 and empty.done is None,
        "새 메시지가 없으면 빈 받기 · 확정할 것 없음",
    )
    for n in range(31, 36):
        send(lots, lot(n), key=f"L{n:03d}")
    flush()
    more = adapter.fetch(None)
    check(
        sorted(one.key for one in more.records) == [f"L{n:03d}" for n in range(31, 36)],
        "그 뒤로 온 5건만",
    )
    more.done()

    print("B. SASL — SCRAM-SHA-512 · PLAIN · 틀린 비밀번호")
    for mech, user, pw in (
        ("SCRAM-SHA-512", "svc_scram", SECRETS["scram"]),
        ("PLAIN", "svc_plain", SECRETS["plain"]),
    ):
        got = KafkaAdapter(
            source(
                lots,
                f"sasl-{mech}",
                bootstrap=SASL_HOST,
                auth=Auth(kind="sasl", mechanism=mech, user=user, token=pw),
            )
        ).fetch(None)
        check(len(got.records) == 36, f"{mech} 로 붙어 36건")
    wrong = "not-the-password-" + RUN
    t0 = time.monotonic()
    try:
        KafkaAdapter(
            source(
                lots,
                "sasl-wrong",
                bootstrap=SASL_HOST,
                timeout=8.0,
                auth=Auth(
                    kind="sasl", mechanism="SCRAM-SHA-512", user="svc_scram", token=wrong
                ),
            )
        ).fetch(None)
        check(False, "틀린 비밀번호가 거절돼야 한다")
    except SourceError as failure:
        text = str(failure)
        check(
            wrong not in text and SECRETS["scram"] not in text,
            f"틀린 비밀번호 → 거절({time.monotonic() - t0:.1f}초), 글에 비밀 없음: "
            f"{text[:140]}",
        )

    print("C. 처음 읽는 그룹 latest · 상한")
    late = KafkaAdapter(source(lots, "latest", start="latest"))
    zero = late.fetch(None)
    check(
        len(zero.records) == 0 and zero.done is not None,
        "latest — 지금까지는 안 읽고 끝을 확정할 것이 있다",
    )
    zero.done()
    for n in range(36, 39):
        send(lots, lot(n), key=f"L{n:03d}")
    flush()
    check(len(late.fetch(None).records) == 3, "그 뒤로 온 3건만")
    capped = KafkaAdapter(source(lots, "cap", max_messages=7))
    part = capped.fetch(None)
    check(
        len(part.records) == 7 and any("max_messages" in one for one in part.notes),
        f"상한 7 — {[one for one in part.notes if 'max' in one]}",
    )
    part.done()
    rest = KafkaAdapter(source(lots, "cap")).fetch(None)  # 같은 그룹 · 상한 없이
    check(len(rest.records) == 39 - 7, f"나머지 {len(rest.records)}건은 다음 실행")

    print("D. 비석 — 메시지 키가 식별자")
    keyed = topic("mes.lot.keyed", 2)
    send(keyed, {"Qty": 1}, key="K1")
    send(keyed, {"Qty": 2}, key="K2")
    send(keyed, None, key="K1")
    flush()
    tomb = KafkaAdapter(
        source(
            keyed,
            "tomb",
            key="_kafka.key",
            columns={"K": "_kafka.key", "QTY": "Qty"},
            deleted=None,
        )
    ).fetch(None)
    last = {one.key: one for one in tomb.records.in_key_order()}
    check(last["K1"].deleted and not last["K2"].deleted, "K1 은 비석으로 지움 · K2 는 그대로")
    skip = KafkaAdapter(
        source(keyed, "tomb-skip", key="Qty", columns={"QTY": "Qty"}, deleted=None)
    )
    try:
        got = skip.fetch(None)
        check(
            any("비석" in one for one in got.notes),
            "식별자가 메시지 키가 아니면 비석은 건너뛰고 말한다",
        )
    except SourceError as failure:
        check(False, f"비석 건너뛰기: {failure}")

    print("E. Avro — 스키마 레지스트리(basic 인증)")
    schema = {
        "type": "record",
        "name": "Lot",
        "fields": [
            {"name": "LotId", "type": "string"},
            {"name": "Qty", "type": "long"},
            {"name": "MadeAt", "type": {"type": "long", "logicalType": "timestamp-millis"}},
        ],
    }
    seen: list[str] = []

    class Registry(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            seen.append(self.headers.get("Authorization") or "")
            if self.path == "/schemas/ids/3" and self.headers.get(
                "Authorization", ""
            ).startswith("Basic "):
                body = json.dumps({"schema": json.dumps(schema)}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(401 if self.path == "/schemas/ids/3" else 404)
                self.end_headers()

        def log_message(self, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Registry)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    avro = topic("mes.lot.avro", 2)
    parsed = parse_schema(schema)
    from datetime import UTC, datetime

    for n in range(4):
        buf = io.BytesIO()
        schemaless_writer(
            buf,
            parsed,
            {"LotId": f"A{n}", "Qty": n, "MadeAt": datetime(2026, 11, 18, 9, n, tzinfo=UTC)},
        )
        send(avro, None, key=f"A{n}", raw=b"\x00" + struct.pack(">I", 3) + buf.getvalue())
    flush()
    reg = SchemaRegistry(
        url=f"http://127.0.0.1:{server.server_port}",
        auth=Auth(kind="basic", user="reader", token="r"),
    )
    got = KafkaAdapter(
        source(
            avro,
            "avro",
            format="avro",
            deleted=None,
            schema_registry=reg,
            columns={"LOT_ID": "LotId", "QTY": "Qty", "MADE_AT": "MadeAt"},
        )
    ).fetch(None)
    vals = {one.key: one.values for one in got.records}
    check(
        len(vals) == 4
        and vals["A2"]["MADE_AT"] == "2026-11-18T09:02:00+00:00"
        and vals["A2"]["QTY"] == "2",
        f"Avro 4건 · 시각은 ISO: {vals.get('A2')}",
    )
    check(len(seen) == 1, f"스키마는 한 번만 묻는다({len(seen)}번)")
    server.shutdown()

    print("F. 보존 기간으로 지워진 위치")
    ret = topic("mes.lot.retention", 1)
    for n in range(10):
        send(ret, lot(n), key=f"L{n:03d}")
    flush()
    keep = KafkaAdapter(source(ret, "retention", max_messages=3))
    head = keep.fetch(None)
    head.done()  # 3 까지 확정
    for fut in admin.delete_records([TopicPartition(ret, 0, 6)]).values():
        fut.result(30)  # 0..5 를 지운다(보존 기간이 지난 것처럼)
    after = KafkaAdapter(source(ret, "retention")).fetch(None)
    said = " ".join(after.notes)
    check(
        sorted(one.key for one in after.records) == [f"L{n:03d}" for n in range(6, 10)]
        and "보존 기간이 지나" in said
        and "파티션 0 3건" in said,
        f"6~9 만 · {said[:160]}",
    )

    print("G. 탐색 · 닿나 · 토픽 목록")
    found = KafkaAdapter(source(lots, "a")).discover()
    check(
        found["waiting"] == 3
        and found["messages_read"] >= 3
        and "LotId" in found["columns"].values(),
        f"남은 {found['waiting']} · 읽어 본 {found['messages_read']} · 열 "
        f"{list(found['columns'])[:4]} · 식별자 후보 {found['hints']['key'][:2]}",
    )
    lag_after = KafkaAdapter(source(lots, "a")).lag()["waiting"]
    check(lag_after == 3, "탐색은 확정하지 않는다(남은 3 그대로)")
    print("   probe:", KafkaAdapter(source(lots, "a")).probe())
    names = {one["topic"] for one in KafkaAdapter(source("", "list")).topics()}
    check(
        lots in names and not any(n.startswith("__") for n in names),
        f"토픽 목록 {len(names)}개(내부 토픽 뺌)",
    )
    try:
        KafkaAdapter(source(f"none.{RUN}", "none")).fetch(None)
        check(False, "없는 토픽은 거절")
    except SourceError as failure:
        check("토픽이 없습니다" in str(failure), f"없는 토픽: {failure}")

    print("H. 실행 — 미러에 넣은 뒤에만 확정 · 받기 점검에 걸리면 확정하지 않음")
    run_topic = topic("mes.lot.run", 3)
    for n in range(1, 21):
        send(run_topic, lot(n), key=f"L{n:03d}")
    flush()
    from relay_core import runner
    from relay_core.definition import load_body
    from relay_core.state import Workdir
    from tests.unit.test_relay_core import RECIPE
    from tests.unit.test_relay_rest import FakeSubmitter

    os.environ["RELAY_T_TOKEN"] = "x"
    base = Path(tempfile.mkdtemp(prefix="relay-kafka-"))
    try:
        (base / "recipe.table.json").write_text(json.dumps(RECIPE), encoding="utf-8")
        body = {
            "format": "relay-pipeline/1",
            "slug": "kafka-check",
            "source": "mes",
            "system": "MES",
            "adapter": {
                "kind": "kafka",
                "bootstrap": SASL_HOST,
                "topic": run_topic,
                "group_id": f"relay-check-run-{RUN}",
                "key": "LotId",
                "columns": {
                    "MODEL_ID": "LotId",
                    "MODEL_NAME": "Name",
                    "KIND": "Kind",
                    "BASE": "Base",
                },
                "deleted": "Gone",
                "timeout": 10,
                "auth": {
                    "kind": "sasl",
                    "mechanism": "SCRAM-SHA-512",
                    "user": "svc_scram",
                    "token_env": "CHECK_KAFKA_PASSWORD",
                },
            },
            "recipe": "recipe.table.json",
            "target": {
                "name": "시험",
                "server": "http://127.0.0.1:9",
                "token_env": "RELAY_T_TOKEN",
            },
        }
        os.environ["CHECK_KAFKA_PASSWORD"] = SECRETS["scram"]
        pipeline = load_body(body, base=base)
        work = Workdir(base / "work", "kafka-check")
        fake = FakeSubmitter()
        one = runner.run(pipeline, work, fake)
        check(
            one.status == "planned" and "Kafka 메시지 20건." in one.message,
            f"첫 실행: {one.status} · {one.message[:120]}",
        )
        lag1 = KafkaAdapter(source(run_topic, "run"))
        check(lag1.lag()["waiting"] == 0, "미러에 넣은 뒤 확정했다(남은 0)")
        runner.apply(pipeline, work, fake)
        for n in range(1, 16):
            send(run_topic, lot(n, Gone=True), key=f"L{n:03d}")
        flush()
        held = runner.run(pipeline, work, fake)
        check(
            held.status == "blocked" and "대량 삭제" in held.message,
            f"대량 삭제로 멈춤: {held.status}",
        )
        check(lag1.lag()["waiting"] == 15, "멈추면 확정하지 않는다(남은 15)")
        import dataclasses

        off = dataclasses.replace(pipeline, intake=None)
        third = runner.run(off, work, fake)
        check(
            third.status == "planned"
            and lag1.lag()["waiting"] == 0
            and work.mirror_size() == 5,
            f"점검을 끄고 다시 — 15건을 읽어 확정 · 미러 {work.mirror_size()}",
        )
    finally:
        shutil.rmtree(base, ignore_errors=True)
        os.environ.pop("CHECK_KAFKA_PASSWORD", None)

    print("\n[요약]", "모두 통과" if not bad else f"실패 {len(bad)}: {bad}")


if __name__ == "__main__":
    main()
