/**
 * 보낸 것 걷어내기 · 다시 켜기 — **시스템 관리자만**(서버도 그렇게 거절한다).
 *
 * 대상 SP 는 원천이 잠근 타입의 객체를 화면에서 못 지운다(정본은 원천). 그래서 시험으로 · 잘못 보낸
 * 것을 거두는 일은 여기서 시킨다 — 이 파이프라인이 넣은 것(기준)에서 고른 객체와 그것에 닿는 선을
 * **사용 중지 · 끊기**로 거두는 계획을 대상에 올리고, 파이프라인을 멈춘다(원천에 그 행이 그대로면 다음
 * 실행이 다시 만드니까). 대상에 들어가는 것은 평소처럼 「적용」 이다.
 *
 * 대상에서 어떻게 거둘지는 둘이다 — **사용 중지**(기본)와 **지우기**(대상 SP 0.4.101 부터 — 되돌릴 수
 * 없다. 이력 · 감사 기록은 남고, 가리키는 것이 있으면 대상이 그 줄을 막는다). 대상이 지우기를 받으면 「앞서 사용 중지로 걷어낸
 * 것」 도 고를 수 있다 — 옛 판에서 사용 중지로 걷어낸 것을 이제 지운다.
 *
 * 다시 켜기는 사람이 원천에서 지웠는지 · 레시피를 고쳤는지 확인한 뒤에 — 토큰(MCP)으로는 서버가
 * 거절한다. 걷어내기 계획이 아직 대기 중이면 서버가 대상에 묻고, 적용됐으면 기준으로 올리고 아니면 그
 * 계획을 거둔다.
 */

import { useState } from 'react'
import { Eraser, PlayCircle } from 'lucide-react'

import { relayApi, stateOf } from '@/modules/relay/api'
import type { PipelineDetail, QueuedOut, RetractIn } from '@/modules/relay/api'
import { ConfirmDialog } from '@/shared/components/ConfirmDialog'
import { Alert, AlertDescription } from '@/shared/components/ui/alert'
import { Button } from '@/shared/components/ui/button'
import { Textarea } from '@/shared/components/ui/textarea'

const SELECT = 'border-input bg-background h-8 rounded-md border px-2 text-sm'

type Mode = 'all' | 'types' | 'keys' | 'previous'
type Action = 'deprecate' | 'delete'

/** 글상자의 식별자 — 줄 · 쉼표로 나눈다. */
export function keysOf(text: string): string[] {
  return [...new Set(text.split(/[\n,]/).map((one) => one.trim()).filter(Boolean))]
}

/**
 * 고른 것을 서버의 모양으로 — 고른 것이 없으면 null(누를 수 없다). 사용 중지가 기본이라 지우기일 때만
 * `action` 을 싣는다. 앞서 걷어낸 것은 지우기로만 고른다.
 */
export function retractBody(
  mode: Mode,
  types: string[],
  keyType: string,
  keys: string,
  action: Action = 'deprecate',
): RetractIn | null {
  if (mode === 'previous') return { previous: true, action: 'delete' }
  const how: RetractIn = action === 'delete' ? { action: 'delete' } : {}
  if (mode === 'all') return { ...how, all: true }
  if (mode === 'types') return types.length ? { ...how, types } : null
  const found = keysOf(keys)
  return keyType && found.length ? { ...how, keys: { [keyType]: found } } : null
}

export function RetractControls({
  detail,
  onQueued,
  onChanged,
}: {
  detail: PipelineDetail
  /** 걷어내기 작업을 넣었다 — 작업 화면 링크를 남기고 상세를 다시 읽는다. */
  onQueued: (queued: QueuedOut & { what: string }) => void
  /** 다시 켰다 — 상세를 다시 읽는다. */
  onChanged: () => void
}) {
  const state = stateOf(detail)
  const [open, setOpen] = useState<'retract' | 'resume' | null>(null)
  const [mode, setMode] = useState<Mode>('keys')
  const [types, setTypes] = useState<string[]>([])
  const [keyType, setKeyType] = useState(detail.types[0] ?? '')
  const [keys, setKeys] = useState('')
  const [action, setAction] = useState<Action>('deprecate')
  const [said, setSaid] = useState('')
  const deletes = detail.target_deletes
  const body = retractBody(mode, types, keyType, keys, deletes ? action : 'deprecate')
  const pending = state.pending
  const removing = mode === 'previous' || (deletes && action === 'delete')

  async function retract() {
    if (!body) return
    const made = await relayApi.retract(detail.slug, body)
    onQueued({ ...made, what: '걷어내기' })
  }

  async function resume() {
    const done = await relayApi.resume(detail.slug)
    setSaid(done.message)
    onChanged()
  }

  if (state.stopped) {
    const waiting = pending?.origin === 'retract'
    return (
      <div className="space-y-2 rounded-md border border-amber-500/50 bg-amber-500/5 p-3 text-sm">
        <p className="font-medium">
          보낸 것을 걷어내고 멈췄습니다{state.stopped.scope ? ` — ${state.stopped.scope}` : ''}
        </p>
        <p className="text-muted-foreground text-xs">
          {waiting
            ? '걷어내기 계획이 적용을 기다립니다 — 대상 SP 에서 계획을 읽고 「적용」 을 클릭하세요. '
            : ''}
          원천에서 그 행을 지우거나 레시피를 고친 뒤 「다시 켜기」 를 클릭하세요 — 원천에 그대로 있으면 다음
          실행이 다시 만듭니다.
        </p>
        <Button size="sm" variant="outline" onClick={() => setOpen('resume')}>
          <PlayCircle />
          다시 켜기
        </Button>
        {said && (
          <Alert>
            <AlertDescription>{said}</AlertDescription>
          </Alert>
        )}
        <ConfirmDialog
          open={open === 'resume'}
          title="파이프라인을 다시 켭니다"
          description={
            <div className="space-y-2">
              {waiting && (
                <p>
                  적용을 기다리는 걷어내기 계획이 있습니다 — 대상에서 적용됐으면 그대로 기준으로 올리고,
                  아니면 <b>그 계획을 거둡니다</b>(대상 SP 작업 화면에 남은 그 미리 보기는 적용하지 마세요).
                </p>
              )}
              <p>
                다음 실행부터 원천을 받습니다. 원천에 걷어낸 행이 그대로 있으면 다음 계획이 그것을 다시
                만듭니다.
              </p>
            </div>
          }
          confirmLabel="다시 켜기"
          onConfirm={resume}
          onClose={() => setOpen(null)}
        />
      </div>
    )
  }

  if (!state.applied_run) return null
  const blocked = Boolean(pending)
  return (
    <div className="flex flex-wrap items-center gap-3">
      <Button size="sm" variant="outline" onClick={() => setOpen('retract')} disabled={blocked}>
        <Eraser />
        보낸 것 걷어내기
      </Button>
      <span className="text-muted-foreground text-xs">
        {blocked
          ? '적용을 기다리는 계획이 있으면 걷어낼 수 없습니다 — 그 계획을 적용하거나 다시 실행한 뒤에.'
          : '시험으로 · 잘못 보낸 객체를 대상에서 사용 중지로 거둡니다(대상 SP 화면에서는 원천의 것이라 못 지웁니다).'}
      </span>
      <ConfirmDialog
        open={open === 'retract'}
        title="보낸 것 걷어내기"
        destructive
        confirmLabel="걷어내기 계획 올리기"
        confirmDisabled={!body}
        description={
          <div className="space-y-3">
            <p>
              이 파이프라인이 대상에 넣은 것 중 고른 객체와, 그것에 닿는 선을{' '}
              <b>{removing ? '지우기' : '사용 중지'} · 끊기</b>로 거두는 계획을 올리고 <b>파이프라인을 멈춥니다</b>.
              대상에는 계획을 「적용」 해야 들어갑니다. 넣지 않은 것(다른 원천 · 사람이 만든 것)은 거절합니다.
            </p>
            <fieldset className="space-y-1.5">
              <legend className="text-muted-foreground mb-1 text-xs">대상에서</legend>
              <label className="flex items-center gap-2">
                <input
                  type="radio"
                  name="retract-action"
                  checked={!removing}
                  disabled={mode === 'previous'}
                  onChange={() => setAction('deprecate')}
                />
                사용 중지(객체는 남고 그만 씁니다)
              </label>
              <label className="flex items-center gap-2">
                <input
                  type="radio"
                  name="retract-action"
                  checked={removing}
                  disabled={!deletes}
                  onChange={() => setAction('delete')}
                />
                지우기(되돌릴 수 없습니다 — 이력 · 감사 기록은 남고, 가리키는 것이 있으면 대상이 그 줄을 막습니다)
              </label>
              {!deletes && (
                <p className="text-muted-foreground pl-6 text-xs">
                  대상 SP{detail.target_version ? `(${detail.target_version})` : ''} 는 지우기를 받지 않습니다 — SP
                  0.4.101 부터.
                </p>
              )}
            </fieldset>
            <fieldset className="space-y-2">
              <legend className="text-muted-foreground mb-1 text-xs">무엇을</legend>
              <label className="flex items-center gap-2">
                <input type="radio" name="retract-mode" checked={mode === 'keys'} onChange={() => setMode('keys')} />
                식별자로
              </label>
              {mode === 'keys' && (
                <div className="space-y-1.5 pl-6">
                  <select
                    aria-label="걷어낼 타입"
                    className={SELECT}
                    value={keyType}
                    onChange={(event) => setKeyType(event.target.value)}
                  >
                    {detail.types.map((one) => (
                      <option key={one} value={one}>
                        {one}
                      </option>
                    ))}
                  </select>
                  <Textarea
                    aria-label="걷어낼 식별자"
                    rows={4}
                    placeholder="식별자 — 한 줄에 하나(쉼표도 됩니다)"
                    value={keys}
                    onChange={(event) => setKeys(event.target.value)}
                  />
                  <p className="text-muted-foreground text-xs">{keysOf(keys).length}개</p>
                </div>
              )}
              <label className="flex items-center gap-2">
                <input type="radio" name="retract-mode" checked={mode === 'types'} onChange={() => setMode('types')} />
                타입 전부
              </label>
              {mode === 'types' && (
                <div className="flex flex-wrap gap-3 pl-6">
                  {detail.types.map((one) => (
                    <label key={one} className="flex items-center gap-1.5">
                      <input
                        type="checkbox"
                        checked={types.includes(one)}
                        onChange={(event) =>
                          setTypes((before) =>
                            event.target.checked ? [...before, one] : before.filter((other) => other !== one),
                          )
                        }
                      />
                      <span className="font-mono text-xs">{one}</span>
                    </label>
                  ))}
                </div>
              )}
              <label className="flex items-center gap-2">
                <input type="radio" name="retract-mode" checked={mode === 'all'} onChange={() => setMode('all')} />
                이 파이프라인이 넣은 것 전부
              </label>
              {deletes && (detail.retracted ?? 0) > 0 && (
                <label className="flex items-center gap-2">
                  <input
                    type="radio"
                    name="retract-mode"
                    checked={mode === 'previous'}
                    onChange={() => setMode('previous')}
                  />
                  앞서 사용 중지로 걷어낸 것 지우기(걷어낸 기록 {detail.retracted}개 중 대상에 사용 중지로 남은 것)
                </label>
              )}
            </fieldset>
          </div>
        }
        onConfirm={retract}
        onClose={() => setOpen(null)}
      />
    </div>
  )
}
