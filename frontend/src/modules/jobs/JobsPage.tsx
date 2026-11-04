/**
 * 작업 — **뒤에서 도는 일이 어디까지 됐나.**
 *
 * 일괄 입력은 보낸 순간 여기 한 줄이 된다. 대화상자를 닫아도 일은 계속되고, 여기서
 * 진행률 · 실패 이유를 본다. 워커가 한 대도 살아 있지 않으면 **그것부터 말한다** — 안 그러면
 * 「대기」 가 영영 대기인 이유를 사람이 알 길이 없다.
 *
 * **계획을 여기서 보고 여기서 적용한다.** 대화상자를 닫아도 계속된다고 해 놓고 돌아와서 적용할
 * 자리가 없으면, 그 말은 절반만 참이다 — 5만 행을 올린 사람이 창을 닫았다는 이유로 처음부터
 * 다시 해야 한다.
 */

import { Fragment, useEffect, useMemo, useState } from 'react'
import { ChevronDown, ChevronRight, Download, Loader2 } from 'lucide-react'

import { jobsApi, isTerminal, STATUS_LABEL } from '@/modules/jobs/api'
import type { Job, JobBulkResult, JobStatus } from '@/modules/jobs/api'
import { reassignOf } from '@/modules/workspaces/api'
import { ReassignPlanView } from '@/modules/workspaces/ReassignPlanView'
import { ConfirmDialog } from '@/shared/components/ConfirmDialog'
import { EmptyState } from '@/shared/components/EmptyState'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { PageHeader } from '@/shared/components/PageHeader'
import { Pagination } from '@/shared/components/Pagination'
import { Alert, AlertDescription, AlertTitle } from '@/shared/components/ui/alert'
import { Badge } from '@/shared/components/ui/badge'
import { Button } from '@/shared/components/ui/button'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/shared/components/ui/table'
import { useAuth } from '@/shared/auth/AuthContext'
import { isSystemAdmin } from '@/shared/auth/roles'
import { useResource } from '@/shared/hooks/useResource'
import { shownDateTime } from '@/shared/lib/datetime'

const PER_PAGE = 50
/** 돌고 있는 것이 있을 때만 이 간격으로 다시 읽는다. */
const REFRESH_MS = 3000

const STATUS_VARIANT: Record<JobStatus, 'default' | 'secondary' | 'outline' | 'destructive'> = {
  queued: 'outline',
  running: 'default',
  done: 'secondary',
  failed: 'destructive',
  cancelled: 'outline',
}

function Progress({ job }: { job: Job }) {
  if (isTerminal(job)) return null
  const { stage, done, total } = job.progress
  if (job.status === 'queued') return <span className="text-muted-foreground">워커 대기</span>
  const pct = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : null
  return (
    <div className="min-w-32 space-y-1">
      <div className="text-muted-foreground flex items-center gap-1 text-xs">
        <Loader2 className="size-3 animate-spin" />
        {stage || '진행 중'}
        {pct !== null && ` ${done.toLocaleString()} / ${total.toLocaleString()}`}
      </div>
      {pct !== null && (
        <div className="bg-muted h-1.5 w-full overflow-hidden rounded">
          <div className="bg-primary h-full" style={{ width: `${pct}%` }} />
        </div>
      )}
    </div>
  )
}

function Outcome({ job }: { job: Job }) {
  if (job.error) return <span className="text-destructive text-xs">{job.error}</span>
  // **결과가 스스로 한 줄로 말하는 종류**(고아 첨부 정리 …) — 그 말을 그대로.
  const summary = (job.result as { summary?: unknown } | null)?.summary
  if (typeof summary === 'string') {
    return <span className="text-muted-foreground text-xs">{summary}</span>
  }
  const counts = (job.result as { counts?: Record<string, number>; applied?: boolean } | null)
    ?.counts
  if (!counts) return null
  const applied = (job.result as { applied?: boolean }).applied
  return (
    <span className="text-muted-foreground text-xs">
      {applied ? '적용 — ' : '계획 — '}
      새로 {counts.create ?? 0} · 고침 {counts.update ?? 0} · 그대로 {counts.unchanged ?? 0}
      {counts.error ? ` · 오류 ${counts.error}` : ''}
    </span>
  )
}

/**
 * 줄 목록을 뺀 요약인가 — 목록 API 는 결과의 줄 목록(`rows`)을 비우고 그 수만 싣는다
 * (`rows_omitted`). 5만 줄 계획이면 결과 하나가 17MB 라, 목록이 그것을 작업마다 싣던 때는 한 번에
 * 93MB 를 3초마다 받았다(2026-10-09). 펼칠 때 상세를 따로 읽는다.
 */
function omitsRows(value: unknown): boolean {
  if (Array.isArray(value)) return value.some(omitsRows)
  if (!value || typeof value !== 'object') return false
  const record = value as Record<string, unknown>
  if (typeof record.rows_omitted === 'number' && record.rows_omitted > 0) return true
  return Object.values(record).some((one) => typeof one === 'object' && omitsRows(one))
}

/** 결과에 줄 목록이 있나 — 있으면 모든 줄을 CSV 로 받을 수 있다. */
function hasRows(value: unknown): boolean {
  if (Array.isArray(value)) return value.some(hasRows)
  if (!value || typeof value !== 'object') return false
  const record = value as Record<string, unknown>
  if (Array.isArray(record.rows)) return true
  return Object.values(record).some((one) => typeof one === 'object' && hasRows(one))
}

/** 아직 적용 안 한 **깨끗한** 계획인가 — 그때만 「적용」 이 선다. **적용 단계가 있는 종류만**
 * (`two_step`) — 데이터 소스 동기화도 계획을 내지만 적용은 「동기화」 를 다시 누르는 것이라, 여기서
 * 「적용」 을 세우면 서버가 거절한다(「이 종류의 작업은 적용 단계가 없습니다」). */
function waitingForApply(job: Job, twoStep: ReadonlySet<string>): boolean {
  if (!twoStep.has(job.kind)) return false
  if (job.status !== 'done' || !job.result) return false
  // **이미 적용한 계획** — 계획의 `result.applied` 는 영영 거짓이라(적용은 새 작업이다) 서버가
  // 따로 알려 준다. 안 보면 적용하고 나서도 「적용」 이 남아 같은 것을 두 번 넣는다.
  if (job.applied_by) return false
  const result = job.result as { applied?: boolean; ok?: boolean }
  if (result.applied) return false
  // 부서 자료 이동 — 막히는 것이 없고 옮길 것이 있으면(`ok`).
  const reassign = reassignOf(job.result)
  if (reassign) return reassign.ok
  // 그 밖의 계획(파이프라인 실행 …) — 자체 판정(`ok`)을 쓴다.
  return result.ok === true
}

function Detail({
  job: listed,
  busy,
  canApply,
  twoStep,
  onApply,
  onDownload,
  onDownloadRows,
}: {
  job: Job
  busy: boolean
  /** 적용을 세우나 — `waitingForApply`. */
  canApply: boolean
  /** 적용 단계가 있는 종류인가 — 없으면 계획이어도 여기서 적용하지 않는다. */
  twoStep: boolean
  onApply: () => void
  onDownload: () => void
  onDownloadRows: () => void
}) {
  // 목록의 결과는 줄 목록을 뺀 요약이다 — 펼치면 상세(앞의 500줄, 오류 먼저)를 읽는다.
  const brief = omitsRows(listed.result)
  const full = useResource(
    () => (brief ? jobsApi.get(listed.id) : Promise.resolve(listed)),
    [listed.id, listed.status, brief],
  )
  const job = (brief ? full.data : null) ?? listed
  // 부서 자료 이동(통폐합) — 무엇이 몇 건 옮겨지나 · 무엇이 막히나. 창을 닫고 와도 여기서 적용한다.
  const reassign = reassignOf(job.result)
  return (
    <div className="bg-muted/30 space-y-3 px-4 py-3">
      {brief && full.loading && (
        <p className="text-muted-foreground flex items-center gap-1 text-xs">
          <Loader2 className="size-3 animate-spin" />
          계획을 읽는 중…
        </p>
      )}
      {brief && full.error && <ErrorNotice error={full.error} />}
      {reassign && <ReassignPlanView result={reassign} />}
      {!reassign && !job.error && (
        <p className="text-muted-foreground text-xs">
          {job.has_output ? '만든 파일을 받으세요.' : '남길 결과가 없는 작업입니다.'}
        </p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        {job.has_output && (
          <Button size="xs" variant="outline" onClick={onDownload} disabled={busy}>
            <Download className="mr-1 size-3" />
            파일 받기
          </Button>
        )}
        {hasRows(job.result) && (
          <Button size="xs" variant="outline" onClick={onDownloadRows} disabled={busy}>
            <Download className="mr-1 size-3" />
            모든 줄 받기(CSV)
          </Button>
        )}
        {canApply && (
          <>
            <Button size="xs" onClick={onApply} disabled={busy}>
              {busy && <Loader2 className="mr-1 size-3 animate-spin" />}
              적용
            </Button>
            <span className="text-muted-foreground text-xs">
              아직 아무것도 안 들어갔습니다 — 위 계획을 읽고 눌러 주세요.
            </span>
          </>
        )}
        {job.applied_by && (
          <span className="text-muted-foreground text-xs">
            적용했습니다 — 적용 작업 <span className="font-mono">{job.applied_by.slice(0, 8)}</span>
          </span>
        )}
        {job.status === 'done' &&
          twoStep &&
          !canApply &&
          !job.applied_by &&
          ((job.result as { applied?: boolean } | null)?.applied === false ||
            reassign?.applied === false) && (
            <span className="text-muted-foreground text-xs">
              {reassign
                ? '막히는 것이 있거나 이동할 것이 없어 적용할 수 없습니다 — 부서 관리의 「자료 이동」 에서 다시 미리 보세요.'
                : '오류가 있거나 바뀌는 것이 없어 적용할 수 없습니다 — 고쳐서 다시 올리세요.'}
            </span>
          )}
      </div>
    </div>
  )
}

/**
 * 주소의 `?job=<id>` — **그 계획을 펼친 채로** 연다.
 *
 * 정제 도구 키트가 미리 보기를 마치면 이 링크를 준다. 사람은 명령 창 대신 여기서 계획을 읽고
 * 「적용」 을 누른다 — 그 플랫폼의 화면이라 엉뚱한 곳에 넣을 일이 없다.
 */
function linkedJob(): string | null {
  if (typeof window === 'undefined') return null
  return new URLSearchParams(window.location.search).get('job')
}

export default function JobsPage() {
  const { user } = useAuth()
  /** 이 작업을 내가 멈추거나 적용할 수 있나 — 시킨 사람과 시스템 관리자만. 「내가 시킨 것만」
   * 을 끄면 같은 부서 동료의 작업이 보이는데, 거기에 단추를 세우면 눌러 보고 거절당했다
   * (2026-10-08). */
  const ownedByMe = (job: Job) =>
    isSystemAdmin(user) || (job.requested_by_id != null && job.requested_by_id === user?.id)
  const [offset, setOffset] = useState(0)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [linked] = useState(linkedJob)
  const [opened, setOpened] = useState<string | null>(linked)
  // **하나씩 펼쳐 누르는 길만 있으면 스무 건에서 아무도 끝까지 안 한다.**
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [confirming, setConfirming] = useState<'apply' | 'cancel' | null>(null)
  const [bulkBusy, setBulkBusy] = useState(false)
  const [results, setResults] = useState<JobBulkResult[] | null>(null)
  const [actionError, setActionError] = useState<Error | null>(null)
  const [kind, setKind] = useState('')
  const [status, setStatus] = useState('')
  /** **기본은 내 것만.** 타이머가 넣은 줄이 대부분이라, 전부 보이면 제 작업을 못 찾는다. */
  const [mine, setMine] = useState(true)
  const page = useResource(
    () =>
      jobsApi.list({
        limit: PER_PAGE,
        offset,
        kind: kind || undefined,
        status: status || undefined,
        mine,
      }),
    [offset, kind, status, mine],
  )
  const kinds = useResource(() => jobsApi.kinds(), [])
  const twoStep = useMemo(
    () => new Set((kinds.data ?? []).filter((one) => one.two_step).map((one) => one.name)),
    [kinds.data],
  )
  const workers = useResource(() => jobsApi.workers(), [])

  // 돌고 있는 것이 있으면 다시 읽는다 — 끝난 목록을 3초마다 두드릴 이유는 없다.
  const active = (page.data?.items ?? []).some((one) => !isTerminal(one))
  const reload = page.reload
  useEffect(() => {
    if (!active) return
    const timer = setInterval(reload, REFRESH_MS)
    return () => clearInterval(timer)
  }, [active, reload])

  const alive = (workers.data ?? []).filter((one) => one.alive)

  /** 누르는 일 하나 — 실패하면 **그 이유를 화면에 남긴다**(조용히 삼키면 눌러도 아무 일이 없다). */
  const act = async (job: Job, run: () => Promise<unknown>) => {
    setBusyId(job.id)
    setActionError(null)
    try {
      await run()
      reload()
    } catch (caught) {
      setActionError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBusyId(null)
    }
  }

  /** 목록에서 고른 것들 — **한 번에 적용하거나 한 번에 취소한다.** */
  const chosen = (page.data?.items ?? []).filter((one) => picked.has(one.id))
  const applicable = chosen.filter((one) => ownedByMe(one) && waitingForApply(one, twoStep))
  const cancellable = chosen.filter(
    (one) => ownedByMe(one) && !isTerminal(one) && !one.cancel_requested,
  )

  async function runMany(what: 'apply' | 'cancel') {
    setActionError(null)
    setBulkBusy(true)
    try {
      const ids = (what === 'apply' ? applicable : cancellable).map((one) => one.id)
      const rows = what === 'apply' ? await jobsApi.applyMany(ids) : await jobsApi.cancelMany(ids)
      setResults(rows)
      setPicked(new Set())
      page.reload()
    } catch (caught) {
      setActionError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBulkBusy(false)
      setConfirming(null)
    }
  }

  const cancel = (job: Job) => act(job, () => jobsApi.cancel(job.id))
  /** 계획을 읽고 누르는 자리 — 같은 파일 · 같은 지문으로 적용 작업이 새로 생긴다. */
  const apply = (job: Job) =>
    act(job, async () => {
      const made = await jobsApi.apply(job.id)
      setOpened(made.id)
    })
  const download = (job: Job) =>
    act(job, () => jobsApi.download(job.id, job.input_file_name ?? `${job.kind}.csv`))

  return (
    <div className="space-y-6">
      <PageHeader
        title="작업"
        description="일괄 입력처럼 뒤에서 도는 일. 대화상자를 닫아도 일은 계속되고, 여기서 진행과 결과를 봅니다."
      />

      {workers.data && alive.length === 0 && (
        <Alert variant="destructive">
          <AlertTitle>워커가 살아 있지 않습니다</AlertTitle>
          <AlertDescription>
            작업은 워커가 집어야 돕니다. 지금은 넣어도 「대기」 에 머뭅니다 — 운영자에게{' '}
            <span className="font-mono">systemctl status &lt;slug&gt;-worker</span> 를 확인해 달라고
            하세요.
          </AlertDescription>
        </Alert>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <select
          aria-label="종류"
          className="border-input bg-background h-8 rounded-md border px-2 text-sm"
          value={kind}
          onChange={(event) => {
            setKind(event.target.value)
            setOffset(0)
          }}
        >
          <option value="">종류 전부</option>
          {(kinds.data ?? []).map((one) => (
            <option key={one.name} value={one.name}>
              {one.label}
            </option>
          ))}
        </select>
        <select
          aria-label="상태"
          className="border-input bg-background h-8 rounded-md border px-2 text-sm"
          value={status}
          onChange={(event) => {
            setStatus(event.target.value)
            setOffset(0)
          }}
        >
          <option value="">상태 전부</option>
          {(Object.keys(STATUS_LABEL) as JobStatus[]).map((one) => (
            <option key={one} value={one}>
              {STATUS_LABEL[one]}
            </option>
          ))}
        </select>
        <label className="flex items-center gap-1.5 text-sm">
          <input
            type="checkbox"
            checked={mine}
            onChange={(event) => {
              setMine(event.target.checked)
              setOffset(0)
            }}
          />
          내가 시킨 것만
        </label>
      </div>

      <ErrorNotice error={page.error} />
      <ErrorNotice error={actionError} />

      {/* 링크로 왔는데 그 작업이 목록에 없다 — **조용히 아무것도 안 열리면** 사람은 링크가 고장 난
          줄 안다. 계획을 본 사람만 적용할 수 있으니 대개 다른 계정으로 들어온 것이다. */}
      {linked && page.data && !page.data.items.some((one) => one.id === linked) && (
        <Alert>
          <AlertTitle>링크의 작업이 이 목록에 없습니다</AlertTitle>
          <AlertDescription>
            미리 보기를 한 사람(정제 도구에 등록한 토큰의 주인)으로 로그인했는지 확인하세요 —
            계획은 그 사람만 적용합니다. 「내가 시킨 것만」 을 끄면 다른 사람의 작업도 보입니다.
          </AlertDescription>
        </Alert>
      )}

      {picked.size > 0 && (
        <div className="flex flex-wrap items-center gap-2 rounded-md border p-2">
          <span className="text-sm">{picked.size}건 선택</span>
          <Button
            size="sm"
            disabled={bulkBusy || applicable.length === 0}
            onClick={() => setConfirming('apply')}
          >
            선택 적용 {applicable.length > 0 && `(${applicable.length})`}
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={bulkBusy || cancellable.length === 0}
            onClick={() => setConfirming('cancel')}
          >
            선택 취소 {cancellable.length > 0 && `(${cancellable.length})`}
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setPicked(new Set())}>
            선택 해제
          </Button>
          {/* **고른 것과 할 수 있는 것이 다를 수 있다.** 그 차이를 말해 주지 않으면
              사람은 「왜 다섯을 골랐는데 셋만 갔나」 를 묻게 된다. */}
          {applicable.length < picked.size && (
            <span className="text-muted-foreground text-xs">
              적용은 오류 없는 계획만 — {picked.size - applicable.length}건은 빠집니다
            </span>
          )}
        </div>
      )}

      <ConfirmDialog
        open={confirming !== null}
        title={
          confirming === 'apply'
            ? `계획 ${applicable.length}건을 적용합니다`
            : `작업 ${cancellable.length}건을 취소합니다`
        }
        description={
          confirming === 'apply' ? (
            <>
              고른 계획마다 <b>적용 작업</b>이 서고 워커가 차례로 넣습니다. 오류가 있는 계획과 이미
              적용한 것은 빠집니다 — 한 건이 막혀도 나머지는 갑니다. 넣은 뒤 되돌리려면 객체 화면의{' '}
              <b>일괄 되돌리기</b>를 씁니다.
            </>
          ) : (
            <>
              도는 중인 작업에 <b>멈춤을 부탁</b>합니다. 워커가 다음 묶음에서 보고 멈추므로,
              이미 넣은 행은 그대로 남습니다 — 끝난 작업은 빠집니다.
            </>
          )
        }
        confirmLabel={confirming === 'apply' ? '적용' : '취소 요청'}
        destructive={confirming === 'cancel'}
        onConfirm={() => runMany(confirming === 'apply' ? 'apply' : 'cancel')}
        onClose={() => setConfirming(null)}
      />

      {results && (
        <div className="space-y-1 rounded-md border p-3">
          <p className="text-sm font-semibold">
            결과 — 성공 {results.filter((one) => one.status === 'ok').length} · 오류{' '}
            {results.filter((one) => one.status === 'error').length}
          </p>
          {results
            .filter((one) => one.status === 'error')
            .map((one) => (
              <p key={one.id} className="text-destructive text-sm">
                {one.message}
              </p>
            ))}
          <Button size="xs" variant="ghost" onClick={() => setResults(null)}>
            닫기
          </Button>
        </div>
      )}

      {page.data && page.data.items.length === 0 ? (
        <EmptyState
          title="작업이 없습니다"
          hint={
            mine || kind || status
              ? '거르기를 풀면 다른 사람이 시킨 것과 타이머가 넣은 것도 보입니다.'
              : '파일을 가져오면 여기 한 줄이 생깁니다.'
          }
        />
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-8">
                <input
                  type="checkbox"
                  aria-label="이 쪽 전부 선택"
                  checked={
                    (page.data?.items ?? []).length > 0 &&
                    (page.data?.items ?? []).every((one) => picked.has(one.id))
                  }
                  onChange={(event) =>
                    setPicked(
                      event.target.checked
                        ? new Set((page.data?.items ?? []).map((one) => one.id))
                        : new Set(),
                    )
                  }
                />
              </TableHead>
              <TableHead className="w-8" />
              <TableHead>시각</TableHead>
              <TableHead>무엇</TableHead>
              <TableHead>상태</TableHead>
              <TableHead>진행 · 결과</TableHead>
              <TableHead>누가</TableHead>
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {(page.data?.items ?? []).map((one) => (
              <Fragment key={one.id}>
                <TableRow>
                  <TableCell className="pr-0">
                    <input
                      type="checkbox"
                      aria-label={`${one.kind_label} 선택`}
                      checked={picked.has(one.id)}
                      onChange={(event) =>
                        setPicked((was) => {
                          const next = new Set(was)
                          if (event.target.checked) next.add(one.id)
                          else next.delete(one.id)
                          return next
                        })
                      }
                    />
                  </TableCell>
                  <TableCell className="pr-0">
                    {/* **끝난 작업은 펼쳐 본다.** 계획을 여기서 읽고 여기서 적용한다. */}
                    <Button
                      size="xs"
                      variant="ghost"
                      aria-label={opened === one.id ? '접기' : '펼치기'}
                      disabled={!isTerminal(one)}
                      onClick={() => setOpened(opened === one.id ? null : one.id)}
                    >
                      {opened === one.id ? (
                        <ChevronDown className="size-3.5" />
                      ) : (
                        <ChevronRight className="size-3.5" />
                      )}
                    </Button>
                  </TableCell>
                  <TableCell className="whitespace-nowrap">
                    {shownDateTime(one.created_at)}
                  </TableCell>
                  <TableCell>
                    {one.kind_label}
                    {one.params.type_slug != null && (
                      <span className="text-muted-foreground ml-1 font-mono text-xs">
                        {String(one.params.type_slug)}
                      </span>
                    )}
                    {one.input_file_name && (
                      <p className="text-muted-foreground text-xs">{one.input_file_name}</p>
                    )}
                  </TableCell>
                  <TableCell>
                    <Badge variant={STATUS_VARIANT[one.status]}>{STATUS_LABEL[one.status]}</Badge>
                    {one.cancel_requested && !isTerminal(one) && (
                      <span className="text-muted-foreground ml-1 text-xs">취소 요청됨</span>
                    )}
                    {one.attempts > 1 && (
                      <span className="text-muted-foreground ml-1 text-xs">{one.attempts}번째</span>
                    )}
                  </TableCell>
                  <TableCell>
                    <Progress job={one} />
                    <Outcome job={one} />
                  </TableCell>
                  <TableCell className="text-xs">
                    {one.requested_by_name ?? '—'}
                    {one.workspace_slug && (
                      <p className="text-muted-foreground font-mono">{one.workspace_slug}</p>
                    )}
                  </TableCell>
                  <TableCell className="text-right">
                    {ownedByMe(one) && !isTerminal(one) && !one.cancel_requested && (
                      <Button
                        size="xs"
                        variant="outline"
                        disabled={busyId === one.id}
                        onClick={() => void cancel(one)}
                      >
                        취소
                      </Button>
                    )}
                    {ownedByMe(one) && waitingForApply(one, twoStep) && opened !== one.id && (
                      <Badge variant="outline">적용 대기</Badge>
                    )}
                  </TableCell>
                </TableRow>
                {opened === one.id && (
                  <TableRow>
                    <TableCell colSpan={8} className="p-0">
                      <Detail
                        job={one}
                        busy={busyId === one.id}
                        canApply={ownedByMe(one) && waitingForApply(one, twoStep)}
                        twoStep={twoStep.has(one.kind)}
                        onApply={() => void apply(one)}
                        onDownload={() => void download(one)}
                        onDownloadRows={() => void act(one, () => jobsApi.rowsCsv(one.id))}
                      />
                    </TableCell>
                  </TableRow>
                )}
              </Fragment>
            ))}
          </TableBody>
        </Table>
      )}

      {page.data && (
        <Pagination total={page.data.total} limit={PER_PAGE} offset={offset} onChange={setOffset} />
      )}
    </div>
  )
}
