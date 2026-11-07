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
  nothing: '보낼 것 없음',
  planned: '계획 생성',
  waiting: '같은 계획 대기',
  blocked: '멈춤',
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
  }
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
        {trend.loading && <p className="text-muted-foreground text-sm">읽는 중…</p>}
      </div>
    )
  }
  const rows = trend.data.rows
  if (rows.length === 0) {
    return (
      <p className="text-muted-foreground text-sm">
        아직 기록된 실행이 없습니다 — 실행이 끝날 때마다(실패한 실행도) 한 줄씩 쌓입니다.
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
        최근 {rows.length}번
        {trend.data.total > rows.length && `(남은 기록 ${trend.data.total}번 중)`} — 실패 {failed} · 멈춤{' '}
        {blocked}
        {held > 0 && `(받기 점검 ${held})`} · 평균 {seconds.toFixed(0)}초
      </p>
      <div className="grid gap-3 lg:grid-cols-3">
        <Figure caption="원천 행 — 미러 · 받은 행">
          <Chart
            kind="line"
            data={data}
            x="at"
            series={[
              { key: 'mirror', label: '미러' },
              { key: 'received', label: '받은 행' },
            ]}
            height={200}
            title="실행마다 미러와 받은 행의 수"
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
            title="실행마다 계획의 생성 · 변경 · 삭제 건수"
          />
        </Figure>
        <Figure caption="실행 시간(초)">
          <Chart
            kind="line"
            data={data}
            x="at"
            series={[{ key: 'seconds', label: '초' }]}
            height={200}
            title="실행마다 걸린 시간(초)"
          />
        </Figure>
      </div>
      {troubles.length > 0 && (
        <ul className="divide-y rounded-md border text-sm">
          {troubles.reverse().map((one) => (
            <li key={one.at} className="flex flex-wrap items-baseline gap-x-3 px-3 py-1.5">
              <span className="text-muted-foreground w-36 shrink-0 text-xs">{shownDateTime(one.at)}</span>
              <span className="font-medium">
                {one.held ? '받기 점검 멈춤' : (STATUS_LABEL[one.status] ?? one.status)}
              </span>
              {one.stage && <span className="text-muted-foreground text-xs">{one.stage} 단계에서</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
