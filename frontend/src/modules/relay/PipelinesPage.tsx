/**
 * 파이프라인 — 운영 현황. 상태별 건수 · 확인 필요 보기 · 검색 · 정렬 · 쪽 넘기기 · 적용 대기 계획의 일괄 적용.
 *
 * 파이프라인이 수십 개를 넘으면 정상인 것까지 늘어놓은 표는 읽히지 않는다. 그래서 확인이 필요한 것이 있으면
 * 기본 보기는 그것만이고, 정상은 건수로만 남긴다(2026-11-24, 판정은 `board.ts`).
 *
 * 지도와 같은 응답(`/relay/map`)을 읽는다 — 목록이 따로 계산하면 지도와 다른 말을 하는 날이 온다. 보기 · 필터 ·
 * 검색 · 정렬 · 쪽은 주소에 둔다(알림의 링크가 '실패만' 같은 보기로 바로 연다).
 */

import { useMemo, useState } from 'react'
import { Plus, Search } from 'lucide-react'
import { Link, useSearchParams } from 'react-router-dom'

import { AlertWebhookStatus } from '@/modules/relay/AlertWebhookStatus'
import { jobLink, pipelineDetailOf, relayApi } from '@/modules/relay/api'
import {
  SORT_OPTIONS,
  applicable,
  attentionReason,
  boardRows,
  defaultView,
  isSortKey,
  lastRunOf,
  needsAttention,
  statusCounts,
} from '@/modules/relay/board'
import type { BoardFilter, BoardView } from '@/modules/relay/board'
import { BulkApply } from '@/modules/relay/BulkApply'
import type { BulkApplyResult } from '@/modules/relay/BulkApply'
import { DriftBadge } from '@/modules/relay/DriftBadge'
import { buildGraph, pipelineRows } from '@/modules/relay/graph'
import { useAuth } from '@/shared/auth/AuthContext'
import { isSystemAdmin } from '@/shared/auth/roles'
import { EmptyState } from '@/shared/components/EmptyState'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { PageHeader } from '@/shared/components/PageHeader'
import { Pagination } from '@/shared/components/Pagination'
import { StatusBadge } from '@/shared/components/StatusBadge'
import { Alert, AlertDescription } from '@/shared/components/ui/alert'
import { Button } from '@/shared/components/ui/button'
import { Input } from '@/shared/components/ui/input'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/shared/components/ui/table'
import { useResource } from '@/shared/hooks/useResource'
import { shownDateTime } from '@/shared/lib/datetime'
import { cn } from '@/shared/lib/utils'

const NO_PIPELINES_HINT =
  "시스템 관리자가 [새 연결]에서 원천의 주소와 형식을 입력해 생성합니다. 정의 파일(relay-pipeline/1)을 서버의 파이프라인 폴더(RELAY_PIPELINES_DIR)에 두거나 AI(MCP)가 정의 버전 초안을 작성해도 됩니다. 사용하기 전까지는 '초안(미사용)'으로 표시됩니다."

/** 한 쪽의 행 수. */
export const PAGE_SIZE = 50

const SELECT = 'border-input bg-background h-8 rounded-md border px-2 text-sm'

const chip = (active: boolean) =>
  cn(
    'inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-sm tabular-nums transition-colors',
    active ? 'border-primary bg-primary/10 font-medium' : 'hover:bg-muted/50',
  )

export default function PipelinesPage() {
  const { user } = useAuth()
  const admin = isSystemAdmin(user)
  const map = useResource(() => relayApi.map(), [])
  const [params, setParams] = useSearchParams()
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [applied, setApplied] = useState<BulkApplyResult[] | null>(null)
  const rows = useMemo(() => (map.data ? pipelineRows(buildGraph(map.data)) : null), [map.data])

  const sort = params.get('sort') ?? ''
  const viewParam = params.get('view')
  const filter: BoardFilter = {
    view: viewParam === 'all' || viewParam === 'attention' ? viewParam : defaultView(rows ?? []),
    status: params.get('status') ?? '',
    query: params.get('q') ?? '',
    sort: isSortKey(sort) ? sort : 'attention',
  }
  const offset = Math.max(Number(params.get('offset') ?? 0) || 0, 0)
  const shown = useMemo(
    () => (rows ? boardRows(rows, filter) : []),
    // 필터는 주소의 값이다 — 객체가 아니라 그 값이 바뀔 때만 다시 계산한다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [rows, filter.view, filter.status, filter.query, filter.sort],
  )
  const page = shown.slice(offset, offset + PAGE_SIZE)
  const attention = rows ? rows.filter(needsAttention).length : 0
  const pickable = admin ? page.filter(applicable) : []
  const chosen = (rows ?? []).filter((row) => picked.has(row.node.id) && applicable(row))

  /** 주소의 값을 바꾼다 — 필터가 바뀌면 첫 쪽으로. */
  const set = (changes: Record<string, string>) => {
    const next = new URLSearchParams(params)
    for (const [key, value] of Object.entries(changes)) {
      if (value) next.set(key, value)
      else next.delete(key)
    }
    if (!('offset' in changes)) next.delete('offset')
    setParams(next, { replace: true })
  }
  const pickView = (view: BoardView) => set({ view, status: '' })
  const toggle = (id: string, on: boolean) =>
    setPicked((before) => {
      const next = new Set(before)
      if (on) next.add(id)
      else next.delete(id)
      return next
    })

  return (
    <div className="space-y-4">
      <PageHeader
        title="파이프라인"
        description="원천의 API 하나를 레시피로 변환해 대상 SP에 전송하는 단위입니다. 확인이 필요한 파이프라인이 먼저 표시됩니다. 파이프라인을 클릭하면 속성 매핑표와 실행 이력을 확인하고 즉시 실행할 수 있습니다."
        actions={
          admin && (
            <Button asChild size="sm">
              <Link to="/relay/pipelines/new">
                <Plus />새 연결
              </Link>
            </Button>
          )
        }
      />
      <ErrorNotice error={map.error} />
      {map.loading && !map.data && <p className="text-muted-foreground text-sm">불러오는 중…</p>}
      {rows && rows.length === 0 && <EmptyState title="파이프라인 없음" hint={NO_PIPELINES_HINT} />}
      {rows && rows.length > 0 && (
        <>
          <div className="flex flex-wrap items-center gap-2" role="group" aria-label="보기">
            <button
              type="button"
              className={chip(filter.view === 'attention' && !filter.status)}
              aria-pressed={filter.view === 'attention' && !filter.status}
              onClick={() => pickView('attention')}
            >
              확인 필요 <span className="font-semibold">{attention.toLocaleString()}</span>
            </button>
            <button
              type="button"
              className={chip(filter.view === 'all' && !filter.status)}
              aria-pressed={filter.view === 'all' && !filter.status}
              onClick={() => pickView('all')}
            >
              전체 <span className="font-semibold">{rows.length.toLocaleString()}</span>
            </button>
            <span className="bg-border mx-1 h-5 w-px" aria-hidden />
            {statusCounts(rows).map((one) => (
              <button
                key={one.value}
                type="button"
                className={chip(filter.status === one.value)}
                aria-pressed={filter.status === one.value}
                onClick={() =>
                  filter.status === one.value
                    ? set({ status: '' })
                    : set({ view: 'all', status: one.value })
                }
              >
                <StatusBadge kind="relay_pipeline" value={one.value} />
                {one.count.toLocaleString()}
              </button>
            ))}
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <div className="relative w-full max-w-xs">
              <Search className="text-muted-foreground absolute top-2 left-2 size-4" aria-hidden />
              <Input
                aria-label="파이프라인 검색"
                placeholder="파이프라인, 원천 시스템, API, 대상"
                className="pl-8"
                value={filter.query}
                onChange={(event) => set({ q: event.target.value })}
              />
            </div>
            <select
              aria-label="정렬"
              className={SELECT}
              value={filter.sort}
              onChange={(event) => set({ sort: event.target.value === 'attention' ? '' : event.target.value })}
            >
              {SORT_OPTIONS.map((one) => (
                <option key={one.value} value={one.value}>
                  {one.label}
                </option>
              ))}
            </select>
            {admin && chosen.length > 0 && (
              <BulkApply
                rows={chosen}
                onDone={(results) => {
                  setApplied(results)
                  setPicked(new Set())
                  map.reload()
                }}
              />
            )}
          </div>

          {applied && (
            <Alert>
              <AlertDescription>
                <p className="mb-1">
                  적용 작업 {applied.filter((one) => one.jobId).length.toLocaleString()}건을 추가했습니다.
                  {applied.some((one) => one.error) && ' 일부 요청은 거부되었습니다.'}
                </p>
                <ul className="space-y-0.5 text-xs">
                  {applied.map((one) => (
                    <li key={one.slug}>
                      <span className="font-mono">{one.slug}</span>{' '}
                      {one.jobId ? (
                        <Link to={jobLink(one.jobId)} className="text-primary hover:underline">
                          작업 확인
                        </Link>
                      ) : (
                        <span className="text-destructive">{one.error}</span>
                      )}
                    </li>
                  ))}
                </ul>
              </AlertDescription>
            </Alert>
          )}

          {shown.length === 0 ? (
            <EmptyState
              title={filter.view === 'attention' && !filter.query ? '확인이 필요한 파이프라인 없음' : '조건에 맞는 파이프라인 없음'}
              hint={
                filter.view === 'attention' && !filter.query
                  ? `전체 ${rows.length.toLocaleString()}개 파이프라인에 확인이 필요한 항목이 없습니다. [전체]를 클릭하면 모든 파이프라인이 표시됩니다.`
                  : '검색어나 상태 필터를 변경하거나 [전체]를 클릭하세요.'
              }
            />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  {pickable.length > 0 && (
                    <TableHead className="w-8">
                      <input
                        type="checkbox"
                        aria-label="이 페이지의 적용 대기 계획 전체 선택"
                        checked={pickable.every((row) => picked.has(row.node.id))}
                        onChange={(event) => {
                          for (const row of pickable) toggle(row.node.id, event.target.checked)
                        }}
                      />
                    </TableHead>
                  )}
                  <TableHead>파이프라인</TableHead>
                  <TableHead>원천 시스템 · API</TableHead>
                  <TableHead>대상</TableHead>
                  <TableHead>상태</TableHead>
                  <TableHead>마지막 실행</TableHead>
                  <TableHead>마지막 적용</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {page.map((row) => {
                  const { node, systems, endpoints, targets } = row
                  const reason = attentionReason(row)
                  return (
                    <TableRow key={node.id}>
                      {pickable.length > 0 && (
                        <TableCell>
                          {applicable(row) && (
                            <input
                              type="checkbox"
                              aria-label={`${node.label} 선택`}
                              checked={picked.has(node.id)}
                              onChange={(event) => toggle(node.id, event.target.checked)}
                            />
                          )}
                        </TableCell>
                      )}
                      <TableCell>
                        <Link to={node.link || '#'} className="font-mono font-medium hover:underline">
                          {node.label}
                        </Link>
                        <p className="text-muted-foreground text-xs">{node.sub}</p>
                      </TableCell>
                      <TableCell>
                        {systems.map((one) => one.label).join(', ') || '—'}
                        {endpoints.map((one) => (
                          <p key={one.id} className="text-muted-foreground text-xs">
                            {one.label}
                            <span className="font-mono"> · {one.sub}</span>
                          </p>
                        ))}
                      </TableCell>
                      <TableCell>{targets.map((one) => one.label).join(', ') || '—'}</TableCell>
                      <TableCell>
                        <span className="flex flex-wrap items-center gap-1.5">
                          <StatusBadge kind="relay_pipeline" value={node.status} />
                          <DriftBadge node={node} />
                        </span>
                        {reason && (
                          <p className="text-muted-foreground mt-0.5 line-clamp-2 max-w-xs text-xs" title={reason}>
                            {reason}
                          </p>
                        )}
                      </TableCell>
                      <TableCell className="whitespace-nowrap">{shownDateTime(lastRunOf(row) || null)}</TableCell>
                      <TableCell className="whitespace-nowrap">
                        {shownDateTime(pipelineDetailOf(node).applied_at)}
                      </TableCell>
                    </TableRow>
                  )
                })}
              </TableBody>
            </Table>
          )}
          <Pagination
            total={shown.length}
            limit={PAGE_SIZE}
            offset={offset}
            unit="개"
            onChange={(next) => set({ offset: String(next) })}
          />
        </>
      )}
      {admin && <AlertWebhookStatus />}
    </div>
  )
}
