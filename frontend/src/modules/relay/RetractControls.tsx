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
    onQueued({ ...made, what: '전송 데이터 회수' })
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
          전송 데이터를 회수하고 파이프라인을 중지했습니다.
          {state.stopped.scope ? ` 회수 대상: ${state.stopped.scope}` : ''}
        </p>
        <p className="text-muted-foreground text-xs">
          {waiting
            ? '회수 계획이 적용 대기 중입니다. 대상 SP에서 계획을 확인하고 [적용]을 클릭하세요. '
            : ''}
          원천에서 해당 데이터를 삭제하거나 레시피를 수정한 후 [재개]를 클릭하세요. 원천에 데이터가 남아
          있으면 다음 실행이 다시 생성합니다.
        </p>
        <Button size="sm" variant="outline" onClick={() => setOpen('resume')}>
          <PlayCircle />
          재개
        </Button>
        {said && (
          <Alert>
            <AlertDescription>{said}</AlertDescription>
          </Alert>
        )}
        <ConfirmDialog
          open={open === 'resume'}
          title="파이프라인 재개"
          description={
            <div className="space-y-2">
              {waiting && (
                <p>
                  적용 대기 중인 회수 계획이 있습니다. 대상에서 적용되었으면 기준 출력으로 반영하고, 적용되지
                  않았으면 <b>해당 계획을 취소합니다</b>. 대상 SP의 [작업] 화면에 남은 미리 보기는 적용하지
                  마세요.
                </p>
              )}
              <p>
                다음 실행부터 원천을 수신합니다. 원천에 회수한 데이터가 남아 있으면 다음 계획이 해당 객체를
                다시 생성합니다.
              </p>
            </div>
          }
          confirmLabel="재개"
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
        전송 데이터 회수
      </Button>
      <span className="text-muted-foreground text-xs">
        {blocked
          ? '적용 대기 중인 계획이 있어 회수할 수 없습니다. 계획을 적용하거나 다시 실행한 후 진행하세요.'
          : '테스트 데이터나 오전송 데이터를 대상에서 회수합니다. 원천이 관리하는 객체는 대상 SP 화면에서 삭제할 수 없습니다.'}
      </span>
      <ConfirmDialog
        open={open === 'retract'}
        title="전송 데이터 회수"
        destructive
        confirmLabel="회수 계획 생성"
        confirmDisabled={!body}
        description={
          <div className="space-y-3">
            <p>
              이 파이프라인이 대상에 적재한 객체 중 선택한 객체를 <b>{removing ? '삭제' : '사용 중지'}</b>하고
              연결된 관계를 해제하는 회수 계획을 생성한 후 <b>파이프라인을 중지</b>합니다. 대상에는 계획을
              [적용]해야 반영됩니다. 이 파이프라인이 적재하지 않은 객체(다른 원천 또는 사람이 생성한 객체)는
              회수할 수 없습니다.
            </p>
            <fieldset className="space-y-1.5">
              <legend className="text-muted-foreground mb-1 text-xs">회수 방식</legend>
              <label className="flex items-center gap-2">
                <input
                  type="radio"
                  name="retract-action"
                  checked={!removing}
                  disabled={mode === 'previous'}
                  onChange={() => setAction('deprecate')}
                />
                사용 중지(기본, 객체는 남음)
              </label>
              <label className="flex items-center gap-2">
                <input
                  type="radio"
                  name="retract-action"
                  checked={removing}
                  disabled={!deletes}
                  onChange={() => setAction('delete')}
                />
                삭제(되돌릴 수 없음)
              </label>
              {deletes ? (
                <p className="text-muted-foreground pl-6 text-xs">
                  이력과 감사 기록은 남습니다. 다른 객체가 참조하는 객체는 대상이 삭제를 거부합니다.
                </p>
              ) : (
                <p className="text-muted-foreground pl-6 text-xs">
                  삭제는 SP 0.4.101 이상 지원
                  {detail.target_version ? `(현재 대상 ${detail.target_version})` : ''}
                </p>
              )}
            </fieldset>
            <fieldset className="space-y-2">
              <legend className="text-muted-foreground mb-1 text-xs">회수 대상</legend>
              <label className="flex items-center gap-2">
                <input type="radio" name="retract-mode" checked={mode === 'keys'} onChange={() => setMode('keys')} />
                식별자 지정
              </label>
              {mode === 'keys' && (
                <div className="space-y-1.5 pl-6">
                  <select
                    aria-label="회수할 타입"
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
                    aria-label="회수할 식별자"
                    rows={4}
                    placeholder="식별자(줄바꿈 또는 쉼표로 구분)"
                    value={keys}
                    onChange={(event) => setKeys(event.target.value)}
                  />
                  <p className="text-muted-foreground text-xs">{keysOf(keys).length}개</p>
                </div>
              )}
              <label className="flex items-center gap-2">
                <input type="radio" name="retract-mode" checked={mode === 'types'} onChange={() => setMode('types')} />
                타입 전체
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
                이 파이프라인이 적재한 객체 전체
              </label>
              {deletes && (detail.retracted ?? 0) > 0 && (
                <label className="flex items-center gap-2">
                  <input
                    type="radio"
                    name="retract-mode"
                    checked={mode === 'previous'}
                    onChange={() => setMode('previous')}
                  />
                  이전에 사용 중지로 회수한 객체 삭제(회수 기록 {detail.retracted}개 중 대상에 사용 중지 상태로 남은
                  객체)
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
