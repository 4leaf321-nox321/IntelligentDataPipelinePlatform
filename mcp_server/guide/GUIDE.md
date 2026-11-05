# datarelay MCP 가이드

`get_guide(topic)` 이 이 파일의 `## ` 절 하나를 돌려준다. 고치면 다음 호출부터 바로 따라간다.

## start

**datarelay 는 레거시 시스템(PLM · MES)의 데이터를 받아 Standard Platform(SP) 인스턴스에 넣는다.**
AI 의 자리는 **레시피(`sp-table/1`)와 파이프라인 정의(`relay-pipeline/1` — 원천 어댑터 · 대상)를 짓고
시험하는 것**이다 — 켜기 · 적용은 사람이 한다. 새 원천(MES 의 새 API · 파일 · DB 뷰)을 붙이는 일은
`get_guide('adapter')`, 대상 SP 에 타입이 없으면 `get_guide('types')`.

레시피를 지을 때의 차례:

1. `relay_map` — 어떤 파이프라인이 있고 어느 원천 · 대상에 붙었나.
2. `pipeline_get(slug)` — 지금 도는 레시피(파일 · 판 N), 칸 대응(계보), 상태.
3. `source_sample(slug)` — 원천의 **평평한 열** 표본(어댑터의 `columns` 가 낸 이름). 레시피는 이 열
   이름만 읽는다.
4. `target_schema(slug)` — 대상 SP 의 타입 · 칸 · 자료형 · 고를 값 · 참조 대상.
5. `recipe_get(slug)` — 지금 도는 레시피(출발점). `version` 을 주면 그 판.
6. 초안을 지어 `recipe_draft_save(slug, recipe, note)` — 판 번호가 생긴다. 켜진 판은 그대로다.
7. `recipe_test(slug, version)` — 미러에 돌려 본다. **대상에 아무것도 안 보낸다.** 미해결 · 행 수 ·
   「켜면 바뀌는 것」(생성 · 고침 · 무덤) · 값을 가린 보고서를 읽고 고친다 → 6 으로.
8. 깨끗해지면 사람에게 알린다: **「화면 › 파이프라인 › 레시피 판 에서 판 N 을 켜 주세요」.** 켜는 도구는
   없다 — 사람이 시험 결과를 보고 켠다.

지킬 것:

- **고를 값(enum)은 대상 정의의 값으로만** 낸다 — 원천 코드는 `map` 으로 옮긴다. 대응에 없는 값은 그
  칸을 비우고 보고서에 남는다.
- **식별자는 원천의 안 바뀌는 ID 그대로.** 이름으로 만들지 않는다.
- 참조 칸은 `{"key_of": "<타입>"}` — 같은 행에서 그 타입의 식별자를 같은 규칙으로 얻는다.
- 행마다 값이 갈리는 칸은 미해결이 된다 — 그 칸이 윗단계(과제)가 아니라 행(모델)의 것이라는 신호다.
  정의를 고치거나 `collect` · `join` 으로 적는다.
- 짐작으로 채우지 않는다. 모르면 사람에게 묻는다.

## adapter

**새 원천을 붙인다** — 파이프라인 정의(`relay-pipeline/1`) 하나가 「어디서 받아 · 무엇을 열로 내어 · 어느 SP 에
· 어떤 `source` 로」 다. 레시피(무엇을 만드나)는 따로 짓는다(`recipe`).

차례:

1. `adapter_kinds()` — 종류마다 꼭 적을 칸 · 모든 칸의 기본값 · 설명. 고르는 법:

   | 원천이 주는 것 | kind | 핵심 칸 |
   | --- | --- | --- |
   | OData(v4 · v2) | `odata` | `service_url` · `entity_set` · `expand` · `modified_field`(증분) · `deleted` |
   | JSON REST(쪽 넘김) | `rest` | `base_url` · `path` · `rows_path` · `paging` · `since_param` |
   | CSV · Excel(xlsx) · JSON 파일 | `file` | `location` — 서버 폴더(`/data/relay/drop/…`) · 공유 폴더(`smb://서버/공유/폴더/lots-*.csv`, 계정은 `auth: {"kind": "basic", "user": "CORP\\svc", "token_env": …}`) · URL. `*` 면 가장 늦게 바뀐 것 · `sheet` · `header_row` · `encoding` |
   | XML · SOAP | `xml` | `url` · `rows_xpath` · `namespaces` · `envelope`(SOAP, `{since}` 자리) · `soap_action` |
   | DB 뷰(읽기 전용 — Oracle · MSSQL · PostgreSQL) | `sql` | `dsn_env`(값은 `oracle+oracledb://…?service_name=…` · `mssql+pymssql://…/<DB>`) · `table`(`스키마.뷰`) 또는 `query`(SELECT 하나) · `modified_column` |

2. `source_discover(adapter)` — 주소 · 인증만 적고 불러 본다(`key` · `columns` 는 아직 몰라도 된다). 돌려준
   `found.paths`(칸의 자리 · 보기 값)와 `found.columns`(열 이름 제안)를 본다. OData 는 `entity_set` 없이 부르면
   `entity_sets`(엔티티 셋 · 키 · 탐색 속성)부터 보인다.
3. 정의를 짓는다 — `columns` 는 `{레시피가 읽을 열 이름: 원천의 자리}`. 이름은 대문자 · `_`(`TASK_CODE`), 자리는
   점으로 잇는다(`Task.Code`) · XML 은 행 기준 XPath. `key` 는 **원천의 안 바뀌는 ID**, `deleted` 는 지움 표시 칸.
4. `pipeline_draft_save(slug, definition, note)` → 판 번호와 `problems`.
5. `pipeline_test(slug, version)` — 원천을 받아 보고 대상을 본다. **대상에 아무것도 안 보낸다.**
   `source.rows` · `source.always_empty`(늘 빈 열 — 자리가 틀렸다는 신호) · `target.token` ·
   `target.types_missing` 을 읽고 고친다 → 4 로.
6. 레시피를 짓는다(`get_guide('recipe')`, `recipe_draft_save`) → `pipeline_test(slug, version, recipe_version=N)`
   이 받은 원천을 그 레시피로 바꿔 본다(`recipe.rows` · `recipe.unresolved` · `recipe.would_send`).
7. 깨끗해지면 사람에게: **「화면 › 파이프라인 › 정의 판 에서 판 N, 레시피 판 에서 판 M 을 켜 주세요」.**
   켜는 도구는 없다. 시험하다 만 초안은 `pipeline_draft_discard` · `recipe_draft_discard` 로 치운다.

원천이 크면 시험은 앞 줄만 받는다(`source.capped` — 기본 5,000줄). 그때 `recipe.would_send` 는 없다 — 덜 받은 채
견주면 나머지가 다 지움으로 보인다. 행 수 · 미해결 · 늘 빈 열로 판단한다.

정의의 모양:

```json
{
  "format": "relay-pipeline/1",
  "slug": "mes-lot",
  "source": "mes",
  "system": "MES",
  "schedule": {"daily_at": "02:00"},
  "adapter": {"kind": "file", "location": "/data/relay/drop/mes/lots-*.csv", "key": "LOT_ID",
              "encoding": "auto", "deleted": "DEL_YN"},
  "target": {"name": "제조 데이터허브", "server": "http://…:8040", "token_env": "MFG_HUB_TOKEN"}
}
```

지킬 것:

- **비밀은 적지 않는다** — 토큰 · 비밀번호 · DSN 은 환경 변수의 **이름**(`auth.token_env` · `dsn_env` ·
  `target.token_env`)만. 값을 적으면 저장이 거절된다. 그 변수를 서버에 넣는 일은 관리자에게 부탁한다.
- `source` 는 **원천 시스템의 이름**(plm · mes) — 대상 타입의 잠금(`managed_by`)과 같아야 적재된다.
  이미 도는 정의와 다르면 `problems` 가 말한다.
- `target.server` 는 **등록된 SP 인스턴스**만 — PLM 은 자주검증 Datahub, MES 는 제조 데이터허브. 다른 플랫폼에
  뿌리지 않는다(허브가 한다). 기준정보를 만들지 않는다(대표 플랫폼이 뿌린다).
- 탐색 · 시험은 울타리 안만 닿는다 — 부를 수 있는 주소(`RELAY_SOURCE_HOSTS` · 켜진 정의의 주소) · 읽을 수 있는
  폴더(`RELAY_FILE_ROOTS`) · DB(`RELAY_ALLOW_SQL`). 403 이면 까닭을 사람에게 그대로 전한다 — 돌아갈 길을
  찾지 않는다.
- **자동 적용(`auto_apply`)은 사람이 정할 일이다 — 사람이 말하지 않았으면 적지 않는다.** 적으면 타이머의 실행이
  기준(`after_manual` 번 사람이 적용한 뒤 · 생성 + 고침 `max_changes` 이하 · 지움 없음, `allow_gone`)을 넘은 계획을
  사람 없이 적용한다. 정의를 켜는 사람이 확인 창에서 본다. MCP 의 `pipeline_run` 은 자동 적용하지 않는다.
- 일정(`schedule`)은 `{"daily_at": "HH:MM"}` 또는 `{"every_minutes": N}`. 지움 표시가 없는 원천은 몇 번에 한 번
  전량을 받아야 지워진 것을 안다(OData `full_every`).

## types

**대상 SP 에 레시피가 만들 타입이 없을 때**(`pipeline_test` 의 `target.types_missing`). datarelay 는 타입을 만들지
않는다 — 타입은 **대상 SP 의 정의**이고 그 SP 의 MCP(또는 화면)로 들인다.

1. 무엇이 있는지 먼저 본다 — 대상 SP MCP 의 `ontology_schema`. 비슷한 타입이 있으면 새로 만들지 않고 그것을
   쓴다. 기준정보(조직 · 공통 코드 …)는 **대표 플랫폼이 뿌린다** — 만들지 않고 참조만 한다.
2. 정의를 지어 **`source` 를 단 묶음으로** 미리 본다 — 대상 SP MCP 의 `bundle_import({"source": "mes",
   "ontology": {...}})`. 계획만 나온다. `source` 를 달아야 그 타입이 `managed_by=mes` 로 **잠긴다**(SP 화면에서
   사람이 못 고친다 — 정본은 원천). `ontology_import` 로 넣으면 잠기지 않는다.
3. 계획을 사람에게 보이고 **적용은 사람이**(대상 SP 의 「작업」 화면). 그다음 `pipeline_test` 를 다시 부른다.

## recipe


엑셀 · CSV 는 행마다 AI 가 판단하지 않는다(규약 5장). **대응 파일**을 만들고 도구가 옮긴다.
AI 가 할 일은 대응 파일을 쓰는 것과 보고서를 읽고 규칙을 고치는 것이다.

```bash
python sp_table.py <대응.table.json> <원천.csv> runs/2026-09-13-plm-models
python sp_pipeline.py validate runs/2026-09-13-plm-models      # 그 뒤는 여느 실행과 같다
```

- 실행 폴더를 **새로** 만든다(있으면 멈춤). `bundle.json` 의 `sources` · `objects_order` ·
  `notes`(열 → 칸 대응)를 채우고, 5000행이 넘는 타입은 `<slug>-001.json` … 으로 나눈다.
- **같은 식별자가 여러 행에 나오면 한 객체로 모은다.** 칸 값이 행마다 다르면 그 칸은 보내지
  않고 `unresolved.json` 에 올린다 — 대개 그 칸이 윗단계(과제)가 아니라 행(모델)의 칸이라는 뜻이다.
  정의와 대응을 고쳐 **새 실행 폴더로 다시 돌린다.** 미해결을 손으로 지워 넘기지 않는다.
- 보고서 `table-report.txt` 는 값을 **가린 패턴**(영문 `A` · 숫자 `9` · 한글 `가`)으로만 적는다 —
  사내 밖으로 옮겨 규칙을 의논해도 된다. 원천 값은 실행 폴더의 파일에만 있다.
- 인코딩은 BOM 붙은 UTF-8, 안 되면 CP949 로 읽는다(`source.encoding` 으로 고정).

### 대응 파일 (`sp-table/1`)

```json
{
  "format": "sp-table/1",
  "title": "과제-모델 표",
  "workspace_slug": "plm",
  "ontology": "plm-ontology.json",
  "source": {"encoding": "auto", "delimiter": ","},
  "dictionaries": {"region": ["KOR", "NA", "EUR"]},
  "parsers": {
    "model_name": {
      "column": "모델명", "separator": "_", "rare_below": 6,
      "slots": [
        {"name": "base", "kinds": {"SM": "^SM-", "OL": "^OL-"}},
        {"name": "marker", "optional": true, "values": ["D1", "D2"]},
        {"name": "region", "dictionary": "region"},
        {"name": "revision", "optional": true, "kinds": {"OS 버전": "^[0-9]{2}$", "차수": "^[A-Z][0-9]$"}},
        {"name": "carrier"}
      ],
      "checks": [
        {"slot": "revision", "suffix_of": "과제명", "joiner": "_"},
        {"require": "revision", "when_column": "SRA일", "in": ["Skip"]}
      ]
    }
  },
  "types": [
    {"type_slug": "plm_project", "key": {"column": "프로젝트명"}},
    {"type_slug": "plm_task", "key": {"column": "과제코드", "upper": true}, "label": {"column": "과제명"},
     "fields": {
       "project": {"key_of": "plm_project"},
       "sra_on": {"column": "SRA일", "date": true, "blank": ["Skip", "-"]},
       "sra_kind": {"column": "SRA일", "map": {"<date>": "실적", "Skip": "면제", "-": "미도래"}}
     }},
    {"type_slug": "plm_model", "key": {"column": "모델명"},
     "fields": {
       "task": {"key_of": "plm_task"},
       "region": {"parser": "model_name", "slot": "region"},
       "revision_kind": {"parser": "model_name", "slot": "revision", "part": "kind"}
     }}
  ],
  "relations": [
    {"relation": "tested_at", "type_slug": "plm_task",
     "src": {"key_of": "plm_task"}, "dst": {"key_of": "plm_site"},
     "evidence_note": {"value": "PLM 표"},
     "properties": {"progress": {"column": "Test진행상태"}}}
  ]
}
```

- **`types` 의 차례가 넣는 차례다** — 참조되는 타입을 먼저.
- `ontology` 는 대응 파일 기준 상대 경로. 있으면 실행 폴더의 `ontology.json` 이 된다.
- 칸 하나를 얻는 법 — 넷 중 하나:

  | 모양 | 뜻 |
  | --- | --- |
  | `{"column": "열"}` | 열의 값(앞뒤 공백 뗌). 빈 칸은 `null`(비움) |
  | `{"key_of": "<type_slug>"}` | 그 타입의 식별자를 **같은 규칙으로** — 참조 칸에 쓴다. 따로 적으면 대문자 · 공백 처리가 어긋난다 |
  | `{"parser": "<이름>", "slot": "<조각>", "part": "kind"?}` | 해석기의 조각 값, `part: "kind"` 면 그 조각의 종류 이름 |
  | `{"value": ...}` | 고정값 |
  | `{"collect": {...}}` | **여러 행의 값을 목록으로** — 같은 식별자가 여러 행에 나올 때 값이 갈리는 것이 **답인** 칸(별칭 여럿, 담당 여럿). 같은 값은 한 번, 순서는 나온 차례 |
  | `{"join": {...}, "separator": "; "}` | 여러 행의 값을 **한 글로** 잇는다(줄마다 다른 메모) |

  `collect` · `join` 이 없으면 값이 갈리는 것은 **미해결**이다 — 어느 쪽이 맞는지 도구가
  모르기 때문이다. 갈려도 되는 칸은 그 둘로 적어 둔다.

  `column` 에 붙는 것: `upper`(대문자로) · `blank`(빈 칸으로 볼 값들) · `date`(`YYYY-MM-DD` ·
  `.` · `/` → ISO, 아니면 비우고 보고서에) · `map`(값 → 값. `<date>` 는 날짜 모양, `<blank>` 는 빈 칸.
  대응에 없는 값은 비우고 보고서에).
- `key` 가 빈 행은 그 타입에서 건너뛴다(보고서에 건수). `label` 을 안 적으면 `key` 와 같다.
- **참조 대조** — 코어(허브에서 받은 PLM 기준정보)를 가리키는 칸은 `column` 에 `match` 를 붙인다:
  `{"column": "모델명", "match": {"keys": "plm_model", "prefix": true, "on_missing": "blank"}}`.
  - `keys`: 식별자를 받을 타입 slug(플랫폼에서 — `SP_SERVER` · `SP_TOKEN`) 또는 `@파일`(한 줄에 하나,
    대응 파일 기준 경로).
  - 맞추는 차례: 그대로 → 대소문자만 다름 → (`prefix` 면) 앞부분이 **하나뿐인** 식별자. 둘 이상이면
    고르지 않는다.
  - `on_missing`: `blank`(기본 — 비워 넣고 보고서에) · `unresolved`(미해결로 올려 사람에게) · `keep`
    (그대로 보냄 — 플랫폼이 거절하면 묶음 전체가 막힌다).
  - 보고서 `[참조 대조]` 에 그대로 · 대소문자 · 앞부분 · 여러 개 · 없음의 건수와 못 맞춘 것의 가린 패턴.

### 선(관계) — `relations`

객체만 만들면 관계는 사람이 같은 원천을 **두 번 읽어** 따로 파일을 만들어야 했다.

| 칸 | 뜻 |
| --- | --- |
| `relation` | 관계 종류 slug |
| `type_slug` | **출발 타입**(파일이 그 타입으로 나간다). `types` 에 있어야 한다 |
| `src` · `dst` | 끝점 — 칸 하나를 얻는 법 넷을 그대로 쓴다(`key_of` 를 주로 쓴다) |
| `evidence_note` | 선택 — **왜 이었나.** 고정값(`{"value": …}`)이나 열 |
| `properties` | 선택 — 선에 붙는 값(근거 건수처럼). 모양은 플랫폼이 관계 종류의 정의로 본다 |

- **세 끝(출발 · 종류 · 도착)이 같으면 한 줄로 모은다** — 한 행에 여러 타입이 섞인 표에서는
  같은 선이 수십 행에 되풀이된다.
- 붙는 값이 행마다 다르면 **짐작하지 않고** `unresolved.json` 에 올린다(객체 칸과 같은 규칙).
  그 선은 값 없이 나간다 — 플랫폼의 빈 칸은 「안 건드림」 이라 옛 값이 남는다.
- 끝점이 빈 행은 선을 만들지 않는다(보고서에 건수).

### 해석기 — 이름에 박힌 조각

- `separator` 로 자른 조각을 **`slots` 의 차례대로** 채운다. **위치가 자리를 정하고, 모양은
  그 자리 안의 종류만 가른다.**
- 조각 조건: `values`(목록) · `dictionary`(`dictionaries` 의 이름) · `pattern`(정규식) ·
  `kinds`(종류 이름 → 정규식, **적은 차례대로** 먼저 맞는 것). 조건이 없으면 무엇이든 맞는다 —
  마지막 자리처럼 **자리로만 정해지는** 조각.
- `optional` 조각은 맞을 때만 먹는다. 모든 조각을 남김없이 채우는 길이 **하나일 때만** 읽힌 것 —
  없으면 `못 나눔`, 둘 이상이면 `두 갈래로 읽힘`. **고르지 않는다.**
- `checks` — 읽힌 뒤의 검증. `suffix_of`: 조각이 있으면 다른 열이 `<joiner><조각>` 으로 끝나야
  한다. `require`: `when_column` 이 `in` 중 하나면 그 조각이 있어야 한다.
- 못 읽었거나 검증을 어긴 행은 **넣되 해석 칸을 모두 비운다**(`null`). 보고서에 이유별 패턴이 나온다.
- `rare_below`: 조각 값이 그보다 적게 나오면 보고서에 드문 값으로 — 마스터와 대조할 후보. 조각에
  `rare_below` 를 따로 적으면 그 조각만 바뀐다(기본 모델코드처럼 원래 종류가 많은 조각은 `0`).


> 위 절은 StandardPlatform `pipeline/AGENTS.md`(v0.4.72) 「표를 규칙으로 옮기기」 를 옮긴 것이다. 변환기는
> `vendor/sp_pipeline/sp_table.py`(같은 판)다. datarelay 에서는 원천 표(CSV)를 사람이 주지 않는다 —
> 어댑터가 원천 API 를 받아 `columns` 의 이름으로 편 표를 만든다(`source_sample`).
