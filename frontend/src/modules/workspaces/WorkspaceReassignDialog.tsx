/**
 * 자료 이동 — **부서 통폐합의 앞 단계.**
 *
 * 없어지는 부서의 자료를 다른 부서로 통째 넘기고, 그다음에 원본을 보관하거나 지운다.
 * 이 길이 없으면 자료를 살리는 방법이 「객체를 하나씩 손으로 수정」 뿐이고, 그 일은
 * 아무도 끝내지 못한다 — 결국 쓰지 않는 부서가 목록에 영원히 남는다.
 *
 * **고른 종류만 옮긴다.** 멤버는 두고 객체만 넘기는 개편이 흔하다(팀은 남고 업무만
 * 넘어가는 경우).
 *
 * **작업이다**(2026-10-10) — 일괄 입력과 같은 무늬로 먼저 계획(무엇이 몇 건 · 무엇이 막히나)을
 * 보고 적용한다. 요청 안에서 옮기던 때는 한 부서 객체 20만 개면 90초 남짓이라 브라우저 · 이중화의
 * nginx 가 먼저 끊었다. 창을 닫아도 작업은 계속되고, 작업 화면에서 같은 계획을 읽고 적용한다.
 */

import { useEffect, useRef, useState } from 'react'
import { Loader2 } from 'lucide-react'

import { jobsApi, STUCK_MS } from '@/modules/jobs/api'
import type { Job } from '@/modules/jobs/api'
import { reassignOf, workspaceApi } from '@/modules/workspaces/api'
import type { ReassignResult, Workspace, WorkspaceContent } from '@/modules/workspaces/api'
import { ReassignPlanView } from '@/modules/workspaces/ReassignPlanView'
import { parentOptions } from '@/modules/workspaces/tree'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { SearchablePicker } from '@/shared/components/SearchablePicker'
import { Button } from '@/shared/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/shared/components/ui/dialog'
import { Label } from '@/shared/components/ui/label'

interface Props {
  source: Workspace
  all: Workspace[]
  onClose: () => void
  onDone: () => void
}

/** 창을 닫으면 기다림을 그만둔다 — 작업은 서버에서 계속된다. */
class Closed extends Error {}

export function WorkspaceReassignDialog({ source, all, onClose, onDone }: Props) {
  const [contents, setContents] = useState<WorkspaceContent[] | null>(null)
  const [target, setTarget] = useState('')
  const [picked, setPicked] = useState<Set<string>>(new Set())
  /** 계획 — 적용은 이 작업의 지문으로 간다(그 사이 원본이 바뀌면 서버가 거절한다). */
  const [plan, setPlan] = useState<ReassignResult | null>(null)
  const [planJob, setPlanJob] = useState<Job | null>(null)
  const [moved, setMoved] = useState<ReassignResult | null>(null)
  const [running, setRunning] = useState<Job | null>(null)
  const [stuck, setStuck] = useState(false)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<Error | null>(null)
  const open = useRef(true)

  useEffect(() => {
    open.current = true
    return () => {
      open.current = false
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    workspaceApi
      .contents(source.slug)
      .then((found) => {
        if (cancelled) return
        setContents(found)
        // 있는 것만 미리 골라 둔다. 0 건까지 켜 두면 「무엇이 실제로 옮겨지나」 가
        // 체크 표시에서 안 읽힌다.
        setPicked(new Set(found.filter((one) => one.count > 0).map((one) => one.kind)))
      })
      .catch((caught: unknown) => {
        if (!cancelled) setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
      })
    return () => {
      cancelled = true
    }
  }, [source.slug])

  const options = parentOptions(
    all.filter((one) => one.slug !== source.slug),
    null,
  )
  const total = (contents ?? [])
    .filter((one) => picked.has(one.kind))
    .reduce((sum, one) => sum + one.count, 0)

  /** 고른 것이 바뀌면 앞의 계획은 버린다 — 그 계획은 다른 것을 보고 세운 것이다. */
  function reset() {
    setPlan(null)
    setPlanJob(null)
    setNotice(null)
    setError(null)
  }

  function toggle(kind: string) {
    setPicked((before) => {
      const next = new Set(before)
      if (next.has(kind)) next.delete(kind)
      else next.add(kind)
      return next
    })
    reset()
  }

  /** 끝날 때까지 본다 — 진행률을 그리고, 대기가 길면 워커를 의심한다. */
  async function settle(started: Job): Promise<Job> {
    setRunning(started)
    setStuck(false)
    const since = Date.now()
    try {
      return await jobsApi.waitFor(started.id, (job) => {
        if (!open.current) throw new Closed()
        setRunning(job)
        setStuck(job.status === 'queued' && Date.now() - since > STUCK_MS)
      })
    } finally {
      if (open.current) {
        setRunning(null)
        setStuck(false)
      }
    }
  }

  /** 끝난 작업이 결과를 못 냈으면 그 까닭 — 취소는 「아무것도 안 옮겼다」 로 말한다. */
  function failure(finished: Job): void {
    if (finished.status === 'cancelled') {
      setNotice('취소했습니다 — 아무것도 이동하지 않았습니다.')
      return
    }
    setError(new Error(finished.error ?? '작업이 끝나지 않았습니다.'))
  }

  async function preview() {
    setBusy(true)
    reset()
    try {
      const started = await workspaceApi.reassign(source.slug, target, [...picked])
      setPlanJob(started)
      const finished = await settle(started)
      const result = reassignOf(finished.result)
      if (finished.status !== 'done' || !result) failure(finished)
      else setPlan(result)
    } catch (caught) {
      if (caught instanceof Closed) return
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      if (open.current) setBusy(false)
    }
  }

  async function apply() {
    if (!planJob) return
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      const finished = await settle(await jobsApi.apply(planJob.id))
      const result = reassignOf(finished.result)
      if (finished.status !== 'done' || !result) {
        failure(finished)
        // 실패 · 취소한 계획은 그대로 다시 적용할 수 있다 — 다만 「그 사이에 바뀌었다」 면 새로
        // 미리 봐야 하니 계획을 버린다.
        if (finished.error?.includes('JOBS-0020')) reset()
        return
      }
      setMoved(result)
      onDone()
    } catch (caught) {
      if (caught instanceof Closed) return
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      if (open.current) setBusy(false)
    }
  }

  async function stop() {
    if (!running) return
    try {
      await jobsApi.cancel(running.id)
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    }
  }

  const targetName = all.find((one) => one.slug === target)?.name ?? target
  const progress = running?.progress

  return (
    <Dialog open onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{source.name} 의 자료 이동</DialogTitle>
          <DialogDescription>
            선택한 것을 다른 부서로 통째 넘깁니다. 자료 자체는 그대로고 소유 부서만 바뀝니다. 먼저
            무엇이 몇 건 이동하는지 미리 보고, 확인한 뒤에 적용합니다. 다 넘긴 뒤에 이 부서를
            보관하거나 삭제하면 통폐합이 끝납니다.
          </DialogDescription>
        </DialogHeader>

        {moved ? (
          <div className="space-y-2 text-sm">
            <p className="text-emerald-600 dark:text-emerald-400">{targetName} 으로 옮겼습니다.</p>
            <ReassignPlanView result={moved} />
            <p className="text-muted-foreground text-xs">
              이제 이 부서를 보관하거나 삭제하면 통폐합이 끝납니다.
            </p>
          </div>
        ) : (
          <div className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="reassign-target">받을 부서</Label>
              <SearchablePicker
                id="reassign-target"
                options={options}
                value={target || null}
                onChange={(next) => {
                  setTarget(next)
                  reset()
                }}
                placeholder="어느 부서로 넘길까요"
                searchPlaceholder="부서 이름이나 주소로 검색"
              />
            </div>
            <div className="space-y-2">
              <Label>넘길 것</Label>
              {contents === null ? (
                <p className="text-muted-foreground text-sm">무엇이 있는지 확인 중…</p>
              ) : (
                <ul className="space-y-1">
                  {contents.map((one) => (
                    <li key={one.kind}>
                      <label
                        className={
                          one.count === 0
                            ? 'text-muted-foreground flex items-center gap-2 text-sm'
                            : 'flex items-center gap-2 text-sm'
                        }
                      >
                        <input
                          type="checkbox"
                          checked={picked.has(one.kind)}
                          disabled={one.count === 0 || busy}
                          onChange={() => toggle(one.kind)}
                        />
                        {one.label} {one.count}건
                      </label>
                    </li>
                  ))}
                </ul>
              )}
              {/* 관계 선과 속성으로 이 부서를 **가리키는** 것은 안 옮긴다 — 자동으로
                  바꾸면 「담당 부서」 가 사람 모르게 바뀐다. */}
              <p className="text-muted-foreground text-xs">
                이 부서를 값으로 가리키는 객체(담당 부서 같은 칸)는 변경하지 않습니다. 그것은 옮기는
                것이 아니라 고치는 일이라, 사람이 정해야 합니다.
              </p>
            </div>
            {plan && <ReassignPlanView result={plan} />}
            {plan && !plan.ok && plan.errors.length === 0 && (
              <p className="text-muted-foreground text-xs">이동할 것이 없습니다.</p>
            )}
          </div>
        )}

        {running && (
          <div className="flex flex-wrap items-center gap-2 text-xs" role="status">
            <Loader2 className="size-3.5 animate-spin" />
            <span className="text-muted-foreground">
              {running.status === 'queued'
                ? stuck
                  ? '아직 아무도 집어 가지 않았습니다 — 작업 워커가 꺼져 있을 수 있습니다. 「내 활동 › 작업」 에서 확인하세요.'
                  : '워커를 기다리는 중 — 창을 닫아도 계속되고, 작업 화면에서 볼 수 있습니다.'
                : progress && progress.total > 0
                  ? `${progress.stage} ${progress.done.toLocaleString()} / ${progress.total.toLocaleString()}건`
                  : `${progress?.stage || '진행 중'}…`}
            </span>
            {planJob && running.id !== planJob.id && (
              <Button size="xs" variant="outline" onClick={() => void stop()}>
                이동 취소
              </Button>
            )}
          </div>
        )}
        {notice && <p className="text-muted-foreground text-sm">{notice}</p>}
        <ErrorNotice error={error} />
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            {moved || running ? '닫기' : '취소'}
          </Button>
          {!moved &&
            (plan ? (
              <Button onClick={() => void apply()} disabled={busy || !plan.ok}>
                {busy && <Loader2 className="mr-1 size-3.5 animate-spin" />}
                {`적용 — ${plan.total.toLocaleString()}건 이동`}
              </Button>
            ) : (
              <Button
                onClick={() => void preview()}
                disabled={busy || !target || picked.size === 0}
              >
                {busy && <Loader2 className="mr-1 size-3.5 animate-spin" />}
                {`미리 보기 — ${total.toLocaleString()}건`}
              </Button>
            ))}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
