"""흘려 쓰는 묶음 — 지문 · 크기가 틀의 것(`digest(payload(load(run)))` · `parts._size`)과
바이트까지 같고, 조각 나누기는 조각을 차례대로 · 상한 안으로 · 빠짐없이 나눈다.

틀이 아는 모양을 다 넣는다: 정의(ontology) · source · 백필 칸 · 별칭 모드 · 객체가 아닌 행 ·
무덤 · 한 타입의 여러 파일 · 정의의 차례(`objects_order`).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from relay_core import bundle, parts, sp
from relay_core.diff import write_batches


def _run(tmp_path: Path, *, models: int = 30, edges: int = 20) -> Path:
    run = tmp_path / "send"
    sp.pipeline.cmd_init(run, title="시험 묶음")
    manifest = json.loads((run / "bundle.json").read_text(encoding="utf-8"))
    manifest.update(
        objects_order=["t_site", "t_model"],
        relations_order=["r_at"],
        source="plm",
        backfill=True,
    )
    (run / "bundle.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    write_batches(
        run,
        "objects",
        {
            "t_model": [
                {
                    "key": f"M{n}",
                    "label": f'모델 {n} — "인용"',
                    "properties": {"k": n},
                    "_source": {"row": n},
                }
                for n in range(models)
            ],
            "t_site": [{"key": "S1", "label": "시험소"}],
        },
        {"t_model": "ws", "t_site": None},
    )
    extra = run / "objects" / "t_model-extra.json"
    extra.write_text(
        json.dumps(
            {
                "type_slug": "t_model",
                "rows": [{"key": "MX"}, "not a row"],
                "aliases_mode": "replace",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    write_batches(
        run,
        "relations",
        {"t_model": [{"src": f"M{n}", "relation": "r_at", "dst": "S1"} for n in range(edges)]},
        {},
    )
    (run / "ontology.json").write_text(
        json.dumps({"types": [{"slug": "t_model", "label": "모델"}]}, ensure_ascii=False),
        "utf-8",
    )
    (run / "tombstones.json").write_text(
        json.dumps({"objects": [{"type_slug": "t_model", "key": "GONE"}], "relations": []}),
        "utf-8",
    )
    return run


def test_흘려_쓴_지문과_크기가_틀의_것과_같다(tmp_path: Path) -> None:
    run = _run(tmp_path)
    body: dict[str, Any] = sp.pipeline.payload(sp.pipeline.load(run))
    got = bundle.payload(run)
    assert got.digest == sp.pipeline.digest(body)
    assert got.size == parts._size(body) and got.edges == 20
    assert "".join(bundle.pieces(run)) == json.dumps(body, sort_keys=True, ensure_ascii=False)


def test_조각은_차례대로_상한_안으로_빠짐없이(tmp_path: Path) -> None:
    run = _run(tmp_path, models=400, edges=300)
    whole = bundle.payload(run)
    cut = parts.split(run, 6000, max_edges=120)
    assert len(cut.parts) > 3 and cut.digest == whole.digest and cut.size == whole.size

    seen_objects: list[str] = []
    seen_edges: list[str] = []
    graves: list[Any] = []
    for number, folder in enumerate(cut.parts, start=1):
        loaded = sp.pipeline.load(folder)
        assert loaded.manifest["part"] == {"number": number, "total": len(cut.parts)}
        assert f"조각 {number}/{len(cut.parts)}" in loaded.manifest["title"]
        body = sp.pipeline.payload(loaded)
        rows = sum(len(one["rows"]) for one in body["objects"] + body["relations"])
        assert parts._size(body) <= 6000 + parts._HEAD or rows == 1
        assert sum(len(one.rows) for one in loaded.relations) <= 120
        seen_objects += [row["key"] for one in loaded.objects for row in one.rows]
        seen_edges += [row["src"] for one in loaded.relations for row in one.rows]
        graves += (loaded.tombstones or {}).get("objects") or []
    original = sp.pipeline.load(run)
    assert seen_objects == [
        row["key"] for one in original.objects for row in one.rows if isinstance(row, dict)
    ]
    assert seen_edges == [row["src"] for one in original.relations for row in one.rows]
    assert graves == [{"type_slug": "t_model", "key": "GONE"}]
