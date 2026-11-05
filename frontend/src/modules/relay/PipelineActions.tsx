/**
 * 지금 실행 · 적용 — **시스템 관리자만**(서버도 그렇게 거절한다). 둘 다 작업으로 들어가고, 넣은 뒤
 * 작업 화면으로 가는 링크를 남긴다 — 「눌렀는데 아무 일이 없다」 로 읽히지 않게.
 *
 * 실행은 원천을 받아 레시피로 바꾸고 대상 SP 에 **계획(미리 보기)** 만 올린다. 대상에 실제로
 * 넣는 것은 「적용」 이다 — 계획을 읽은 사람이 누른다.
 *
 * 초안(정의 판이 아직 안 켜진 파이프라인)에는 단추를 세우지 않는다 — 서버가 실행을 거절하므로
 * 눌러 봐야 오류뿐이다. 무엇을 하면 서는지만 적는다.
 */

import { useState } from 'react'
import { Loader2, Play, Upload } from 'lucide-react'
import { Link } from 'react-router-dom'

import { jobLink, relayApi, stateOf } from '@/modules/relay/api'
import type { PipelineDetail, QueuedOut } from '@/modules/relay/api'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { Alert, AlertDescription, AlertTitle } from '@/shared/components/ui/alert'
import { Button } from '@/shared/components/ui/button'

/** 넣은 작업 — 작업 화면으로 가는 링크를 남긴다. 실행 · 적용 · 대조가 같은 꼴을 쓴다. */
export function QueuedNotice({ queued }: { queued: QueuedOut & { what: string } }) {
  return (
    <Alert>
      <AlertTitle>{queued.what} 작업을 등록했습니다</AlertTitle>
      <AlertDescription>
        워커가 집으면 돕니다.{' '}
        <Link to={jobLink(queued.job_id)}>작업 화면에서 진행 보기</Link>
      </AlertDescription>
    </Alert>
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
  const [busy, setBusy] = useState<'run' | 'apply' | null>(null)
  const [queued, setQueued] = useState<(QueuedOut & { what: string }) | null>(null)
  const [failed, setFailed] = useState<Error | null>(null)
  const pending = stateOf(detail).pending
  const canApply = Boolean(pending?.ok)

  if (detail.draft) {
    return (
      <section className="rounded-md border border-dashed p-3">
        <p className="text-muted-foreground text-sm">
          초안은 돌지 않습니다 — 아래 「정의 판」 에서 판을 시험하고 켜면 「지금 실행」 · 「적용」 이
          섭니다.
        </p>
      </section>
    )
  }

  async function act(what: 'run' | 'apply') {
    setBusy(what)
    setFailed(null)
    setQueued(null)
    try {
      const made =
        what === 'run'
          ? await relayApi.run(detail.slug, overwrite ? 'overwrite' : 'keep')
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
          사람이 고친 칸도 {detail.system || '원천'} 값으로 덮기(overwrite)
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
        실행은 대상 SP 에 계획만 올립니다 — 아무것도 안 들어갑니다.{' '}
        {canApply
          ? '대상 SP 에서 계획을 읽고 「적용」 을 누르면 들어갑니다.'
          : pending
            ? '지금 계획에는 오류가 있어 적용할 수 없습니다 — 고쳐서 다시 실행하세요.'
            : '적용을 기다리는 계획이 없습니다.'}
        {overwrite && ' 덮기를 켜면 대상에서 사람이 고친 칸도 원천 값으로 바뀝니다(계획에 적힙니다).'}
      </p>
      <ErrorNotice error={failed} />
      {queued && <QueuedNotice queued={queued} />}
    </section>
  )
}
