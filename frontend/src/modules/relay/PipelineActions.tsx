/**
 * 지금 실행 · 적용 — **시스템 관리자만**(서버도 그렇게 거절한다). 둘 다 작업으로 들어가고, 넣은 뒤
 * 작업 화면으로 가는 링크를 남긴다 — 「눌렀는데 아무 일이 없다」 로 읽히지 않게.
 *
 * 실행은 원천을 받아 레시피로 바꾸고 대상 SP 에 **계획(미리 보기)** 만 올린다. 대상에 실제로
 * 넣는 것은 「적용」 이다 — 계획을 읽은 사람이 누른다.
 *
 * 초안(정의 판이 아직 안 켜진 파이프라인)에는 단추를 세우지 않는다 — 서버가 실행을 거절하므로
 * 눌러 봐야 오류뿐이다. 무엇을 하면 서는지만 적는다.
 *
 * **받기 점검이 멈췄으면**(대량 삭제 · 빈 칸) 「확인 후 진행」 을 낸다 — 지금 멈춘 건수 · 칸까지만
 * 지나가는 실행이다. 원천 · 어댑터를 바로잡았으면 그냥 「지금 실행」 이 지나간다.
 */

import { useState } from 'react'
import { CheckCheck, Loader2, Play, Upload } from 'lucide-react'
import { Link } from 'react-router-dom'

import { jobLink, partsOf, relayApi, stateOf } from '@/modules/relay/api'
import type { HeldIntake, PipelineDetail, QueuedOut } from '@/modules/relay/api'
import { ConfirmDialog } from '@/shared/components/ConfirmDialog'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { Alert, AlertDescription, AlertTitle } from '@/shared/components/ui/alert'
import { Button } from '@/shared/components/ui/button'

/** 넣은 작업 — 작업 화면으로 가는 링크를 남긴다. 실행 · 적용 · 대조가 같은 꼴을 쓴다. */
export function QueuedNotice({ queued }: { queued: QueuedOut & { what: string } }) {
  return (
    <Alert>
      <AlertTitle>{queued.what} 작업을 등록했습니다</AlertTitle>
      <AlertDescription>
        워커가 가져가면 실행됩니다.{' '}
        <Link to={jobLink(queued.job_id)}>작업 화면에서 진행 보기</Link>
      </AlertDescription>
    </Alert>
  )
}

/** 「확인 후 진행」 이 무엇을 하나 — 숫자로. 대상에는 아직 아무것도 안 들어간다(계획만). */
function acceptText(held: HeldIntake) {
  const found = held.findings
  return (
    <div className="space-y-2">
      <ul className="list-disc space-y-1 pl-4">
        {found.deleted > 0 && (
          <li>
            미러 {found.before.toLocaleString()}건 중 <b>{found.deleted.toLocaleString()}건</b>이 삭제되고,
            다음 계획에 그만큼의 삭제(사용 중지)가 들어갑니다.
          </li>
        )}
        {found.blanks.map((one) => (
          <li key={one.column}>
            칸 <span className="font-mono">{one.column}</span> 의 값이 있던{' '}
            <b>{one.lost.toLocaleString()}건</b>이 비고, 다음 계획이 대상의 그 칸을 비웁니다.
          </li>
        ))}
      </ul>
      <p>
        확인한 건수 · 칸까지만 진행합니다 — 실행할 때 원천이 더 나빠졌으면 다시 멈춥니다. 대상에는 계획을
        읽고 「적용」 해야 들어갑니다. 원천이 일부만 준 것이면 진행하지 말고 원천 · 어댑터를 먼저 수정하세요.
      </p>
    </div>
  )
}

export function PipelineActions({
  detail,
  onQueued,
}: {
  detail: PipelineDetail
  /** 넣고 나면 상세를 다시 읽는다 — 이력 · 작업 표가 그 한 줄을 보이게. */
  onQueued: () => void
}) {
  const [overwrite, setOverwrite] = useState(false)
  const [full, setFull] = useState(false)
  const [busy, setBusy] = useState<'run' | 'apply' | null>(null)
  const [queued, setQueued] = useState<(QueuedOut & { what: string }) | null>(null)
  const [failed, setFailed] = useState<Error | null>(null)
  const [confirming, setConfirming] = useState(false)
  const held = stateOf(detail).held
  const pending = stateOf(detail).pending
  // 나눠 보내다 뒤 조각에서 멈춘 계획은 「적용」 이 그 조각부터 다시 미리 본다.
  const resumes = Boolean(pending && !pending.ok && (pending.part ?? 0) > 0)
  const canApply = Boolean(pending?.ok) || resumes

  if (detail.draft) {
    return (
      <section className="rounded-md border border-dashed p-3">
        <p className="text-muted-foreground text-sm">
          초안은 실행되지 않습니다 — 아래 「정의 판」 에서 판을 시험하고 사용하면 「지금 실행」 ·
          「적용」 이 표시됩니다.
        </p>
      </section>
    )
  }

  async function accept() {
    const made = await relayApi.run(detail.slug, overwrite ? 'overwrite' : 'keep', true)
    setQueued({ ...made, what: '확인 후 진행' })
    onQueued()
  }

  async function act(what: 'run' | 'apply') {
    setBusy(what)
    setFailed(null)
    setQueued(null)
    try {
      const made =
        what === 'run'
          ? full
            ? await relayApi.run(detail.slug, overwrite ? 'overwrite' : 'keep', false, true)
            : await relayApi.run(detail.slug, overwrite ? 'overwrite' : 'keep')
          : await relayApi.apply(detail.slug)
      setQueued({ ...made, what: what === 'run' ? '실행' : '적용' })
      onQueued()
    } catch (caught) {
      setFailed(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBusy(null)
    }
  }

  return (
    <section className="space-y-2 rounded-md border p-3">
      <div className="flex flex-wrap items-center gap-3">
        <Button size="sm" onClick={() => void act('run')} disabled={busy !== null}>
          {busy === 'run' ? <Loader2 className="animate-spin" /> : <Play />}
          지금 실행
        </Button>
        <label className="flex items-center gap-1.5 text-sm">
          <input
            type="checkbox"
            checked={overwrite}
            onChange={(event) => setOverwrite(event.target.checked)}
          />
          사람이 수정한 칸도 {detail.system || '원천'} 값으로 덮기(overwrite)
        </label>
        <label className="flex items-center gap-1.5 text-sm" title="어댑터의 칸을 수정한 뒤 이미 지나간 행까지 다시 읽습니다 — 안 온 행은 삭제로 읽히고, 받기 점검이 대량 삭제를 막습니다.">
          <input type="checkbox" checked={full} onChange={(event) => setFull(event.target.checked)} />
          전량 다시 받기
        </label>
        <Button
          size="sm"
          variant="outline"
          onClick={() => void act('apply')}
          disabled={busy !== null || !canApply}
        >
          {busy === 'apply' ? <Loader2 className="animate-spin" /> : <Upload />}
          적용
        </Button>
      </div>
      <p className="text-muted-foreground text-xs">
        실행은 대상 SP 에 계획만 생성합니다 — 아무것도 들어가지 않습니다.{' '}
        {resumes
          ? '앞 조각은 적용됐고 한 조각의 미리 보기에서 멈췄습니다 — 원인을 해결한 뒤 「적용」 하면 그 조각부터 다시 보냅니다.'
          : canApply
            ? pending && partsOf(pending) > 1
              ? `나눠 보내는 계획입니다(조각 ${partsOf(pending)}개) — 대상 SP 에서 첫 조각의 계획을 읽고 「적용」 을 클릭하면 조각마다 차례로 미리 보고 들어갑니다.`
              : '대상 SP 에서 계획을 읽고 「적용」 을 클릭하면 들어갑니다.'
            : pending
            ? '지금 계획에는 오류가 있어 적용할 수 없습니다 — 원인을 해결한 뒤 다시 실행하세요.'
            : '적용을 기다리는 계획이 없습니다.'}
        {overwrite && ' 덮기를 선택하면 대상에서 사람이 수정한 칸도 원천 값으로 바뀝니다(계획에 적힙니다).'}
      </p>
      {held && (
        <div className="space-y-2 rounded-md border border-amber-500/50 bg-amber-500/5 p-3 text-sm">
          <p className="font-medium">받기 점검에서 멈췄습니다 — 미러 · 워터마크에 반영하지 않았습니다</p>
          <ul className="list-disc space-y-0.5 pl-4 text-xs">
            {held.reasons.map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
          <div className="flex flex-wrap items-center gap-3">
            <Button size="sm" variant="outline" onClick={() => setConfirming(true)} disabled={busy !== null}>
              <CheckCheck />
              확인 후 진행
            </Button>
            <span className="text-muted-foreground text-xs">
              원천 · 어댑터를 수정했으면 「지금 실행」 을 클릭하세요 — 바로잡혔으면 점검을 지나갑니다.
            </span>
          </div>
        </div>
      )}
      <ErrorNotice error={failed} />
      {queued && <QueuedNotice queued={queued} />}
      {held && (
        <ConfirmDialog
          open={confirming}
          title="받기 점검의 멈춤을 확인하고 진행합니다"
          description={acceptText(held)}
          confirmLabel="확인 후 진행"
          destructive={held.findings.deleted > 0}
          onConfirm={accept}
          onClose={() => setConfirming(false)}
        />
      )}
    </section>
  )
}
