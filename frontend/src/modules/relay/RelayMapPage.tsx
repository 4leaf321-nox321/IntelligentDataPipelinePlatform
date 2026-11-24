/**
 * 연결 지도 — **이 플랫폼의 첫 화면.** 어느 원천의 어느 API 가 어느 파이프라인을 거쳐 어느
 * Standard Platform 의 무슨 타입으로 가나, 그리고 그 너머 누가 그것을 가져가나.
 *
 * 서버가 정의에서 매번 계산한다(손으로 그린 그림이 없다). 대상에 못 닿으면 그 대상만 빨갛게
 * 그리고 나머지는 그대로 — 대상 하나가 꺼졌다고 지도 전체가 안 뜨면 원인을 볼 자리가 없다.
 */

import { useMemo, useRef } from 'react'
import { RadioTower, RefreshCw } from 'lucide-react'
import { useSearchParams } from 'react-router-dom'

import { relayApi } from '@/modules/relay/api'
import {
  ATTENTION_FILTER,
  buildGraph,
  isFiltered,
  nodesOfKind,
  statusOptions,
  visibleIds,
} from '@/modules/relay/graph'
import type { MapFilter } from '@/modules/relay/graph'
import { SUMMARY_AT, summaryGraph } from '@/modules/relay/summary'
import type { MapNode } from '@/modules/relay/api'
import { RelayFlow } from '@/modules/relay/RelayFlow'
import { EmptyState } from '@/shared/components/EmptyState'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { PageHeader } from '@/shared/components/PageHeader'
import { StatusBadge } from '@/shared/components/StatusBadge'
import { Alert, AlertDescription, AlertTitle } from '@/shared/components/ui/alert'
import { Button } from '@/shared/components/ui/button'
import { useResource } from '@/shared/hooks/useResource'
import { shownDateTime } from '@/shared/lib/datetime'

const SELECT = 'border-input bg-background h-8 rounded-md border px-2 text-sm'

/** 범례 — 선의 색 · 모양이 무엇을 말하나. 판정은 배지와 같은 표에서 온다. */
function Legend() {
  const line = (className: string, dashed = false) => (
    <svg width="28" height="8" aria-hidden className={className}>
      <line
        x1="0"
        y1="4"
        x2="28"
        y2="4"
        stroke="currentColor"
        strokeWidth="2"
        strokeDasharray={dashed ? '5 4' : undefined}
      />
    </svg>
  )
  return (
    <ul className="text-muted-foreground flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
      <li className="flex items-center gap-1">{line('text-muted-foreground/60')}정상</li>
      <li className="flex items-center gap-1">
        {line('text-amber-500 dark:text-amber-400', true)}적용 대기
      </li>
      <li className="flex items-center gap-1">
        {line('text-amber-500 dark:text-amber-400')}중단
      </li>
      <li className="flex items-center gap-1">{line('text-destructive')}실패 · 정의 오류</li>
      <li className="flex items-center gap-1">
        {line('text-muted-foreground/60', true)}초안(미사용, 정의 버전을 사용하기 전에는 실행되지 않음)
      </li>
      <li className="flex items-center gap-1">
        {line('text-muted-foreground/40', true)}후속 수신처(이 플랫폼이 전송하지 않음)
      </li>
      <li>연결선 굵기 = 마지막 실행에서 해당 경로로 전송한 건수</li>
      <li className="flex items-center gap-1">
        <StatusBadge kind="relay_reconcile" value="drift" />
        N건 = 마지막 대조에서 대상 SP와 기준 출력이 다른 객체 수
      </li>
    </ul>
  )
}

type MapView = 'summary' | 'full'

/** 대상 상태를 조회한 시각 중 가장 오래된 것 — 지도에 보이는 연결 상태가 언제의 것인가. */
export function oldestCheck(nodes: MapNode[]): string | null {
  const times = nodes
    .filter((one) => one.kind === 'target')
    .map((one) => (one.detail as { checked_at?: string | null } | undefined)?.checked_at)
    .filter((one): one is string => Boolean(one))
    .sort()
  return times[0] ?? null
}

export default function RelayMapPage() {
  // [대상 다시 조회]는 다음 한 번만 대상을 지금 조회한다 — [새로 고침]은 저장값을 읽는다.
  const probeNext = useRef(false)
  const map = useResource(() => {
    const probe = probeNext.current
    probeNext.current = false
    return relayApi.map(probe)
  }, [])
  const [params, setParams] = useSearchParams()
  const filter: MapFilter = {
    system: params.get('system') ?? '',
    target: params.get('target') ?? '',
    status: params.get('status') ?? '',
  }
  const graph = useMemo(() => (map.data ? buildGraph(map.data) : null), [map.data])
  const visible = useMemo(
    () => (graph ? visibleIds(graph, filter) : null),
    // 거르기는 주소의 글 셋이다 — 객체가 아니라 그 값이 바뀔 때만 다시 센다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [graph, filter.system, filter.target, filter.status],
  )

  const pick = (key: keyof MapFilter | 'view', value: string) => {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value)
    else next.delete(key)
    setParams(next, { replace: true })
  }

  const pipelines = graph ? nodesOfKind(graph, 'pipeline') : []
  const shownPipelines = visible ? pipelines.filter((one) => visible.has(one.id)) : pipelines
  const viewParam = params.get('view')
  const view: MapView =
    viewParam === 'summary' || viewParam === 'full'
      ? viewParam
      : pipelines.length > SUMMARY_AT
        ? 'summary'
        : 'full'
  const drawn = useMemo(
    () => (graph && view === 'summary' ? summaryGraph(graph, visible) : graph),
    [graph, view, visible],
  )
  const checkedAt = map.data ? oldestCheck(map.data.nodes) : null

  /** 원천 시스템 · 대상을 클릭하면 그 경로만 전체 보기로(초점 보기). */
  const focus = (node: MapNode) => {
    if (node.kind !== 'system' && node.kind !== 'target') return
    const next = new URLSearchParams(params)
    next.set(node.kind, node.id)
    next.set('view', 'full')
    setParams(next, { replace: true })
  }
  const probeTargets = () => {
    probeNext.current = true
    map.reload()
  }

  return (
    <div className="space-y-4">
      <PageHeader
        title="연결 지도"
        description="원천 시스템의 API가 어느 파이프라인을 거쳐 어느 Standard Platform의 어떤 타입으로 전송되는지 표시합니다. 파이프라인을 클릭하면 상세 화면이, 원천 시스템이나 대상을 클릭하면 해당 경로만 표시됩니다. 대상 SP의 연결 상태는 5분마다 조회한 값입니다."
        actions={
          <>
            <Button size="sm" variant="outline" onClick={probeTargets} disabled={map.loading}>
              <RadioTower />
              대상 다시 조회
            </Button>
            <Button size="sm" variant="outline" onClick={map.reload} disabled={map.loading}>
              <RefreshCw className={map.loading ? 'animate-spin' : undefined} />
              새로 고침
            </Button>
          </>
        }
      />

      <ErrorNotice error={map.error} />

      {map.loading && !map.data && (
        <p className="text-muted-foreground text-sm">연결 지도를 불러오는 중…</p>
      )}

      {map.data && map.data.problems && map.data.problems.length > 0 && (
        <Alert className="border-amber-500/40 bg-amber-500/5">
          <AlertTitle>점검 항목 {map.data.problems.length}건</AlertTitle>
          <AlertDescription>
            <ul className="list-disc space-y-0.5 pl-4">
              {map.data.problems.map((one) => (
                <li key={one}>{one}</li>
              ))}
            </ul>
          </AlertDescription>
        </Alert>
      )}

      {graph && pipelines.length === 0 && (
        <EmptyState
          title="파이프라인 없음"
          hint={
            <>
              정의 파일(<span className="font-mono">relay-pipeline/1</span>)은 서버의 파이프라인
              폴더(<span className="font-mono">RELAY_PIPELINES_DIR</span>)에 둡니다. 폴더에 추가하고 [새로
              고침]을 클릭하면 지도에 표시됩니다. <span className="font-mono">*.example.json</span>은 예시
              파일이므로 제외됩니다. AI(MCP)가 작성한 정의 버전 초안도 '초안(미사용)'으로 표시됩니다.
            </>
          }
        />
      )}

      {graph && pipelines.length > 0 && (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <div className="flex items-center gap-1" role="group" aria-label="보기">
              {(
                [
                  ['summary', '요약'],
                  ['full', '전체'],
                ] as const
              ).map(([value, label]) => (
                <Button
                  key={value}
                  size="sm"
                  variant={view === value ? 'secondary' : 'ghost'}
                  aria-pressed={view === value}
                  onClick={() => pick('view', value)}
                >
                  {label}
                </Button>
              ))}
            </div>
            <select
              aria-label="원천 시스템"
              className={SELECT}
              value={filter.system}
              onChange={(event) => pick('system', event.target.value)}
            >
              <option value="">원천 시스템 전체</option>
              {nodesOfKind(graph, 'system').map((one) => (
                <option key={one.id} value={one.id}>
                  {one.label}
                </option>
              ))}
            </select>
            <select
              aria-label="대상"
              className={SELECT}
              value={filter.target}
              onChange={(event) => pick('target', event.target.value)}
            >
              <option value="">대상 전체</option>
              {nodesOfKind(graph, 'target').map((one) => (
                <option key={one.id} value={one.id}>
                  {one.label}
                </option>
              ))}
            </select>
            <select
              aria-label="상태"
              className={SELECT}
              value={filter.status}
              onChange={(event) => pick('status', event.target.value)}
            >
              <option value="">상태 전체</option>
              <option value={ATTENTION_FILTER}>확인 필요만</option>
              {statusOptions(graph).map((one) => (
                <option key={one.value} value={one.value}>
                  {one.label}
                </option>
              ))}
            </select>
            {isFiltered(filter) && (
              <Button size="sm" variant="ghost" onClick={() => setParams({}, { replace: true })}>
                필터 해제
              </Button>
            )}
            <span className="text-muted-foreground ml-auto text-xs">
              파이프라인 {shownPipelines.length}
              {visible ? ` / ${pipelines.length}` : ''} · 생성 시각{' '}
              {shownDateTime(map.data?.generated_at)}
              {checkedAt && ` · 대상 상태 조회 ${shownDateTime(checkedAt)}`}
            </span>
          </div>

          <Legend />

          {shownPipelines.length === 0 ? (
            <EmptyState
              title="필터 조건에 맞는 파이프라인 없음"
              hint="원천 시스템, 대상, 상태 중 하나를 '전체'로 변경하거나 [필터 해제]를 클릭하세요."
            />
          ) : (
            <div className="bg-muted/20 h-[70vh] min-h-[480px] overflow-hidden rounded-md border">
              {/* 거르기가 바뀌면 새로 맞춰 보인다 — 남은 것이 화면 한쪽 구석에 작게 남지 않게. */}
              <RelayFlow
                key={params.toString()}
                graph={drawn ?? graph}
                visible={view === 'summary' ? null : visible}
                onNodeClick={focus}
              />
            </div>
          )}
        </>
      )}
    </div>
  )
}
