/**
 * 실행 추이 — 실행마다 받은 행 · 미러 · 계획의 건수 · 걸린 시간(작업 폴더의 `trend.json`, 서버
 * `relay_core.runner`). 원천이 갑자기 줄거나 늘고, 계획이 갑자기 커지고, 실행이 점점 느려지는 것이
 * 눈에 보이게 — 받기 점검의 기준(정의의 `intake`)을 정할 근거도 된다.
 *
 * 작업 기록(「최근 작업」)은 며칠 뒤 지워지지만 추이는 작업 폴더에 남는다(최근 400번).
 *
 * 그림 라이브러리는 이 덩어리에만 든다 — 상세 화면이 `lazy()` 로 매단다(그림을 안 보는 사람까지
 * 받지 않게).
 */

import type { ReactNode } from 'react'

import { relayApi } from '@/modules/relay/api'
import type { TrendRow } from '@/modules/relay/api'
import { Chart } from '@/shared/charts'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { useResource } from '@/shared/hooks/useResource'
import { shownDateTime } from '@/shared/lib/datetime'

/** 실행의 결과 — 실행 이력의 말과 같다. */
const STATUS_LABEL: Record<string, string> = {
  nothing: '변경 사항 없음',
  planned: '계획 생성',
  waiting: '동일 계획 대기',
  blocked: '중단',
  failed: '실패',
}

/** 가로축의 이름 — 「11-07 01:00」. 같은 분에 두 번 돌면 이름이 겹치므로 차례를 붙인다. */
function axisLabel(at: string, index: number, all: TrendRow[]): string {
  const shown = shownDateTime(at).slice(5, 16)
  const twin = all.some((other, at2) => at2 !== index && shownDateTime(other.at).slice(5, 16) === shown)
  return twin ? `${shown} #${index + 1}` : shown
}

function point(row: TrendRow, index: number, all: TrendRow[]): Record<string, unknown> {
  return {
    at: axisLabel(row.at, index, all),
    mirror: row.mirror ?? null,
    received: row.received ?? null,
    created: row.plan?.created ?? 0,
    changed: row.plan?.changed ?? 0,
    gone: row.plan?.gone ?? 0,
    seconds: row.seconds,
    peak: row.peak_mb ?? null,
    convert: row.memory?.['변환'] ?? null,
    compare: row.memory?.['비교'] ?? null,
  }
}

/** 실행의 변환 · 비교 방식 — 「증분 변환 · 증분 비교」 또는 「전체 변환(레시피가 바뀜) · 전체 비교」. 기록이 없으면
 * 빈 문자열. */
export function modeLine(row: TrendRow | undefined): string {
  if (!row?.mode) return ''
  const full = row.full_reason ? `전체 변환(${row.full_reason})` : '전체 변환'
  const convert = row.mode === 'incremental' ? '증분 변환' : full
  if (!row.compared) return convert
  return `${convert} · ${row.compared === 'incremental' ? '증분 비교' : '전체 비교'}`
}

/** 실행의 단계별 소요 시간 — 「수신 4 · 변환 30 · 비교 2초」. 기록이 없으면 빈 문자열. */
export function secondsLine(row: TrendRow | undefined): string {
  const stages = Object.entries(row?.seconds_by_stage ?? {})
  if (!stages.length) return ''
  return `${stages.map(([stage, one]) => `${stage} ${Math.round(one).toLocaleString()}`).join(' · ')}초`
}

/** 마지막 실행의 단계별 최고 메모리 — 「수신 120 · 변환 840 · 비교 410 MB」. 기록이 없으면 빈 문자열. */
export function memoryLine(row: TrendRow | undefined): string {
  const stages = Object.entries(row?.memory ?? {})
  if (!stages.length) return ''
  return `${stages.map(([stage, mb]) => `${stage} ${Math.round(mb).toLocaleString()}`).join(' · ')} MB`
}

/** 마지막 실행이 어떻게 돌았나 — 방식 · 단계별 최고 메모리 · 소요 시간. 옛 판의 기록에는 없다. */
function LastRun({ row }: { row: TrendRow | undefined }) {
  const lines = [
    ['방식', modeLine(row)],
    ['단계별 최고 메모리', memoryLine(row)],
    ['단계별 소요 시간', secondsLine(row)],
  ].filter(([, text]) => text)
  if (!lines.length) return null
  return (
    <dl className="text-muted-foreground grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs">
      <dt className="col-span-2 font-medium">마지막 실행</dt>
      {lines.map(([name, text]) => (
        <div key={name} className="col-span-2 grid grid-cols-subgrid">
          <dt>{name}</dt>
          <dd>{text}</dd>
        </div>
      ))}
    </dl>
  )
}

function Figure({ caption, children }: { caption: string; children: ReactNode }) {
  return (
    <figure className="min-w-0 space-y-1 rounded-md border p-2">
      <figcaption className="text-muted-foreground text-xs">{caption}</figcaption>
      {children}
    </figure>
  )
}

export default function PipelineTrend({ slug }: { slug: string }) {
  const trend = useResource(() => relayApi.trend(slug), [slug])
  if (!trend.data) {
    return (
      <div className="space-y-2">
        <ErrorNotice error={trend.error} />
        {trend.loading && <p className="text-muted-foreground text-sm">불러오는 중…</p>}
      </div>
    )
  }
  const rows = trend.data.rows
  if (rows.length === 0) {
    return (
      <p className="text-muted-foreground text-sm">
        기록된 실행이 없습니다. 실행이 끝날 때마다(실패 포함) 한 건씩 기록됩니다.
      </p>
    )
  }
  const data = rows.map((row, index) => point(row, index, rows))
  const failed = rows.filter((one) => one.status === 'failed').length
  const blocked = rows.filter((one) => one.status === 'blocked').length
  const held = rows.filter((one) => one.held).length
  const seconds = rows.reduce((sum, one) => sum + one.seconds, 0) / rows.length
  const troubles = rows.filter((one) => one.status === 'failed' || one.status === 'blocked').slice(-5)
  return (
    <div className="space-y-3">
      <p className="text-muted-foreground text-sm">
        최근 {rows.length}회
        {trend.data.total > rows.length && `(전체 기록 ${trend.data.total}회 중)`} · 실패 {failed} · 중단{' '}
        {blocked}
        {held > 0 && `(수신 검증 ${held})`} · 평균 {seconds.toFixed(0)}초
      </p>
      <LastRun row={rows[rows.length - 1]} />
      <div className="grid gap-3 lg:grid-cols-2 xl:grid-cols-4">
        <Figure caption="원천 행 — 미러 · 수신 행">
          <Chart
            kind="line"
            data={data}
            x="at"
            series={[
              { key: 'mirror', label: '미러' },
              { key: 'received', label: '수신 행' },
            ]}
            height={200}
            title="실행별 미러 행 수와 수신 행 수"
          />
        </Figure>
        <Figure caption="계획 — 생성 · 변경 · 삭제(객체 · 관계 합)">
          <Chart
            kind="bar"
            stacked
            data={data}
            x="at"
            series={[
              { key: 'created', label: '생성' },
              { key: 'changed', label: '변경' },
              { key: 'gone', label: '삭제' },
            ]}
            height={200}
            title="실행별 계획의 생성 · 변경 · 삭제 건수"
          />
        </Figure>
        <Figure caption="소요 시간(초)">
          <Chart
            kind="line"
            data={data}
            x="at"
            series={[{ key: 'seconds', label: '초' }]}
            height={200}
            title="실행별 소요 시간(초)"
          />
        </Figure>
        <Figure caption="최고 메모리(MB) — 실행 전체 · 변환 · 비교">
          <Chart
            kind="line"
            data={data}
            x="at"
            series={[
              { key: 'peak', label: '전체' },
              { key: 'convert', label: '변환' },
              { key: 'compare', label: '비교' },
            ]}
            height={200}
            title="실행별 최고 메모리(MB)"
          />
        </Figure>
      </div>
      {troubles.length > 0 && (
        <ul className="divide-y rounded-md border text-sm">
          {troubles.reverse().map((one) => (
            <li key={one.at} className="flex flex-wrap items-baseline gap-x-3 px-3 py-1.5">
              <span className="text-muted-foreground w-36 shrink-0 text-xs">{shownDateTime(one.at)}</span>
              <span className="font-medium">
                {one.held ? '수신 검증 중단' : (STATUS_LABEL[one.status] ?? one.status)}
              </span>
              {one.stage && <span className="text-muted-foreground text-xs">{one.stage} 단계</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
