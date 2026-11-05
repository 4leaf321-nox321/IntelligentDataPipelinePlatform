/**
 * 시험이 쓰는 지도 — 서버(`services.build_map`)가 내는 차례 그대로 지어냈다.
 *
 *   PLM ─ GET /tasks  ─ plm-tasks  (정상)     ─┐
 *       └ GET /models ─ plm-models (적용 대기) ─┴ 자주검증 Datahub ─ plm_task · plm_project · plm_model
 *                                                                  └ (그 너머) 리포트 아카이브
 *   MES ─ GET /lots   ─ mes-lots   (실패)     ── 제조 데이터허브(닿지 않음) ─ lot
 */

import type { MapEdge, MapNode, RelayMap } from '@/modules/relay/api'

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
        detail: { applied_at: '2026-11-04T09:00:00+00:00', pending_link: '' },
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
        detail: { pending_job: 'j-1', pending_link: 'http://hub/jobs?job=j-1' },
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
