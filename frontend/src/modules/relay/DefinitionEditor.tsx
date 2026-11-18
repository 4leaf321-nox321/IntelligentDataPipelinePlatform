/**
 * 정의 편집 — 사람이 화면에서 **정의 판의 초안을 작성한다**. 지금까지 정의 초안은 AI(MCP)와 「새 연결」 만
 * 지었다 — 운영을 시작하면 「주소 하나 · 일정 하나만 고치면 되는」 일이 잦고, 그때마다 AI 를 부르게 하지
 * 않는다(레시피 편집과 같은 길).
 *
 * 출발 판(사용 중인 판 · 정의 파일 · 지난 판)을 불러 고치고 **초안으로 저장**한다. 사용하지 않는다 —
 * 저장한 초안은 목록에서 「시험」 으로 원천 · 대상에 닿는지 본 뒤 「사용」 한다.
 *
 * 「칸」 은 어댑터의 칸(서버가 정의 모양에서 계산해 준 것) · 식별자 · 열 · 인증 · 계산 칸 · 받기 점검 ·
 * 일정 · 대상을 고친다. 어댑터 종류 · 원천 이름(`source`) · 자동 적용처럼 무게가 큰 칸은 「본문(JSON)」 에서
 * 고친다. 비밀은 받지 않는다 — 환경 변수의 이름만. 저장 전에 출발 판에서 **바뀐 칸**을 보인다.
 */

import { Fragment, useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { Loader2, Plus, Trash2 } from 'lucide-react'

import { relayApi } from '@/modules/relay/api'
import type { AdapterField, DefinitionSaved, DefinitionVersion } from '@/modules/relay/api'
import { AUTH_CERT, AUTH_EXTRA, AUTH_KINDS, AUTH_LABEL, authKindsFor, valueOf } from '@/modules/relay/connect'
import {
  adapterFields,
  at,
  authKindOf,
  blockers,
  changes,
  clone,
  columnRows,
  columnsOf,
  isPlain,
  keyOf,
  keyText,
  scheduleKindOf,
  textOf,
  withAt,
  withAuthKind,
  withSchedule,
} from '@/modules/relay/definitionEdit'
import type { DefinitionDoc, ScheduleKind } from '@/modules/relay/definitionEdit'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { Alert, AlertDescription } from '@/shared/components/ui/alert'
import { Button } from '@/shared/components/ui/button'
import { Input } from '@/shared/components/ui/input'
import { Textarea } from '@/shared/components/ui/textarea'
import { useResource } from '@/shared/hooks/useResource'

const SELECT = 'border-input bg-background h-8 rounded-md border px-2 text-sm'
const FILE = 'file'
const fail = (caught: unknown) => (caught instanceof Error ? caught : new Error('알 수 없는 오류'))

/** 출발 판의 이름 — 목록의 상태를 붙인다. */
function startLabel(one: DefinitionVersion): string {
  if (one.status === 'active') return `사용 중인 판 ${one.version}`
  const state = one.status === 'draft' ? '초안' : '사용 종료'
  return `판 ${one.version}(${state}${one.note ? ` · ${one.note}` : ''})`
}

function Row({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="grid gap-1 text-sm sm:grid-cols-[13rem_1fr] sm:items-start">
      <span className="text-muted-foreground pt-1.5 text-xs">{label}</span>
      <span className="space-y-0.5">
        {children}
        {hint && <span className="text-muted-foreground block text-xs">{hint}</span>}
      </span>
    </label>
  )
}

function Group({ title, children }: { title: string; children: ReactNode }) {
  return (
    <fieldset className="space-y-2 rounded-md border p-3">
      <legend className="px-1 text-sm font-medium">{title}</legend>
      {children}
    </fieldset>
  )
}

/** JSON 칸 하나 — 고치는 동안은 글로 들고, 읽히면 본문에 넣는다. 비우면 그 칸을 지운다. */
function JsonField({
  label,
  hint,
  value,
  onChange,
}: {
  label: string
  hint: string
  value: unknown
  onChange: (next: unknown) => void
}) {
  const [text, setText] = useState(() => (value === undefined ? '' : JSON.stringify(value, null, 2)))
  const [bad, setBad] = useState('')
  return (
    <Row label={label} hint={hint}>
      <Textarea
        aria-label={label}
        className="min-h-24 font-mono text-xs"
        value={text}
        onChange={(event) => {
          const next = event.target.value
          setText(next)
          if (!next.trim()) {
            setBad('')
            onChange(undefined)
            return
          }
          try {
            onChange(JSON.parse(next))
            setBad('')
          } catch (caught) {
            setBad(`JSON 으로 읽지 못했습니다 — ${fail(caught).message}`)
          }
        }}
      />
      {bad && <span className="text-destructive block text-xs">{bad}</span>}
    </Row>
  )
}

/** 어댑터 칸 하나 — 수 · JSON 칸은 그 모양으로 읽는다(`connect.valueOf`). 비우면 서버의 기본값. */
function AdapterInput({
  field,
  value,
  onChange,
}: {
  field: AdapterField
  value: unknown
  onChange: (next: unknown) => void
}) {
  const [text, setText] = useState(() => textOf(value))
  const [bad, setBad] = useState('')
  const fallback = field.default === null || field.default === '' ? '' : `기본 ${textOf(field.default)}`
  return (
    <Row label={`${field.name}${field.required ? ' *' : ''}`} hint={field.doc || undefined}>
      <Input
        aria-label={field.name}
        className="h-8 font-mono text-xs"
        placeholder={fallback}
        value={text}
        onChange={(event) => {
          setText(event.target.value)
          try {
            onChange(valueOf(field, event.target.value))
            setBad('')
          } catch (caught) {
            setBad(fail(caught).message)
          }
        }}
      />
      {bad && <span className="text-destructive block text-xs">{bad}</span>}
    </Row>
  )
}

function ColumnsTable({ rows, onChange }: { rows: [string, string][]; onChange: (next: [string, string][]) => void }) {
  const set = (index: number, which: 0 | 1, text: string) =>
    onChange(rows.map((one, at) => (at === index ? (which === 0 ? [text, one[1]] : [one[0], text]) : one)))
  return (
    <div className="space-y-1">
      {rows.map(([name, path], index) => (
        <div key={index} className="flex items-center gap-1">
          <Input
            aria-label={`열 ${index + 1} 이름`}
            className="h-8 w-48 font-mono text-xs"
            placeholder="열 이름(LOT_ID)"
            value={name}
            onChange={(event) => set(index, 0, event.target.value)}
          />
          <span className="text-muted-foreground text-xs">←</span>
          <Input
            aria-label={`열 ${index + 1} 원천의 자리`}
            className="h-8 flex-1 font-mono text-xs"
            placeholder="원천의 자리(LotId · steps.Seq)"
            value={path}
            onChange={(event) => set(index, 1, event.target.value)}
          />
          <Button
            size="icon"
            variant="ghost"
            className="size-8"
            aria-label={`열 ${index + 1} 삭제`}
            onClick={() => onChange(rows.filter((_one, at) => at !== index))}
          >
            <Trash2 className="size-4" />
          </Button>
        </div>
      ))}
      <Button size="sm" variant="outline" onClick={() => onChange([...rows, ['', '']])}>
        <Plus className="size-4" />열 추가
      </Button>
    </div>
  )
}

/** 「칸」 — 본문의 칸을 고친다. 고친 것은 곧장 `onChange` 로(본문 하나가 정본이다). */
function DefinitionFields({
  doc,
  fields,
  onChange,
}: {
  doc: DefinitionDoc
  fields: AdapterField[]
  onChange: (next: DefinitionDoc) => void
}) {
  const put = (path: string[], value: unknown) => onChange(withAt(doc, path, value))
  const adapter = isPlain(doc.adapter) ? doc.adapter : {}
  const auth = isPlain(adapter.auth) ? adapter.auth : undefined
  const kind = authKindOf(auth)
  const rows = columnRows(adapter.columns)
  const intake = doc.intake
  const intakeOn = intake !== false
  const scheduleKind = scheduleKindOf(doc.schedule)
  const [columns, setColumns] = useState<[string, string][]>(() => rows ?? [])
  const authText = (name: string) => textOf(at(auth, [name]))
  const putAuth = (name: string, text: string) => {
    const next = withAt({ auth: auth ?? { kind } }, ['auth', name], text.trim() || undefined)
    const cleaned = next.auth as Record<string, unknown> | undefined
    put(['adapter', 'auth'], cleaned && (cleaned.kind !== 'none' || Object.keys(cleaned).length > 1) ? cleaned : undefined)
  }

  return (
    <div className="space-y-3">
      <Group title={`원천 — 어댑터 ${String(adapter.kind ?? '?')}`}>
        <p className="text-muted-foreground text-xs">
          어댑터 종류는 「본문(JSON)」 에서 바꿉니다 — 종류마다 칸이 다릅니다. 비운 칸은 서버의 기본값을 씁니다.
        </p>
        {fields.map((field) => (
          <AdapterInput
            key={field.name}
            field={field}
            value={adapter[field.name]}
            onChange={(next) => put(['adapter', field.name], next)}
          />
        ))}
        <Row label="식별자 *" hint="안 바뀌는 칸 — 여럿이면 , 로 나눕니다(값을 | 로 이은 여러 칸 식별자).">
          <Input
            aria-label="식별자"
            className="h-8 font-mono text-xs"
            defaultValue={keyText(adapter.key)}
            onChange={(event) => put(['adapter', 'key'], keyOf(event.target.value))}
          />
        </Row>
        <Row label="열" hint="레시피가 읽을 열 이름 ← 원천의 자리.">
          {rows === null ? (
            <span className="font-mono text-xs">
              {textOf(adapter.columns)} — 파일의 모든 열을 그대로 읽습니다(「본문(JSON)」 에서 수정).
            </span>
          ) : (
            <ColumnsTable
              rows={columns}
              onChange={(next) => {
                setColumns(next)
                put(['adapter', 'columns'], columnsOf(next))
              }}
            />
          )}
        </Row>
      </Group>

      <Group title="인증">
        <Row label="방식">
          <select
            aria-label="인증 방식"
            className={SELECT}
            value={kind}
            onChange={(event) =>
              put(['adapter', 'auth'], withAuthKind(auth, event.target.value as (typeof AUTH_KINDS)[number]))
            }
          >
            {authKindsFor(String(adapter.kind ?? '')).map((one) => (
              <option key={one} value={one}>
                {AUTH_LABEL[one]}
              </option>
            ))}
          </select>
        </Row>
        {kind !== 'none' && (
          <Row label="비밀의 환경 변수 이름" hint="값은 서버의 .env 에 둡니다 — 여기에는 이름만.">
            <Input
              aria-label="비밀의 환경 변수 이름"
              className="h-8 font-mono text-xs"
              value={authText('token_env')}
              onChange={(event) => putAuth('token_env', event.target.value)}
            />
          </Row>
        )}
        {[...AUTH_EXTRA[kind], ...AUTH_CERT].map((extra) => (
          <Row key={extra.name} label={extra.label}>
            <Input
              aria-label={extra.label}
              className="h-8 font-mono text-xs"
              value={authText(extra.name)}
              onChange={(event) => putAuth(extra.name, event.target.value)}
            />
          </Row>
        ))}
      </Group>

      <Group title="계산 칸 · 받기 점검">
        <JsonField
          label="계산 칸(computed)"
          hint='{"새 열": {"template": "{A}|{B}"}} — template · number · datetime · replace · first. 비우면 계산 칸이 없습니다.'
          value={doc.computed}
          onChange={(next) => put(['computed'], next)}
        />
        <Row label="받기 점검" hint="받은 것이 이상하면(대량 삭제 · 늘 있던 값이 빔) 변환 전에 멈춥니다.">
          <span className="flex flex-wrap items-center gap-3 text-sm">
            <label className="flex items-center gap-1">
              <input
                type="checkbox"
                aria-label="받기 점검 사용"
                checked={intakeOn}
                onChange={(event) => put(['intake'], event.target.checked ? undefined : false)}
              />
              사용
            </label>
            {intakeOn &&
              (
                [
                  ['delete_ratio', '삭제 비율(0.05)'],
                  ['delete_rows', '삭제 건수(10)'],
                  ['blank_rows', '빈 칸 행(3)'],
                ] as const
              ).map(([name, label]) => (
                <Input
                  key={name}
                  aria-label={`받기 점검 ${name}`}
                  className="h-8 w-32 font-mono text-xs"
                  placeholder={label}
                  defaultValue={textOf(at(intake, [name]))}
                  onChange={(event) => {
                    const text = event.target.value.trim()
                    const number = Number(text)
                    put(['intake', name], text ? (Number.isFinite(number) ? number : text) : undefined)
                  }}
                />
              ))}
          </span>
        </Row>
      </Group>

      <Group title="일정 · 대상">
        <Row label="일정">
          <span className="flex flex-wrap items-center gap-2">
            <select
              aria-label="일정"
              className={SELECT}
              value={scheduleKind}
              onChange={(event) => {
                const next = event.target.value as ScheduleKind
                put(['schedule'], withSchedule(doc.schedule, next, next === 'daily' ? '01:00' : '60'))
              }}
            >
              <option value="manual">수동</option>
              <option value="daily">매일</option>
              <option value="every">몇 분마다</option>
            </select>
            {scheduleKind === 'daily' && (
              <Input
                aria-label="매일 시각"
                type="time"
                className="h-8 w-32"
                value={textOf(at(doc.schedule, ['daily_at']))}
                onChange={(event) => put(['schedule'], withSchedule(doc.schedule, 'daily', event.target.value))}
              />
            )}
            {scheduleKind === 'every' && (
              <Input
                aria-label="몇 분마다"
                className="h-8 w-24 font-mono text-xs"
                value={textOf(at(doc.schedule, ['every_minutes']))}
                onChange={(event) => put(['schedule'], withSchedule(doc.schedule, 'every', event.target.value))}
              />
            )}
          </span>
        </Row>
        <Row label="원천 시스템의 이름(system)">
          <Input
            aria-label="원천 시스템의 이름"
            className="h-8"
            value={textOf(doc.system)}
            onChange={(event) => put(['system'], event.target.value || undefined)}
          />
        </Row>
        {(
          [
            ['name', '대상 이름'],
            ['server', '대상 SP 주소 *'],
            ['token_env', '대상 토큰의 환경 변수 *'],
          ] as const
        ).map(([name, label]) => (
          <Row key={name} label={label}>
            <Input
              aria-label={label.replace(' *', '')}
              className="h-8 font-mono text-xs"
              value={textOf(at(doc.target, [name]))}
              onChange={(event) => put(['target', name], event.target.value.trim() ? event.target.value : undefined)}
            />
          </Row>
        ))}
      </Group>
    </div>
  )
}

export function DefinitionEditor({
  slug,
  versions,
  onSaved,
  onClose,
}: {
  slug: string
  versions: DefinitionVersion[]
  onSaved: (saved: DefinitionSaved) => void
  onClose: () => void
}) {
  const usable = versions.filter((one) => one.status !== 'discarded')
  const active = usable.find((one) => one.status === 'active')
  const [start, setStart] = useState<string>(active ? String(active.version) : FILE)
  const [base, setBase] = useState<DefinitionDoc | null>(null)
  const [draft, setDraft] = useState<DefinitionDoc | null>(null)
  /** 불러올 때마다 칸을 새로 세운다 — 칸들이 든 글을 버리게. */
  const [loaded, setLoaded] = useState(0)
  const [mode, setMode] = useState<'fields' | 'json'>('fields')
  const [text, setText] = useState('')
  const [parseError, setParseError] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | null>(null)
  const kinds = useResource(() => relayApi.adapterKinds(), [])

  async function load(which: string) {
    setBusy(true)
    setError(null)
    try {
      const body =
        which === FILE
          ? (await relayApi.definitionFile(slug)).body
          : (await relayApi.definition(slug, Number(which))).body
      if (!isPlain(body)) {
        throw new Error('같은 slug 의 정의 파일이 없습니다 — 다른 출발 판을 선택하세요.')
      }
      setBase(clone(body))
      setDraft(clone(body))
      setLoaded((n) => n + 1)
      setMode('fields')
      setParseError('')
    } catch (caught) {
      setError(fail(caught))
    } finally {
      setBusy(false)
    }
  }

  useEffect(() => {
    void load(start)
    // 처음 열 때 한 번 — 출발 판을 바꾸면 「불러오기」 로 다시 읽는다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slug])

  /** 본문(JSON)에서 칸으로 — 읽히지 않으면 바꾸지 않고 그 까닭을 말한다. */
  function toFields(): DefinitionDoc | null {
    try {
      const parsed: unknown = JSON.parse(text)
      if (!isPlain(parsed)) throw new Error('JSON 객체({ … })가 아닙니다')
      setDraft(parsed)
      setLoaded((n) => n + 1)
      setParseError('')
      setMode('fields')
      return parsed
    } catch (caught) {
      setParseError(`본문을 JSON 으로 읽지 못했습니다 — ${fail(caught).message}`)
      return null
    }
  }

  function toJson() {
    setText(JSON.stringify(draft, null, 2))
    setParseError('')
    setMode('json')
  }

  const secretFields = kinds.data?.secret_fields ?? ['dsn', 'password', 'secret', 'token']
  const blocked = draft && mode === 'fields' ? blockers(draft, slug, secretFields) : []

  async function save() {
    const doc = mode === 'json' ? toFields() : draft
    if (!doc || blockers(doc, slug, secretFields).length) return
    setBusy(true)
    setError(null)
    try {
      onSaved(await relayApi.saveDefinition(slug, doc, note.trim()))
    } catch (caught) {
      setError(fail(caught))
    } finally {
      setBusy(false)
    }
  }

  const changed = base && draft && mode === 'fields' ? changes(base, draft) : []
  const adapterKind = draft && isPlain(draft.adapter) ? String(draft.adapter.kind ?? '') : ''
  const spec = kinds.data?.kinds.find((one) => one.kind === adapterKind)

  return (
    <div className="space-y-3 rounded-md border p-3" data-testid="definition-editor">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium">초안 작성</span>
        <select
          aria-label="정의 출발 판"
          className={SELECT}
          value={start}
          onChange={(event) => setStart(event.target.value)}
        >
          {active && <option value={String(active.version)}>{startLabel(active)}</option>}
          <option value={FILE}>정의 파일</option>
          {usable
            .filter((each) => each.status !== 'active')
            .map((each) => (
              <option key={each.version} value={String(each.version)}>
                {startLabel(each)}
              </option>
            ))}
        </select>
        <Button size="sm" variant="outline" disabled={busy} onClick={() => void load(start)}>
          불러오기
        </Button>
        {busy && <Loader2 className="size-4 animate-spin" />}
        <span className="text-muted-foreground text-xs">
          불러온 판은 바뀌지 않습니다 — 고친 것은 새 초안으로 저장됩니다.
        </span>
      </div>
      <ErrorNotice error={error ?? kinds.error} />

      {draft && (
        <Fragment>
          <div className="flex items-center gap-1">
            <Button
              size="sm"
              variant={mode === 'fields' ? 'secondary' : 'ghost'}
              aria-pressed={mode === 'fields'}
              onClick={() => mode === 'json' && toFields()}
            >
              칸
            </Button>
            <Button
              size="sm"
              variant={mode === 'json' ? 'secondary' : 'ghost'}
              aria-pressed={mode === 'json'}
              onClick={() => mode === 'fields' && toJson()}
            >
              본문(JSON)
            </Button>
          </div>

          {mode === 'fields' && (
            <DefinitionFields
              key={loaded}
              doc={draft}
              fields={adapterFields(spec?.fields ?? [])}
              onChange={setDraft}
            />
          )}
          {mode === 'json' && (
            <Textarea
              aria-label="정의 본문"
              className="min-h-72 font-mono text-xs"
              value={text}
              onChange={(event) => setText(event.target.value)}
            />
          )}
          {parseError && <p className="text-destructive text-sm">{parseError}</p>}

          {mode === 'fields' && (
            <p className="text-muted-foreground text-sm" data-testid="definition-changes">
              {changed.length ? `출발 판에서 바뀐 것: ${changed.join(' · ')}` : '출발 판에서 바뀐 것이 없습니다.'}
            </p>
          )}
          {blocked.length > 0 && (
            <Alert variant="destructive">
              <AlertDescription>
                <ul className="list-disc space-y-0.5 pl-4">
                  {blocked.map((each) => (
                    <li key={each}>{each}</li>
                  ))}
                </ul>
              </AlertDescription>
            </Alert>
          )}
          <div className="flex flex-wrap items-center gap-2">
            <Input
              aria-label="정의 메모"
              className="h-8 w-80"
              placeholder="메모 — 무엇을 왜 수정했나"
              value={note}
              onChange={(event) => setNote(event.target.value)}
            />
            <Button
              size="sm"
              disabled={busy || blocked.length > 0 || (mode === 'fields' && changed.length === 0)}
              onClick={() => void save()}
            >
              초안 저장
            </Button>
            <Button size="sm" variant="ghost" onClick={onClose}>
              취소
            </Button>
          </div>
        </Fragment>
      )}
    </div>
  )
}
