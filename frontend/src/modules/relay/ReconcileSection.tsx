/**
 * 대조 — 대상 SP 에 **지금 실제로 있는 것**을 지난번 적용된 출력(기준)과 견준 마지막 보고서(개발계획 4c).
 *
 * relay 는 평소 자기 출력끼리만 견준다. 그래서 허브에서 누가 객체를 지우거나 사용 중지하거나 허브를
 * 백업에서 되돌리면 모른 채 어긋나서 돈다 — 대조가 그것을 타입마다 넷으로 센다(빠짐 · 안 씀 · 다름 ·
 * 대상에만). 확인 못 한 참조 칸은 어긋남으로 세지 않고 따로 적는다.
 *
 * 상세는 갈래마다 본보기 20개만 싣는다. 참 건수(`counts`)가 더 크면 그렇다고 적고 「전체 보고서」 를
 * 권한다 — 말 안 하면 사람은 본보기를 전부로 읽는다.
 *
 * 「대조」 · 「보정 계획 올리기」 는 시스템 관리자만(서버도 그렇게 거절한다). 보정 계획은 어긋난 줄을
 * 기준 그대로 다시 보내는 **미리 보기**라 평소 계획처럼 대상의 검토함에 뜨고 적용은 사람이 한다.
 * 적용을 기다리는 계획이 있으면 올리지 않는다 — 덮으면 사람이 보던 계획이 사라진다(서버도 그렇게 멈춘다).
 */

import { Fragment, useState } from 'react'
import { FileUp, GitCompare, Loader2 } from 'lucide-react'

import {
  DRIFT_KINDS,
  DRIFT_LABEL,
  driftCount,
  driftShown,
  fixableCount,
  reconcileOf,
  reconcileStatus,
  relayApi,
  reportTruncated,
  stateOf,
} from '@/modules/relay/api'
import type {
  DriftKind,
  PipelineDetail,
  QueuedOut,
  ReconcileReport,
  ReconcileType,
} from '@/modules/relay/api'
import { QueuedNotice } from '@/modules/relay/PipelineActions'
import { Fact } from '@/modules/relay/PipelineRuns'
import { ConfirmDialog } from '@/shared/components/ConfirmDialog'
import { EmptyState } from '@/shared/components/EmptyState'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { StatusBadge } from '@/shared/components/StatusBadge'
import { Alert, AlertDescription, AlertTitle } from '@/shared/components/ui/alert'
import { Button } from '@/shared/components/ui/button'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/shared/components/ui/table'
import { shownDateTime } from '@/shared/lib/datetime'
import { cn } from '@/shared/lib/utils'

const fail = (caught: unknown) => (caught instanceof Error ? caught : new Error('알 수 없는 오류'))

/** 건수 칸 — 0 은 흐리게(어긋남이 없는 칸이 눈을 끌지 않게). */
function Count({ value }: { value: number }) {
  return <span className={cn(value ? 'font-medium' : 'text-muted-foreground')}>{value}</span>
}

/** 한 갈래의 본보기 — 참 건수가 더 크면 앞의 몇 건만이라고 적는다. */
function Samples({ type, kind }: { type: ReconcileType; kind: DriftKind }) {
  const total = driftCount(type, kind)
  if (!total) return null
  const shown = driftShown(type, kind)
  return (
    <div className="space-y-1">
      <p className="text-xs font-medium">
        {DRIFT_LABEL[kind]} {total}건
        {shown < total && (
          <span className="text-muted-foreground ml-1 font-normal">— 앞의 {shown}건만 표시</span>
        )}
      </p>
      {kind === 'changed' ? (
        <ul className="space-y-0.5 text-xs">
          {Object.entries(type.changed ?? {}).map(([key, fields]) => (
            <li key={key}>
              <span className="font-mono">{key}</span>
              <span className="text-muted-foreground"> — {fields.join(' · ')}</span>
            </li>
          ))}
        </ul>
      ) : (
        <ul className="flex flex-wrap gap-1">
          {(type[kind] ?? []).map((key) => (
            <li key={key} className="bg-background rounded border px-1 font-mono text-xs">
              {key}
            </li>
          ))}
        </ul>
      )}
      {kind === 'extra' && !type.managed && (
        <p className="text-muted-foreground text-xs">
          이 원천이 관리하지 않는 타입이라 대상에만 있는 객체는 사람이 만든 것일 수 있습니다 — 보정
          계획에 포함하지 않으며 사람이 판단합니다.
        </p>
      )}
    </div>
  )
}

function TypeTable({ report }: { report: ReconcileReport }) {
  const [opened, setOpened] = useState<Set<string>>(new Set())
  const types = Object.entries(report.types ?? {})
  if (types.length === 0) return null
  const toggle = (slug: string) =>
    setOpened((was) => {
      const next = new Set(was)
      if (next.has(slug)) next.delete(slug)
      else next.add(slug)
      return next
    })
  const unmanagedExtra = types.some(([, one]) => !one.managed && driftCount(one, 'extra') > 0)
  return (
    <div className="space-y-1">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>타입</TableHead>
            <TableHead className="text-right">기준 객체</TableHead>
            {DRIFT_KINDS.map((kind) => (
              <TableHead key={kind} className="text-right">
                {DRIFT_LABEL[kind]}
              </TableHead>
            ))}
            <TableHead className="text-right">확인 못 한 참조</TableHead>
            <TableHead />
          </TableRow>
        </TableHeader>
        <TableBody>
          {types.map(([slug, one]) => {
            const drifted = DRIFT_KINDS.some((kind) => driftCount(one, kind) > 0)
            const extra = driftCount(one, 'extra')
            return (
              <Fragment key={slug}>
                <TableRow data-testid={`reconcile-type-${slug}`}>
                  <TableCell>
                    <span className="font-mono">{slug}</span>
                    {one.absent && (
                      <p className="text-destructive text-xs">대상에 없는 타입</p>
                    )}
                  </TableCell>
                  <TableCell className="text-right">{one.rows}</TableCell>
                  {DRIFT_KINDS.map((kind) => (
                    <TableCell key={kind} className="text-right">
                      <Count value={driftCount(one, kind)} />
                      {kind === 'extra' && extra > 0 && !one.managed && (
                        <span className="text-muted-foreground ml-1 text-xs">(사람이 판단)</span>
                      )}
                    </TableCell>
                  ))}
                  <TableCell className="text-right">
                    <span className="text-muted-foreground">{one.unverified}</span>
                  </TableCell>
                  <TableCell className="text-right">
                    {drifted && (
                      <Button
                        size="xs"
                        variant="ghost"
                        aria-label={`${slug} 본보기 ${opened.has(slug) ? '접기' : '펼치기'}`}
                        onClick={() => toggle(slug)}
                      >
                        {opened.has(slug) ? '본보기 접기' : '본보기 펼치기'}
                      </Button>
                    )}
                  </TableCell>
                </TableRow>
                {drifted && opened.has(slug) && (
                  <TableRow className="hover:bg-transparent">
                    <TableCell colSpan={DRIFT_KINDS.length + 4} className="bg-muted/30 whitespace-normal">
                      <div className="space-y-2">
                        {DRIFT_KINDS.map((kind) => (
                          <Samples key={kind} type={one} kind={kind} />
                        ))}
                      </div>
                    </TableCell>
                  </TableRow>
                )}
              </Fragment>
            )
          })}
        </TableBody>
      </Table>
      <p className="text-muted-foreground text-xs">
        빠짐 — 기준에는 있는데 대상에 없음 · 안 씀 — 기준에서는 사용인데 대상에서 사용 중지 · 다름 —
        칸 값이 기준과 다름 · 대상에만 — 대상에는 사용인데 기준에 없음. 확인 못 한 참조는 상대 객체를
        이 대조에서 확인하지 못한 참조 칸이며 어긋남에 포함하지 않습니다.
        {unmanagedExtra &&
          ' (사람이 판단) — 이 원천이 관리하지 않는 타입이라 대상에만 있는 객체는 보정 계획에 포함하지 않습니다.'}
      </p>
    </div>
  )
}

/** 보정 계획을 지금 올릴 수 없는 까닭 — 없으면 빈 글. */
function planBlocker(detail: PipelineDetail, report: ReconcileReport): string {
  const pending = stateOf(detail).pending
  if (pending) {
    return pending.origin === 'reconcile'
      ? '보정 계획이 이미 적용을 기다리고 있습니다 — 대상 SP 에서 계획을 확인하고 적용하세요. 적용하면 바로 다시 대조합니다.'
      : '적용을 기다리는 계획이 있어 보정 계획을 올릴 수 없습니다 — 덮으면 사람이 보던 계획이 사라집니다. 그 계획을 적용한 뒤 다시 대조하세요.'
  }
  if ((report.problems ?? []).length > 0) {
    return '마지막 대조를 마치지 못해 보정 계획을 올릴 수 없습니다 — 원인을 확인한 뒤 다시 대조하세요.'
  }
  if (fixableCount(report) === 0) {
    return '어긋남이 이 원천이 관리하지 않는 타입의 「대상에만」 뿐이라 보정 계획에 실을 것이 없습니다 — 사람이 판단합니다.'
  }
  return ''
}

/** 확인 창의 목록 — 갈래마다 보정 계획이 무엇을 하나. 마지막 대조의 수다. */
function PlanLines({ report }: { report: ReconcileReport }) {
  const sum = { missing: 0, deprecated: 0, changed: 0, managedExtra: 0, otherExtra: 0 }
  for (const one of Object.values(report.types ?? {})) {
    sum.missing += driftCount(one, 'missing')
    sum.deprecated += driftCount(one, 'deprecated')
    sum.changed += driftCount(one, 'changed')
    if (one.managed) sum.managedExtra += driftCount(one, 'extra')
    else sum.otherExtra += driftCount(one, 'extra')
  }
  const lines = [
    sum.missing > 0 && `빠짐 ${sum.missing}건 — 기준 그대로 다시 추가`,
    sum.deprecated > 0 && `안 씀 ${sum.deprecated}건 — 다시 「사용」 으로 표시`,
    sum.changed > 0 && `다름 ${sum.changed}건 — 기준 값으로 수정`,
    sum.managedExtra > 0 && `대상에만 ${sum.managedExtra}건 — 사용 중지(이 원천이 관리하는 타입)`,
    sum.otherExtra > 0 &&
      `대상에만 ${sum.otherExtra}건 — 포함하지 않음(이 원천이 관리하지 않는 타입 — 사람이 판단)`,
  ].filter((one): one is string => Boolean(one))
  return (
    <ul className="list-disc pl-4">
      {lines.map((one) => (
        <li key={one}>{one}</li>
      ))}
    </ul>
  )
}

function ReconcileActions({
  detail,
  report,
  onQueued,
}: {
  detail: PipelineDetail
  report: ReconcileReport | null
  onQueued: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [asking, setAsking] = useState(false)
  const [queued, setQueued] = useState<(QueuedOut & { what: string }) | null>(null)
  const [failed, setFailed] = useState<Error | null>(null)
  const baseline = Boolean(stateOf(detail).applied_run || stateOf(detail).applied_at)
  const offerPlan = Boolean(report && report.drift > 0)
  const blocker = report && offerPlan ? planBlocker(detail, report) : ''

  async function compare() {
    setBusy(true)
    setFailed(null)
    setQueued(null)
    try {
      const made = await relayApi.reconcile(detail.slug, false)
      setQueued({ ...made, what: '대조' })
      onQueued()
    } catch (caught) {
      setFailed(fail(caught))
    } finally {
      setBusy(false)
    }
  }

  /** 확인 창이 부른다 — 실패하면 창이 오류를 든 채 남는다. */
  async function plan() {
    setFailed(null)
    setQueued(null)
    const made = await relayApi.reconcile(detail.slug, true)
    setQueued({ ...made, what: '보정 계획' })
    onQueued()
  }

  return (
    <div className="space-y-2 rounded-md border p-3">
      <div className="flex flex-wrap items-center gap-3">
        <Button size="sm" variant="outline" onClick={() => void compare()} disabled={busy}>
          {busy ? <Loader2 className="animate-spin" /> : <GitCompare />}
          대조
        </Button>
        {offerPlan && (
          <Button
            size="sm"
            onClick={() => setAsking(true)}
            disabled={busy || Boolean(blocker)}
          >
            <FileUp />
            보정 계획 올리기
          </Button>
        )}
      </div>
      <p className="text-muted-foreground text-xs">
        대조는 대상 SP 의 객체를 읽기만 합니다 — 대상에 아무것도 보내지 않습니다.
        {!baseline && ' 아직 적용한 것이 없어 견줄 기준이 없습니다 — 첫 적용 뒤부터 대조할 수 있습니다.'}
      </p>
      {offerPlan && (
        <p className={cn('text-xs', blocker ? 'text-amber-700 dark:text-amber-400' : 'text-muted-foreground')}>
          {blocker ||
            '보정 계획은 어긋난 객체를 기준 그대로 다시 보내는 미리 보기입니다 — 대상 SP 의 검토함에 표시되고, 적용은 사람이 합니다.'}
        </p>
      )}
      <ErrorNotice error={failed} />
      {queued && <QueuedNotice queued={queued} />}

      {report && (
        <ConfirmDialog
          open={asking}
          title="보정 계획을 올립니다"
          description={
            <div className="space-y-2">
              <p>
                대상과 다시 대조한 뒤, 어긋난 객체를 기준(지난번 적용된 출력) 그대로 다시 보내는 계획을
                대상 SP 의 검토함에 올립니다. 미리 보기이므로 올리는 것만으로는 대상이 바뀌지 않습니다
                — 계획을 확인하고 적용하는 것은 사람입니다.
              </p>
              <PlanLines report={report} />
              <p className="text-muted-foreground">
                마지막 대조({shownDateTime(report.at)})의 수입니다 — 올릴 때 다시 견주므로 달라질 수
                있습니다. 다시 보내는 객체에 닿는 기준의 관계도 함께 싣습니다. 적용하면 바로 다시
                대조합니다.
              </p>
            </div>
          }
          confirmLabel="올리기"
          onConfirm={plan}
          onClose={() => setAsking(false)}
        />
      )}
    </div>
  )
}

export function ReconcileSection({
  detail,
  admin,
  onQueued,
}: {
  detail: PipelineDetail
  admin: boolean
  /** 대조 · 보정 계획 작업을 넣었다 — 상세를 다시 읽는다. */
  onQueued: () => void
}) {
  const report = reconcileOf(detail)
  const [full, setFull] = useState<ReconcileReport | null>(null)
  const [loading, setLoading] = useState(false)
  const [failed, setFailed] = useState<Error | null>(null)
  // 불러온 전체 보고서는 상세의 것보다 새것이 아니면 버린다 — 그 사이 다시 대조했으면 상세가 새것이다.
  const shown = full && report && full.at >= report.at ? full : report
  const schedule = detail.reconcile_label || '수동'

  async function loadFull() {
    setLoading(true)
    setFailed(null)
    try {
      setFull(await relayApi.reconcileReport(detail.slug))
    } catch (caught) {
      setFailed(fail(caught))
    } finally {
      setLoading(false)
    }
  }

  return (
    <section className="space-y-3">
      <h2 className="text-base font-semibold">대조</h2>
      <p className="text-muted-foreground text-sm">
        대상 SP 에 지금 있는 객체를 지난번 적용된 출력(기준)과 견줍니다 — 대상에서 누가 객체를 삭제하거나
        사용 중지하거나 백업에서 되돌리면 평소 실행은 그것을 모릅니다. 어긋난 객체는 「보정 계획」 으로 기준
        그대로 다시 보냅니다.
      </p>

      <dl className="space-y-1.5">
        <Fact label="일정">
          <span className={cn(schedule === '수동' && 'text-muted-foreground')}>{schedule}</span>
        </Fact>
        {shown && (
          <>
            <Fact label="마지막 대조">
              <span className="flex flex-wrap items-center gap-2">
                {shownDateTime(shown.at)}
                <StatusBadge kind="relay_reconcile" value={reconcileStatus(shown)} />
              </span>
            </Fact>
            <Fact label="결과">{shown.summary || '—'}</Fact>
          </>
        )}
      </dl>

      {admin && !detail.draft && (
        <ReconcileActions detail={detail} report={shown} onQueued={onQueued} />
      )}
      {!admin && (
        <p className="text-muted-foreground text-sm">
          「대조」 · 「보정 계획 올리기」 는 시스템 관리자가 합니다.
        </p>
      )}

      {!shown ? (
        <EmptyState
          title="아직 대조한 적이 없습니다"
          hint={
            detail.draft
              ? '초안은 돌지 않아 견줄 기준이 없습니다 — 정의 판을 켜고 첫 적용을 마친 뒤부터 대조합니다.'
              : schedule === '수동'
                ? admin
                  ? '「대조」 를 클릭하면 대상 SP 의 객체를 읽어 기준과 견줍니다 — 대상에는 아무것도 보내지 않습니다.'
                  : '시스템 관리자가 대조하면 여기에 결과가 표시됩니다.'
                : `일정(${schedule})에 따라 첫 적용 뒤 그 시각에 대조합니다.${
                    admin ? ' 「대조」 로 지금 견줄 수도 있습니다.' : ''
                  }`
          }
        />
      ) : (
        <>
          {(shown.problems ?? []).length > 0 && (
            <Alert className="border-amber-500/40 bg-amber-500/5">
              <AlertTitle>대조하지 못한 것이 있습니다</AlertTitle>
              <AlertDescription>
                <ul className="list-disc pl-4">
                  {shown.problems.map((one) => (
                    <li key={one}>{one}</li>
                  ))}
                </ul>
              </AlertDescription>
            </Alert>
          )}
          {reportTruncated(shown) && (
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <span className="text-muted-foreground">
                갈래마다 앞의 본보기만 표시합니다 — 건수는 전부입니다.
              </span>
              <Button size="xs" variant="outline" onClick={() => void loadFull()} disabled={loading}>
                {loading && <Loader2 className="animate-spin" />}
                전체 보고서 불러오기
              </Button>
            </div>
          )}
          {shown === full && (
            <p className="text-muted-foreground text-xs">전체 보고서를 표시하고 있습니다.</p>
          )}
          <ErrorNotice error={failed} />
          <TypeTable report={shown} />
        </>
      )}
    </section>
  )
}
