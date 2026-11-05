/**
 * 시험이 쓰는 지도 — 서버(`services.build_map`)가 내는 차례 그대로 지어냈다.
 *
 *   PLM ─ GET /tasks  ─ plm-tasks  (정상)     ─┐
 *       └ GET /models ─ plm-models (적용 대기) ─┴ 자주검증 Datahub ─ plm_task · plm_project · plm_model
 *                                                                  └ (그 너머) 리포트 아카이브
 *   MES ─ GET /lots   ─ mes-lots   (실패)     ── 제조 데이터허브(닿지 않음) ─ lot
 *
 * plm-tasks 는 마지막 대조에서 대상과 3건 어긋났다(`detail.drift`).
 */

import type { MapEdge, MapNode, ReconcileReport, RelayMap } from '@/modules/relay/api'

const node = (over: Partial<MapNode> & Pick<MapNode, 'id' | 'kind' | 'label'>): MapNode => ({
  sub: '',
  status: '',
  status_label: '',
  link: '',
  detail: {},
  ...over,
})

const edge = (source: string, target: string, weight = 0, status = ''): MapEdge => ({
  source,
  target,
  weight,
  status,
})

export function sampleMap(): RelayMap {
  return {
    generated_at: '2026-11-05T01:00:00+00:00',
    problems: ['제조 데이터허브(http://mfg)에 닿지 않습니다.'],
    nodes: [
      node({ id: 'system:plm', kind: 'system', label: 'PLM', sub: 'plm' }),
      node({ id: 'endpoint:tasks', kind: 'endpoint', label: 'GET /tasks · plm.local', sub: 'http://plm.local/tasks' }),
      node({
        id: 'pipeline:plm-tasks',
        kind: 'pipeline',
        label: 'plm-tasks',
        sub: 'source plm · rest',
        status: 'ok',
        status_label: '정상',
        link: '/relay/pipelines/plm-tasks',
        detail: {
          applied_at: '2026-11-04T09:00:00+00:00',
          pending_link: '',
          drift: 3,
          reconciled_at: '2026-11-05T03:00:00+00:00',
        },
      }),
      node({ id: 'target:hub', kind: 'target', label: '자주검증 Datahub', sub: 'datahub · v1.2', status: 'ok', status_label: '연결됨' }),
      node({ id: 'type:hub:plm_task', kind: 'type', label: 'plm_task', sub: '자주검증 Datahub' }),
      node({ id: 'type:hub:plm_project', kind: 'type', label: 'plm_project', sub: '자주검증 Datahub' }),
      node({ id: 'endpoint:models', kind: 'endpoint', label: 'GET /models · plm.local', sub: 'http://plm.local/models' }),
      node({
        id: 'pipeline:plm-models',
        kind: 'pipeline',
        label: 'plm-models',
        sub: 'source plm · rest',
        status: 'pending',
        status_label: '적용 대기',
        link: '/relay/pipelines/plm-models',
        detail: { pending_job: 'j-1', pending_link: 'http://hub/jobs?job=j-1', drift: 0, reconciled_at: null },
      }),
      node({ id: 'type:hub:plm_model', kind: 'type', label: 'plm_model', sub: '자주검증 Datahub' }),
      node({ id: 'system:mes', kind: 'system', label: 'MES', sub: 'mes' }),
      node({ id: 'endpoint:lots', kind: 'endpoint', label: 'OData Lots · mes.local', sub: 'http://mes.local/odata/Lots' }),
      node({
        id: 'pipeline:mes-lots',
        kind: 'pipeline',
        label: 'mes-lots',
        sub: 'source mes · odata',
        status: 'failed',
        status_label: '실패',
        link: '/relay/pipelines/mes-lots',
      }),
      node({ id: 'target:mfg', kind: 'target', label: '제조 데이터허브', sub: 'http://mfg', status: 'unreachable', status_label: '닿지 않음' }),
      node({ id: 'type:mfg:lot', kind: 'type', label: 'lot', sub: '제조 데이터허브' }),
      node({ id: 'downstream:hub:report', kind: 'downstream', label: '리포트 아카이브', sub: '마지막 수신 2026-11-04T08:00' }),
    ],
    edges: [
      edge('system:plm', 'endpoint:tasks'),
      edge('endpoint:tasks', 'pipeline:plm-tasks', 0, 'ok'),
      edge('pipeline:plm-tasks', 'target:hub', 10, 'ok'),
      edge('target:hub', 'type:hub:plm_task', 7),
      edge('target:hub', 'type:hub:plm_project', 3),
      edge('system:plm', 'endpoint:models'),
      edge('endpoint:models', 'pipeline:plm-models', 0, 'pending'),
      edge('pipeline:plm-models', 'target:hub', 4, 'pending'),
      edge('target:hub', 'type:hub:plm_model', 4),
      edge('system:mes', 'endpoint:lots'),
      edge('endpoint:lots', 'pipeline:mes-lots', 0, 'failed'),
      edge('pipeline:mes-lots', 'target:mfg', 0, 'failed'),
      edge('target:mfg', 'type:mfg:lot', 0),
      edge('target:hub', 'downstream:hub:report'),
    ],
  }
}

/**
 * 위 지도에 초안 하나를 더한다 — AI(MCP)가 지은 정의 판이 아직 안 켜진 새 파이프라인. 돌지 않으므로
 * 건수도 레시피(타입)도 없다.
 */
export function withDraft(map: RelayMap): RelayMap {
  return {
    ...map,
    nodes: [
      ...map.nodes,
      node({ id: 'endpoint:parts', kind: 'endpoint', label: 'GET /parts · plm.local', sub: 'http://plm.local/parts' }),
      node({
        id: 'pipeline:plm-parts',
        kind: 'pipeline',
        label: 'plm-parts',
        sub: 'source plm · rest',
        status: 'draft',
        status_label: '초안(꺼짐)',
        link: '/relay/pipelines/plm-parts',
      }),
    ],
    edges: [
      ...map.edges,
      edge('system:plm', 'endpoint:parts'),
      edge('endpoint:parts', 'pipeline:plm-parts', 0, 'draft'),
      edge('pipeline:plm-parts', 'target:hub', 0, 'draft'),
    ],
  }
}

/**
 * 대조 보고서 — 상세가 싣는 꼴(갈래마다 본보기 20개까지 · `counts` 가 참 건수). `plm_task` 는 빠짐이
 * 본보기보다 많고(25건 중 20건), `plm_project` 는 이 원천이 관리하지 않아 대상에만 있는 것이 보정
 * 계획에서 빠진다. 식별자는 지어낸 것이다.
 */
export function sampleReconcile(over: Partial<ReconcileReport> = {}): ReconcileReport {
  const missing = Array.from({ length: 25 }, (_, index) => `T${String(index + 1).padStart(3, '0')}`)
  return {
    at: '2026-11-05T03:00:00Z',
    baseline: 'runs/20261104T090000Z',
    source: 'plm',
    drift: 29,
    summary: '대상과 어긋남 29건(빠짐 25 · 안 씀 1 · 다름 1 · 대상에만 2) — 기준 객체 40건. 확인 못 한 참조 칸 2.',
    problems: [],
    types: {
      plm_task: {
        rows: 30,
        missing: missing.slice(0, 20),
        deprecated: ['T100'],
        changed: { T200: ['label', 'due'] },
        extra: ['T900'],
        counts: { missing: 25, deprecated: 1, changed: 1, extra: 1 },
        unverified: 2,
        managed: true,
        absent: false,
      },
      plm_project: {
        rows: 10,
        missing: [],
        deprecated: [],
        changed: {},
        extra: ['P900'],
        counts: { missing: 0, deprecated: 0, changed: 0, extra: 1 },
        unverified: 0,
        managed: false,
        absent: false,
      },
    },
    ...over,
  }
}

/** 위 보고서의 전부 — `GET …/reconcile` 이 주는 꼴(본보기를 자르지 않는다). */
export function fullReconcile(): ReconcileReport {
  const shown = sampleReconcile()
  const missing = Array.from({ length: 25 }, (_, index) => `T${String(index + 1).padStart(3, '0')}`)
  return { ...shown, types: { ...shown.types, plm_task: { ...shown.types.plm_task, missing } } }
}
