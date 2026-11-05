/**
 * 연결 지도 — **이 플랫폼의 첫 화면.** 어느 원천의 어느 API 가 어느 파이프라인을 거쳐 어느
 * Standard Platform 의 무슨 타입으로 가나, 그리고 그 너머 누가 그것을 가져가나.
 *
 * 서버가 정의에서 매번 계산한다(손으로 그린 그림이 없다). 대상에 못 닿으면 그 대상만 빨갛게
 * 그리고 나머지는 그대로 — 대상 하나가 꺼졌다고 지도 전체가 안 뜨면 원인을 볼 자리가 없다.
 */

import { useMemo } from 'react'
import { RefreshCw } from 'lucide-react'
import { useSearchParams } from 'react-router-dom'

import { relayApi } from '@/modules/relay/api'
import {
  buildGraph,
  isFiltered,
  nodesOfKind,
  statusOptions,
  visibleIds,
} from '@/modules/relay/graph'
import type { MapFilter } from '@/modules/relay/graph'
import { RelayFlow } from '@/modules/relay/RelayFlow'
import { EmptyState } from '@/shared/components/EmptyState'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { PageHeader } from '@/shared/components/PageHeader'
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
        {line('text-amber-500 dark:text-amber-400')}멈춤
      </li>
      <li className="flex items-center gap-1">{line('text-destructive')}실패 · 정의 오류</li>
      <li className="flex items-center gap-1">
        {line('text-muted-foreground/60', true)}초안(꺼짐 — 정의 판을 켜야 돕니다)
      </li>
      <li className="flex items-center gap-1">
        {line('text-muted-foreground/40', true)}그 너머(이 플랫폼이 보내지 않음)
      </li>
      <li>선 굵기 = 지난 실행에서 그 길로 보낸 건수</li>
    </ul>
  )
}

export default function RelayMapPage() {
  const map = useResource(() => relayApi.map(), [])
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

  const pick = (key: keyof MapFilter, value: string) => {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value)
    else next.delete(key)
    setParams(next, { replace: true })
  }

  const pipelines = graph ? nodesOfKind(graph, 'pipeline') : []
  const shownPipelines = visible ? pipelines.filter((one) => visible.has(one.id)) : pipelines

  return (
    <div className="space-y-4">
      <PageHeader
        title="연결 지도"
        description="원천 시스템의 API 가 어느 파이프라인을 거쳐 어느 Standard Platform 의 무슨 타입으로 가는지 봅니다. 정의에서 매번 계산합니다 — 파이프라인을 누르면 칸 대응 표와 실행 이력이 열립니다."
        actions={
          <Button size="sm" variant="outline" onClick={map.reload} disabled={map.loading}>
            <RefreshCw className={map.loading ? 'animate-spin' : undefined} />
            다시 읽기
          </Button>
        }
      />

      <ErrorNotice error={map.error} />

      {map.loading && !map.data && (
        <p className="text-muted-foreground text-sm">
          지도를 그리는 중… 대상 SP 마다 지금 닿는지 묻느라 몇 초 걸릴 수 있습니다.
        </p>
      )}

      {map.data && map.data.problems && map.data.problems.length > 0 && (
        <Alert className="border-amber-500/40 bg-amber-500/5">
          <AlertTitle>살펴볼 것 {map.data.problems.length}건</AlertTitle>
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
          title="파이프라인이 없습니다"
          hint={
            <>
              정의 파일(<span className="font-mono">relay-pipeline/1</span>)은 서버의 파이프라인
              폴더(<span className="font-mono">RELAY_PIPELINES_DIR</span>)에 둡니다. 거기 넣고{' '}
              「다시 읽기」 를 누르면 여기 그려집니다 — <span className="font-mono">*.example.json</span>{' '}
              은 본보기라 빠집니다. AI(MCP)가 정의 판 초안을 지어도 여기 「초안(꺼짐)」 으로 그려집니다.
            </>
          }
        />
      )}

      {graph && pipelines.length > 0 && (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <select
              aria-label="원천 시스템"
              className={SELECT}
              value={filter.system}
              onChange={(event) => pick('system', event.target.value)}
            >
              <option value="">원천 시스템 전부</option>
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
              <option value="">대상 전부</option>
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
              <option value="">상태 전부</option>
              {statusOptions(graph).map((one) => (
                <option key={one.value} value={one.value}>
                  {one.label}
                </option>
              ))}
            </select>
            {isFiltered(filter) && (
              <Button size="sm" variant="ghost" onClick={() => setParams({}, { replace: true })}>
                거르기 풀기
              </Button>
            )}
            <span className="text-muted-foreground ml-auto text-xs">
              파이프라인 {shownPipelines.length}
              {visible ? ` / ${pipelines.length}` : ''} · 그린 시각{' '}
              {shownDateTime(map.data?.generated_at)}
            </span>
          </div>

          <Legend />

          {shownPipelines.length === 0 ? (
            <EmptyState
              title="거르기에 걸리는 파이프라인이 없습니다"
              hint="원천 시스템 · 대상 · 상태 중 하나를 「전부」 로 돌리거나 「거르기 풀기」 를 누르세요."
            />
          ) : (
            <div className="bg-muted/20 h-[70vh] min-h-[480px] overflow-hidden rounded-md border">
              {/* 거르기가 바뀌면 새로 맞춰 보인다 — 남은 것이 화면 한쪽 구석에 작게 남지 않게. */}
              <RelayFlow key={params.toString()} graph={graph} visible={visible} />
            </div>
          )}
        </>
      )}
    </div>
  )
}
