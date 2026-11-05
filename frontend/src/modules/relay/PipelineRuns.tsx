/**
 * 파이프라인이 지금 어디까지 왔나 — 워터마크 · 마지막 적용 · 적용을 기다리는 계획, 그리고 이력과
 * 최근 작업.
 *
 * 계획은 **대상 SP 의 작업 화면**에서 읽는다(새 탭). 이 플랫폼의 작업(실행 · 적용)은 여기
 * 작업 화면에서 본다.
 *
 * 이력은 자동 적용이 한 적용(「자동 적용」)을 사람의 적용과 가른다. 타이머의 실행이 기준을 못 넘어
 * 계획이 사람을 기다리면(「자동 적용 안 함」) 그 까닭을 줄 아래에 늘어놓는다.
 */

import type { ReactNode } from 'react'
import { ExternalLink } from 'lucide-react'
import { Link } from 'react-router-dom'

import { countsLine, historyOf, jobLink, stateOf, targetJobLink } from '@/modules/relay/api'
import type { HistoryEvent, PipelineDetail } from '@/modules/relay/api'
import { StatusBadge } from '@/shared/components/StatusBadge'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/shared/components/ui/table'
import { shownDateTime } from '@/shared/lib/datetime'

const EVENT_LABEL: Record<string, string> = {
  planned: '계획 올림',
  applied: '적용',
  blocked: '멈춤',
  nothing: '보낼 것 없음',
  auto_skipped: '자동 적용 안 함',
}

/** 이력 한 줄의 이름 — 자동 적용이 한 적용은 사람의 적용과 다른 이름을 쓴다. */
function eventLabel(one: HistoryEvent): string {
  if (one.event === 'applied' && one.by === 'auto') return '자동 적용'
  return EVENT_LABEL[one.event ?? ''] ?? one.event ?? '—'
}

const JOB_KIND: Record<string, string> = { relay_run: '실행', relay_apply: '적용' }

/** 이름 · 값 한 줄 — 상세의 사실 목록이 같은 꼴을 쓴다. */
export function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[8rem_1fr] gap-2 text-sm">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-all">{children}</dd>
    </div>
  )
}

export function PipelineState({ detail }: { detail: PipelineDetail }) {
  const state = stateOf(detail)
  const pending = state.pending
  return (
    <dl className="space-y-1.5">
      <Fact label="워터마크">
        <span className="font-mono text-xs">{state.watermark || '—'}</span>
        {state.watermark ? null : (
          <span className="text-muted-foreground ml-1 text-xs">(다음 실행은 전량을 받습니다)</span>
        )}
      </Fact>
      <Fact label="마지막 적용">{shownDateTime(state.applied_at)}</Fact>
      <Fact label="적용 대기 계획">
        {pending ? (
          <div className="space-y-1">
            <div className="flex flex-wrap items-center gap-2">
              <StatusBadge kind="relay_pipeline" value={pending.ok ? 'pending' : 'blocked'} />
              <span className="text-muted-foreground text-xs">{shownDateTime(pending.at)}</span>
              {detail.target_server && pending.job_id && (
                <a
                  href={targetJobLink(detail.target_server, pending.job_id)}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-0.5 text-xs underline"
                >
                  대상 SP 에서 계획 보기
                  <ExternalLink className="size-3" />
                </a>
              )}
            </div>
            {pending.summary && <p className="text-xs whitespace-pre-line">{pending.summary}</p>}
            {!pending.ok && (
              <p className="text-destructive text-xs">
                계획에 오류가 있어 적용할 수 없습니다 — 원인을 고치고 다시 실행하세요.
              </p>
            )}
          </div>
        ) : (
          <span className="text-muted-foreground">없음</span>
        )}
      </Fact>
    </dl>
  )
}

export function PipelineHistory({ detail }: { detail: PipelineDetail }) {
  const history = historyOf(detail)
  if (history.length === 0) {
    return (
      <p className="text-muted-foreground text-sm">
        아직 돈 적이 없습니다 — 시스템 관리자가 「지금 실행」 을 누르면 첫 줄이 생깁니다.
      </p>
    )
  }
  return (
    <ul className="divide-y rounded-md border">
      {history.map((one, index) => {
        const line = countsLine(one.counts)
        return (
          <li key={`${one.at}:${index}`} className="flex flex-wrap items-baseline gap-x-3 gap-y-1 px-3 py-2 text-sm">
            <span className="text-muted-foreground w-36 shrink-0 text-xs">{shownDateTime(one.at)}</span>
            <span className="font-medium">
              {eventLabel(one)}
              {one.event === 'planned' && one.ok === false && (
                <span className="text-destructive ml-1 text-xs">(계획 오류)</span>
              )}
            </span>
            {line && <span className="text-muted-foreground text-xs">{line}</span>}
            {one.job_id && (
              <span className="text-muted-foreground font-mono text-xs">{one.job_id.slice(0, 8)}</span>
            )}
            {(one.reasons ?? []).length > 0 && (
              <ul className="text-muted-foreground w-full list-disc pl-4 text-xs sm:pl-40">
                {one.reasons!.map((reason) => (
                  <li key={reason}>{reason}</li>
                ))}
              </ul>
            )}
          </li>
        )
      })}
    </ul>
  )
}

export function PipelineJobs({ detail }: { detail: PipelineDetail }) {
  if (detail.jobs.length === 0) {
    return <p className="text-muted-foreground text-sm">이 파이프라인의 작업이 아직 없습니다.</p>
  }
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>시각</TableHead>
          <TableHead>종류</TableHead>
          <TableHead>상태</TableHead>
          <TableHead>결과</TableHead>
          <TableHead />
        </TableRow>
      </TableHeader>
      <TableBody>
        {detail.jobs.map((one) => (
          <TableRow key={one.id}>
            <TableCell className="whitespace-nowrap">{shownDateTime(one.created_at)}</TableCell>
            <TableCell>{JOB_KIND[one.kind] ?? one.kind}</TableCell>
            <TableCell>
              <StatusBadge kind="job" value={one.status} />
            </TableCell>
            <TableCell className="max-w-md text-xs whitespace-normal">
              {one.error ? (
                <span className="text-destructive">{one.error}</span>
              ) : (
                <span className="text-muted-foreground">{one.summary || '—'}</span>
              )}
            </TableCell>
            <TableCell className="text-right">
              <Link to={jobLink(one.id)} className="text-xs underline">
                작업 화면
              </Link>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

