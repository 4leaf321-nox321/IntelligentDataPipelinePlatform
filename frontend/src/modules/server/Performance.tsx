/**
 * 느린 요청 · 큰 응답(ADR 0026) — **사람이 말하기 전에 본다.**
 *
 * 작업 목록 한 번이 93MB · 4.4초였던 것을, 사용자가 「적용을 누르면 화면이 너무 느리다」 고
 * 말하고 나서야 알았다(2026-10-09). 그 수치는 그 전부터 요청마다 났는데 남는 곳이 없었다. 서버가
 * 요청마다 걸린 시간 · 응답 크기를 남기고, 이 화면은 그것을 **경로 틀**마다 묶어 보인다.
 *
 *   경로 틀     `/api/objects/{type_slug}/{object_id}` — 같은 API 를 한 줄로
 *   p50 · p95  절반이 이보다 빨랐다 · 스무 번 중 한 번만 이보다 느렸다(평균은 느린 몇 번을 묻는다)
 *   최악 사례   줄을 클릭하면 — 누가 · 언제 · 상태 · 시간 · 크기 · 요청 ID
 *
 * 작업(일괄 입력 · 내보내기 · 동기화)이 얼마나 걸리고 얼마나 기다리는지도 같은 자리에 — 오래
 * 걸리는 일을 작업으로 옮겼는데 거기서 느려지면 다시 아무도 모르게 된다.
 *
 * **끊긴 질문**(ADR 0031)도 여기 — 시간 상한에 걸리거나 SPARQL 이 너무 커서 답을 못 받은 물음을 같은
 * 모양끼리 묶는다. 무엇을 지표로 미리 세어 둘지를 짐작이 아니라 실제 물음으로 가른다.
 */

import { Fragment, useState } from 'react'

import { api } from '@/shared/api/client'
import { EmptyState } from '@/shared/components/EmptyState'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { Pagination } from '@/shared/components/Pagination'
import { StatusBadge } from '@/shared/components/StatusBadge'
import { Button } from '@/shared/components/ui/button'
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

export type PerformancePeriod = '1h' | '24h' | '7d'
export type RequestSort = 'p95' | 'max_bytes' | 'calls'

export interface RequestStat {
  method: string
  /** 경로 틀. 맞는 라우트가 없던 요청은 「(없는 경로)」 하나로 모인다. */
  route: string
  calls: number
  p50_ms: number
  p95_ms: number
  max_ms: number
  avg_bytes: number
  max_bytes: number
  /** 대부분이 JSON 이 아닌 응답(파일 · 사진) — 문턱 판정에서 뺀다. */
  file: boolean
  slow: boolean
  large: boolean
}

export interface RequestStats {
  period: PerformancePeriod
  /** 실제로 센 기간의 시작 — 기록이 너무 많으면 기간의 시작보다 늦다(`capped`). */
  since: string
  capped: boolean
  /** 가장 오래된 기록. 없으면 아직 아무것도 안 남긴 것이다. */
  first_recorded_at: string | null
  thresholds: { p95_ms: number; min_calls: number; response_bytes: number }
  items: RequestStat[]
  total: number
  limit: number
  offset: number
}

export interface RequestCase {
  created_at: string
  user_label: string | null
  path: string
  status_code: number
  duration_ms: number
  response_bytes: number
  content_type: string | null
  request_id: string | null
}

export interface JobKindStat {
  kind: string
  label: string
  done: number
  failed: number
  cancelled: number
  /** 지금 대기 · 실행 중. */
  open: number
  run_p50_s: number | null
  run_p95_s: number | null
  run_max_s: number | null
  wait_p50_s: number | null
  wait_p95_s: number | null
  wait_max_s: number | null
  /** 아직 대기인 것 가운데 가장 오래 기다린 것 — 워커가 멎으면 여기만 는다. */
  queued_oldest_s: number | null
}

export interface CutQuestion {
  kind: string
  kind_label: string
  code: string
  method: string
  route: string
  shape: Record<string, unknown>
  count: number
  users: number
  last_at: string
  last_request_id: string | null
}

export interface CutQuestions {
  period: string
  since: string
  items: CutQuestion[]
  total: number
}

export interface JobStats {
  period: PerformancePeriod
  since: string
  items: JobKindStat[]
}

const PERIODS: { value: PerformancePeriod; label: string }[] = [
  { value: '1h', label: '최근 1시간' },
  { value: '24h', label: '24시간' },
  { value: '7d', label: '7일' },
]

const SORTS: { value: RequestSort; label: string }[] = [
  { value: 'p95', label: '느린 순' },
  { value: 'max_bytes', label: '큰 순' },
  { value: 'calls', label: '호출 많은 순' },
]

/** 서버가 강제하는 상한 안에서 고른 값(`shared/pagination.py` 의 MAX_LIMIT 는 200). */
const PER_PAGE = 50

/** 850ms · 1.2초 · 2분 5초 — 표에서 자릿수가 들쭉날쭉하지 않게 단위를 바꾼다. */
export function shownMs(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)}초`
  const minutes = Math.floor(ms / 60_000)
  const seconds = Math.round((ms % 60_000) / 1000)
  return seconds ? `${minutes}분 ${seconds}초` : `${minutes}분`
}

/** 512B · 12KB · 3.4MB — 1024 로 나눈다(서버의 문턱 `LARGE_RESPONSE_MB` 도 그렇게 센다). */
export function shownBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`
  if (bytes < 1024 ** 2) return `${Math.round(bytes / 1024)}KB`
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)}MB`
  return `${(bytes / 1024 ** 3).toFixed(1)}GB`
}

function shownSeconds(seconds: number | null): string {
  return seconds === null ? '—' : shownMs(seconds * 1000)
}

/** 고르는 단추 묶음 — 기간 · 정렬. 눌린 것을 `aria-pressed` 로 말한다. */
function Choice<T extends string>({
  label,
  options,
  value,
  onChange,
}: {
  label: string
  options: { value: T; label: string }[]
  value: T
  onChange: (next: T) => void
}) {
  return (
    <div className="flex items-center gap-1" role="group" aria-label={label}>
      {options.map((one) => (
        <Button
          key={one.value}
          type="button"
          size="sm"
          variant={one.value === value ? 'secondary' : 'ghost'}
          aria-pressed={one.value === value}
          onClick={() => onChange(one.value)}
        >
          {one.label}
        </Button>
      ))}
    </div>
  )
}

/** 그 경로 틀의 최근 최악 사례 — 오래 걸린 순(큰 순으로 보고 있으면 큰 순). */
function Cases({
  route,
  method,
  period,
  bySize,
}: {
  route: string
  method: string
  period: PerformancePeriod
  bySize: boolean
}) {
  const query = new URLSearchParams({
    route,
    method,
    period,
    order: bySize ? 'bytes' : 'duration',
  })
  const cases = useResource(
    () => api.get<RequestCase[]>(`/server/performance/requests/cases?${query.toString()}`),
    [route, method, period, bySize],
  )
  if (cases.error) return <ErrorNotice error={cases.error} />
  if (!cases.data) return <p className="text-muted-foreground text-sm">불러오는 중…</p>
  if (cases.data.length === 0) {
    return (
      <p className="text-muted-foreground text-sm">
        이 기간의 사례가 없습니다 — 보관 기간이 지나 정리됐을 수 있습니다.
      </p>
    )
  }
  return (
    <div className="space-y-1">
      <p className="text-muted-foreground text-xs">
        {bySize ? '가장 큰' : '가장 오래 걸린'} 순 {cases.data.length}건. 요청 ID 로 서버 로그(app.log)에서
        그 요청의 모든 줄을 찾습니다.
      </p>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>시각</TableHead>
            <TableHead>누가</TableHead>
            <TableHead>경로</TableHead>
            <TableHead className="text-right">상태</TableHead>
            <TableHead className="text-right">걸린 시간</TableHead>
            <TableHead className="text-right">크기</TableHead>
            <TableHead>요청 ID</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {cases.data.map((one, index) => (
            <TableRow key={`${one.request_id ?? ''}-${index}`}>
              <TableCell className="whitespace-nowrap">{shownDateTime(one.created_at)}</TableCell>
              <TableCell>{one.user_label ?? '로그인 전'}</TableCell>
              <TableCell className="max-w-80 font-mono text-xs break-all">{one.path}</TableCell>
              <TableCell className="text-right tabular-nums">{one.status_code}</TableCell>
              <TableCell className="text-right tabular-nums">{shownMs(one.duration_ms)}</TableCell>
              <TableCell className="text-right tabular-nums">
                {shownBytes(one.response_bytes)}
              </TableCell>
              <TableCell className="font-mono text-xs">{one.request_id ?? '—'}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}

function Requests({
  period,
  sort,
  onSort,
}: {
  period: PerformancePeriod
  sort: RequestSort
  onSort: (next: RequestSort) => void
}) {
  const [offset, setOffset] = useState(0)
  const [open, setOpen] = useState<string | null>(null)
  const stats = useResource(
    () =>
      api.get<RequestStats>(
        `/server/performance/requests?period=${period}&sort=${sort}&limit=${PER_PAGE}&offset=${offset}`,
      ),
    [period, sort, offset],
  )
  const data = stats.data
  const limits = data?.thresholds

  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-base font-semibold">경로마다</h2>
        <Choice label="정렬" options={SORTS} value={sort} onChange={onSort} />
      </div>
      {limits && (
        <p className="text-muted-foreground text-sm">
          p50 은 호출의 절반이 그보다 빨랐다는 뜻이고, p95 는 스무 번 중 한 번만 그보다 느렸다는
          뜻입니다. <StatusBadge kind="performance" value="slow" /> 은 p95 가{' '}
          {shownMs(limits.p95_ms)}를 넘은 경로(24시간에 {limits.min_calls}번 이상 호출된 것만),{' '}
          <StatusBadge kind="performance" value="large" /> 는 {shownBytes(limits.response_bytes)}
          보다 큰 응답을 낸 경로입니다. <StatusBadge kind="performance" value="file" /> 응답(내려받기 ·
          사진)은 크고 받는 쪽 속도를 타는 것이 정상이라 세지 않습니다. 홈의 「남은 일」 은 최근
          24시간을 이 기준으로 셉니다. 줄을 클릭하면 그 경로의 최악 사례가 표시됩니다.
        </p>
      )}
      <ErrorNotice error={stats.error} />
      {data?.capped && (
        <div className="rounded-md border border-amber-500/40 bg-amber-500/5 p-3 text-sm">
          기록이 많아 {shownDateTime(data.since)} 이후의 최근 기록만 셌습니다 — 기간을 좁히면 전부
          셉니다.
        </div>
      )}
      {data && data.items.length === 0 ? (
        data.first_recorded_at === null ? (
          <EmptyState
            title="아직 기록이 없습니다"
            hint="요청 시간은 이 기능이 들어간 판부터 남깁니다. 화면을 사용하면 몇 초 뒤부터 쌓입니다."
          />
        ) : (
          <EmptyState
            title="이 기간에 기록된 요청이 없습니다"
            hint={`가장 오래된 기록은 ${shownDateTime(data.first_recorded_at)} 입니다. 기간을 넓혀 보세요.`}
          />
        )
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>메서드</TableHead>
              <TableHead>경로 틀</TableHead>
              <TableHead className="text-right">호출</TableHead>
              <TableHead className="text-right">p50</TableHead>
              <TableHead className="text-right">p95</TableHead>
              <TableHead className="text-right">최대</TableHead>
              <TableHead className="text-right">평균 크기</TableHead>
              <TableHead className="text-right">최대 크기</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {(data?.items ?? []).map((one) => {
              const key = `${one.method} ${one.route}`
              const opened = open === key
              return (
                <Fragment key={key}>
                  <TableRow
                    className="cursor-pointer"
                    data-state={opened ? 'selected' : undefined}
                    onClick={() => setOpen(opened ? null : key)}
                  >
                    <TableCell className="font-mono text-xs">{one.method}</TableCell>
                    <TableCell>
                      {/* 행 전체가 눌리지만 키보드로도 열 수 있게 단추를 둔다. */}
                      <button
                        type="button"
                        className="text-left font-mono text-xs break-all hover:underline"
                        aria-expanded={opened}
                        onClick={(event) => {
                          event.stopPropagation()
                          setOpen(opened ? null : key)
                        }}
                      >
                        {one.route}
                      </button>
                      {one.file && <StatusBadge kind="performance" value="file" className="ml-2" />}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {one.calls.toLocaleString('ko-KR')}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">{shownMs(one.p50_ms)}</TableCell>
                    <TableCell className="text-right whitespace-nowrap tabular-nums">
                      {one.slow && <StatusBadge kind="performance" value="slow" className="mr-2" />}
                      {shownMs(one.p95_ms)}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">{shownMs(one.max_ms)}</TableCell>
                    <TableCell className="text-right tabular-nums">
                      {shownBytes(one.avg_bytes)}
                    </TableCell>
                    <TableCell className="text-right whitespace-nowrap tabular-nums">
                      {one.large && (
                        <StatusBadge kind="performance" value="large" className="mr-2" />
                      )}
                      {shownBytes(one.max_bytes)}
                    </TableCell>
                  </TableRow>
                  {opened && (
                    <TableRow className="hover:bg-transparent">
                      <TableCell colSpan={8} className="bg-muted/30 p-3">
                        <Cases
                          route={one.route}
                          method={one.method}
                          period={period}
                          bySize={sort === 'max_bytes'}
                        />
                      </TableCell>
                    </TableRow>
                  )}
                </Fragment>
              )
            })}
          </TableBody>
        </Table>
      )}
      {data && (
        <Pagination
          total={data.total}
          limit={data.limit}
          offset={data.offset}
          onChange={setOffset}
          unit="곳"
        />
      )}
    </section>
  )
}

/**
 * 물음의 모양을 읽을 말로 — 목록 · 통계면 타입 · 기준 · 조건(칸과 연산 — 값은 서버가 안 남긴다),
 * SPARQL 이면 범위와 질의문.
 */
export function shapeParts(shape: Record<string, unknown>): { label: string; value: string }[] {
  const out: { label: string; value: string }[] = []
  const text = (value: unknown) => (Array.isArray(value) ? value.join(', ') : String(value))
  const names: [string, string][] = [
    ['type', '타입'],
    ['group_by', '기준'],
    ['split_by', '세부 기준'],
    ['metric', '집계'],
    ['metric_field', '집계 칸'],
    ['grain', '기간 단위'],
    ['conditions', '조건'],
    ['status', '상태'],
    ['year', '해'],
  ]
  for (const [key, label] of names) {
    if (shape[key] !== undefined) out.push({ label, value: text(shape[key]) })
  }
  if (shape.q) out.push({ label: '검색어', value: '있음' })
  if (shape.under) out.push({ label: '트리', value: '가지 하나' })
  if ('query' in shape) {
    const types = Array.isArray(shape.types) ? shape.types : []
    out.push({ label: '범위', value: types.length ? types.join(', ') : '기본(기록 타입 뺌)' })
  } else if (shape.types !== undefined) {
    out.push({ label: '타입들', value: text(shape.types) })
  }
  return out
}

function CutQuestionsSection({ period }: { period: PerformancePeriod }) {
  const cut = useResource(
    () => api.get<CutQuestions>(`/server/performance/cut?period=${period}`),
    [period],
  )
  return (
    <section className="space-y-3">
      <h2 className="text-base font-semibold">끊긴 질문</h2>
      <p className="text-muted-foreground text-sm">
        시간 상한(<span className="font-mono">READ_QUERY_SECONDS</span>)에 걸리거나 SPARQL 이 너무
        커서 답을 못 받은 물음입니다. 같은 모양끼리 묶어 많이 끊긴 것부터 보입니다 — 자주 끊기는
        물음은 지표로 미리 세어 둘 후보입니다. 조건의 값과 검색어는 남기지 않습니다.
      </p>
      <ErrorNotice error={cut.error} />
      {cut.data && cut.data.items.length === 0 ? (
        <EmptyState
          title="이 기간에 끊긴 질문이 없습니다"
          hint="목록 · 통계 · SPARQL 이 모두 답을 냈다는 뜻입니다."
        />
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>까닭</TableHead>
              <TableHead>물음</TableHead>
              <TableHead className="text-right">번</TableHead>
              <TableHead className="text-right">사람</TableHead>
              <TableHead>마지막</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {(cut.data?.items ?? []).map((one) => (
              <TableRow key={`${one.kind}/${one.code}/${one.route}/${JSON.stringify(one.shape)}`}>
                <TableCell>
                  {one.kind_label}
                  <p className="text-muted-foreground font-mono text-xs">{one.code}</p>
                </TableCell>
                <TableCell className="max-w-xl">
                  <p className="font-mono text-xs">
                    {one.method} {one.route}
                  </p>
                  <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-2 text-xs">
                    {shapeParts(one.shape).map((part) => (
                      <Fragment key={part.label}>
                        <dt className="text-muted-foreground">{part.label}</dt>
                        <dd className="font-mono break-all">{part.value}</dd>
                      </Fragment>
                    ))}
                  </dl>
                  {typeof one.shape.query === 'string' && (
                    <pre className="bg-muted mt-1 max-h-32 overflow-auto rounded p-2 text-xs whitespace-pre-wrap">
                      {one.shape.query}
                    </pre>
                  )}
                </TableCell>
                <TableCell className="text-right tabular-nums">{one.count}</TableCell>
                <TableCell className="text-right tabular-nums">{one.users}</TableCell>
                <TableCell className="text-xs">
                  {shownDateTime(one.last_at)}
                  {one.last_request_id && (
                    <p className="text-muted-foreground font-mono">{one.last_request_id}</p>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      {cut.data && cut.data.total > cut.data.items.length && (
        <p className="text-muted-foreground text-xs">
          모양 {cut.data.total}가지 중 많이 끊긴 {cut.data.items.length}가지만 보입니다.
        </p>
      )}
    </section>
  )
}

function Jobs({ period }: { period: PerformancePeriod }) {
  const jobs = useResource(
    () => api.get<JobStats>(`/server/performance/jobs?period=${period}`),
    [period],
  )
  return (
    <section className="space-y-3">
      <h2 className="text-base font-semibold">작업 종류마다</h2>
      <p className="text-muted-foreground text-sm">
        그 기간에 들어온 작업입니다. 대기는 들어와 워커가 시작하기까지, 소요는 시작해 끝나기까지입니다.
        대기가 길면 워커가 모자라거나 앞의 작업이 깁니다 — 아직 대기인 작업은 「가장 오래 대기」 로
        따로 표시합니다.
      </p>
      <ErrorNotice error={jobs.error} />
      {jobs.data && jobs.data.items.length === 0 ? (
        <EmptyState
          title="이 기간에 들어온 작업이 없습니다"
          hint="작업은 일괄 입력 · 내보내기 · 동기화처럼 오래 걸리는 일입니다. 기간을 넓혀 보세요."
        />
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>종류</TableHead>
              <TableHead className="text-right">완료</TableHead>
              <TableHead className="text-right">실패</TableHead>
              <TableHead className="text-right">대기 · 실행 중</TableHead>
              <TableHead className="text-right">소요 p50</TableHead>
              <TableHead className="text-right">소요 p95</TableHead>
              <TableHead className="text-right">소요 최대</TableHead>
              <TableHead className="text-right">대기 p50</TableHead>
              <TableHead className="text-right">대기 p95</TableHead>
              <TableHead className="text-right">대기 최대</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {(jobs.data?.items ?? []).map((one) => (
              <TableRow key={one.kind}>
                <TableCell>
                  {one.label}
                  <p className="text-muted-foreground font-mono text-xs">{one.kind}</p>
                </TableCell>
                <TableCell className="text-right tabular-nums">{one.done}</TableCell>
                <TableCell className="text-right tabular-nums">{one.failed}</TableCell>
                <TableCell className="text-right tabular-nums">
                  {one.open}
                  {one.queued_oldest_s !== null && (
                    <p className="text-muted-foreground text-xs">
                      가장 오래 대기 {shownSeconds(one.queued_oldest_s)}
                    </p>
                  )}
                </TableCell>
                <TableCell className="text-right tabular-nums">{shownSeconds(one.run_p50_s)}</TableCell>
                <TableCell className="text-right tabular-nums">{shownSeconds(one.run_p95_s)}</TableCell>
                <TableCell className="text-right tabular-nums">{shownSeconds(one.run_max_s)}</TableCell>
                <TableCell className="text-right tabular-nums">
                  {shownSeconds(one.wait_p50_s)}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {shownSeconds(one.wait_p95_s)}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {shownSeconds(one.wait_max_s)}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </section>
  )
}

export function Performance({
  initialPeriod = '24h',
  initialSort = 'p95',
}: {
  initialPeriod?: PerformancePeriod
  initialSort?: RequestSort
}) {
  const [period, setPeriod] = useState<PerformancePeriod>(initialPeriod)
  const [sort, setSort] = useState<RequestSort>(initialSort)

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-muted-foreground text-sm">
          요청마다 응답의 마지막 바이트를 보낼 때까지 걸린 시간과 보낸 크기입니다. 기록은 보관
          기간(<span className="font-mono">LOG_RETENTION_DAYS</span>)이 지나면 정리됩니다.
        </p>
        <Choice label="기간" options={PERIODS} value={period} onChange={setPeriod} />
      </div>
      {/* 기간 · 정렬이 바뀌면 첫 쪽 · 닫힌 줄부터 — 새로 세운다(`key`). */}
      <Requests key={`${period}/${sort}`} period={period} sort={sort} onSort={setSort} />
      <CutQuestionsSection period={period} />
      <Jobs period={period} />
    </div>
  )
}

/** 주소의 값(`?period=` · `?sort=`)을 고를 수 있는 것으로 — 모르는 값이면 기본. */
export function periodOf(raw: string | null): PerformancePeriod {
  return PERIODS.some((one) => one.value === raw) ? (raw as PerformancePeriod) : '24h'
}

export function sortOf(raw: string | null): RequestSort {
  return SORTS.some((one) => one.value === raw) ? (raw as RequestSort) : 'p95'
}
