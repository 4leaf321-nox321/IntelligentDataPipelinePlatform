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

type OnTrace = (typeSlug: string, key: string) => void

/** 본보기의 식별자 하나 — 「값의 출처」 로 그 값을 조회하는 단추(없으면 글자만). */
function KeyChip({ slug, value, onTrace }: { slug: string; value: string; onTrace?: OnTrace }) {
  if (!onTrace) return <span className="font-mono">{value}</span>
  return (
    <button
      type="button"
      className="font-mono underline-offset-2 hover:underline"
      aria-label={`${slug} ${value} 출처 조회`}
      onClick={() => onTrace(slug, value)}
    >
      {value}
    </button>
  )
}

/** 한 갈래의 본보기 — 참 건수가 더 크면 앞의 몇 건만이라고 적는다. */
function Samples({
  slug,
  type,
  kind,
  onTrace,
}: {
  slug: string
  type: ReconcileType
  kind: DriftKind
  onTrace?: OnTrace
}) {
  const total = driftCount(type, kind)
  if (!total) return null
  const shown = driftShown(type, kind)
  return (
    <div className="space-y-1">
      <p className="text-xs font-medium">
        {DRIFT_LABEL[kind]} {total}건
        {shown < total && (
          <span className="text-muted-foreground ml-1 font-normal">(앞의 {shown}건만 표시)</span>
        )}
      </p>
      {kind === 'changed' ? (
        <ul className="space-y-0.5 text-xs">
          {Object.entries(type.changed ?? {}).map(([key, fields]) => (
            <li key={key}>
              <KeyChip slug={slug} value={key} onTrace={onTrace} />
              <span className="text-muted-foreground">: {fields.join(' · ')}</span>
            </li>
          ))}
        </ul>
      ) : (
        <ul className="flex flex-wrap gap-1">
          {(type[kind] ?? []).map((key) => (
            <li key={key} className="bg-background rounded border px-1 text-xs">
              <KeyChip slug={slug} value={key} onTrace={onTrace} />
            </li>
          ))}
        </ul>
      )}
      {kind === 'extra' && !type.managed && (
        <p className="text-muted-foreground text-xs">
          이 원천이 관리하지 않는 타입이므로 대상에만 존재하는 객체는 사람이 생성한 객체일 수 있습니다.
          보정 계획에 포함하지 않으며 사람이 판단합니다.
        </p>
      )}
    </div>
  )
}

function TypeTable({ report, onTrace }: { report: ReconcileReport; onTrace?: OnTrace }) {
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
            <TableHead className="text-right">미확인 참조</TableHead>
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
                        aria-label={`${slug} 항목 ${opened.has(slug) ? '숨김' : '표시'}`}
                        onClick={() => toggle(slug)}
                      >
                        {opened.has(slug) ? '항목 숨김' : '항목 표시'}
                      </Button>
                    )}
                  </TableCell>
                </TableRow>
                {drifted && opened.has(slug) && (
                  <TableRow className="hover:bg-transparent">
                    <TableCell colSpan={DRIFT_KINDS.length + 4} className="bg-muted/30 whitespace-normal">
                      <div className="space-y-2">
                        {DRIFT_KINDS.map((kind) => (
                          <Samples key={kind} slug={slug} type={one} kind={kind} onTrace={onTrace} />
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
        누락은 기준 출력에는 있으나 대상에 없는 객체, 사용 중지는 기준 출력에서는 사용 중이나 대상에서
        사용 중지된 객체, 값 불일치는 속성 값이 기준 출력과 다른 객체, 대상에만 존재는 대상에서는 사용
        중이나 기준 출력에 없는 객체입니다. 미확인 참조는 참조하는 객체를 대상 SP에서 확인할 수 없는
        참조 속성이며, 불일치로 집계하지 않습니다.
        {unmanagedExtra &&
          " '(사람이 판단)'은 이 원천이 관리하지 않는 타입이어서 대상에만 존재하는 객체를 보정 계획에 포함하지 않는다는 표시입니다."}
      </p>
    </div>
  )
}

/** 보정 계획을 지금 올릴 수 없는 까닭 — 없으면 빈 글. */
function planBlocker(detail: PipelineDetail, report: ReconcileReport): string {
  const pending = stateOf(detail).pending
  if (pending) {
    return pending.origin === 'reconcile'
      ? '보정 계획이 이미 적용 대기 중입니다. 대상 SP에서 계획을 확인하고 적용하세요. 적용하면 즉시 다시 대조합니다.'
      : '적용 대기 중인 계획이 있어 보정 계획을 생성할 수 없습니다. 덮어쓰면 검토 중인 계획이 사라집니다. 해당 계획을 적용한 후 다시 대조하세요.'
  }
  if ((report.problems ?? []).length > 0) {
    return '마지막 대조가 완료되지 않아 보정 계획을 생성할 수 없습니다. 원인을 확인한 후 다시 대조하세요.'
  }
  if (fixableCount(report) === 0) {
    return "불일치가 이 원천이 관리하지 않는 타입의 '대상에만 존재' 항목뿐이라 보정 계획에 포함할 항목이 없습니다. 사람이 판단합니다."
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
    sum.missing > 0 && `누락 ${sum.missing}건: 기준 출력대로 다시 추가`,
    sum.deprecated > 0 && `사용 중지 ${sum.deprecated}건: 다시 '사용'으로 표시`,
    sum.changed > 0 && `값 불일치 ${sum.changed}건: 기준 출력 값으로 수정`,
    sum.managedExtra > 0 && `대상에만 존재 ${sum.managedExtra}건: 사용 중지(이 원천이 관리하는 타입)`,
    sum.otherExtra > 0 &&
      `대상에만 존재 ${sum.otherExtra}건: 포함하지 않음(이 원천이 관리하지 않는 타입, 사람이 판단)`,
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
            보정 계획 생성
          </Button>
        )}
      </div>
      <p className="text-muted-foreground text-xs">
        대조는 대상 SP의 객체를 조회만 합니다. 대상에 아무것도 전송하지 않습니다.
        {!baseline && ' 적용 이력이 없어 비교할 기준 출력이 없습니다. 첫 적용 후부터 대조할 수 있습니다.'}
      </p>
      {offerPlan && (
        <p className={cn('text-xs', blocker ? 'text-amber-700 dark:text-amber-400' : 'text-muted-foreground')}>
          {blocker ||
            '보정 계획은 불일치 객체를 기준 출력대로 다시 전송하는 미리 보기입니다. 대상 SP의 검토함에 표시되며, 적용은 사람이 합니다.'}
        </p>
      )}
      <ErrorNotice error={failed} />
      {queued && <QueuedNotice queued={queued} />}

      {report && (
        <ConfirmDialog
          open={asking}
          title="보정 계획 생성"
          description={
            <div className="space-y-2">
              <p>
                대상과 다시 대조한 후, 불일치 객체를 기준 출력(마지막으로 적용된 출력)대로 다시 전송하는
                계획을 대상 SP의 검토함에 미리 보기로 등록합니다. 등록만으로는 대상이 변경되지 않습니다.
                계획의 확인과 적용은 사람이 합니다.
              </p>
              <PlanLines report={report} />
              <p className="text-muted-foreground">
                마지막 대조({shownDateTime(report.at)}) 기준 건수입니다. 생성 시 다시 비교하므로 달라질 수
                있습니다. 다시 전송하는 객체와 연결된 기준 출력의 관계도 함께 포함합니다. 적용하면 즉시 다시
                대조합니다.
              </p>
            </div>
          }
          confirmLabel="생성"
          onConfirm={plan}
          onClose={() => setAsking(false)}
        />
      )}
    </div>
  )
}

/** 대조가 대상을 읽은 길(`Report.via`) — 코어가 가장 빠르고, 목록은 대상이 클수록 느려진다. */
const VIA_LABEL: Record<string, string> = {
  코어: '코어 API(커서, 2,000개 단위)',
  내보내기: '번들 내보내기(1회)',
  목록: '목록 API(페이지당 200개, 느림)',
}

export function ReconcileSection({
  detail,
  admin,
  onQueued,
  onTrace,
}: {
  detail: PipelineDetail
  admin: boolean
  /** 대조 · 보정 계획 작업을 넣었다 — 상세를 다시 읽는다. */
  onQueued: () => void
  /** 본보기의 식별자를 클릭했다 — 「값의 출처」 로 그 값을 조회한다. */
  onTrace?: OnTrace
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
        대상 SP의 현재 객체를 기준 출력(마지막으로 적용된 출력)과 비교합니다. 대상에서 객체를 삭제 또는
        사용 중지하거나 백업에서 복원한 경우 일반 실행으로는 감지되지 않습니다. 불일치 객체는 보정 계획으로
        기준 출력대로 다시 전송합니다.
      </p>

      <dl className="space-y-1.5">
        <Fact label="대조 주기">
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
            {shown.via && <Fact label="대상 조회 방식">{VIA_LABEL[shown.via] ?? shown.via}</Fact>}
          </>
        )}
      </dl>

      {admin && !detail.draft && (
        <ReconcileActions detail={detail} report={shown} onQueued={onQueued} />
      )}
      {!admin && (
        <p className="text-muted-foreground text-sm">
          [대조], [보정 계획 생성]은 시스템 관리자만 할 수 있습니다.
        </p>
      )}

      {!shown ? (
        <EmptyState
          title="대조 이력 없음"
          hint={
            detail.draft
              ? '초안은 실행되지 않으므로 비교할 기준 출력이 없습니다. 정의 버전을 사용하고 첫 적용을 완료한 후부터 대조합니다.'
              : schedule === '수동'
                ? admin
                  ? '[대조]를 클릭하면 대상 SP의 객체를 조회해 기준 출력과 비교합니다. 대상에는 아무것도 전송하지 않습니다.'
                  : '시스템 관리자가 대조하면 여기에 결과가 표시됩니다.'
                : `대조 주기(${schedule})에 따라 첫 적용 후 해당 시각에 대조합니다.${
                    admin ? ' [대조]로 즉시 비교할 수도 있습니다.' : ''
                  }`
          }
        />
      ) : (
        <>
          {(shown.problems ?? []).length > 0 && (
            <Alert className="border-amber-500/40 bg-amber-500/5">
              <AlertTitle>대조할 수 없는 항목이 있습니다</AlertTitle>
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
                유형마다 앞의 일부 항목만 표시합니다. 건수는 전체 건수입니다.
              </span>
              <Button size="xs" variant="outline" onClick={() => void loadFull()} disabled={loading}>
                {loading && <Loader2 className="animate-spin" />}
                전체 보고서 조회
              </Button>
            </div>
          )}
          {shown === full && (
            <p className="text-muted-foreground text-xs">전체 보고서 표시 중</p>
          )}
          <ErrorNotice error={failed} />
          <TypeTable report={shown} onTrace={onTrace} />
        </>
      )}
    </section>
  )
}
