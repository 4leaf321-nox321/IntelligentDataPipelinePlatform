/**
 * 레시피 편집 — 사람이 화면에서 **초안을 작성한다**(5단계). 지금까지 초안은 AI(MCP)만 작성했다 — 운영을
 * 시작하면 「칸 하나만 고치면 되는」 일이 잦고, 그때마다 AI 를 부르게 하지 않는다.
 *
 * 출발 판(사용 중인 판 · 정의 파일의 레시피 · 지난 판)을 불러 고치고 **초안으로 저장**한다. 사용하지
 * 않는다 — 저장한 초안은 목록에서 「시험」 으로 「사용하면 보낼 것」 을 본 뒤 「사용」 한다(같은 길).
 *
 * 「칸」 은 타입마다 칸의 규칙(열 · 다른 타입의 식별자 · 고정값 · 대응 · 날짜 · 대문자)을 고친다. 여러 행
 * 모으기 · 해석기 · 대상 식별자 대조는 읽기만 하고 「본문(JSON)」 에서 고치게 한다(`recipeEdit.ts`).
 * 저장 전에 출발 판에서 **바뀐 칸**을 보인다 — 칸 하나 고치려다 다른 데를 건드린 것이 여기서 보인다.
 */

import { Fragment, useEffect, useState } from 'react'
import { Loader2, Plus, Trash2 } from 'lucide-react'

import { relayApi } from '@/modules/relay/api'
import type { RecipeSaved, RecipeVersion } from '@/modules/relay/api'
import {
  RULE_LABEL,
  blockers,
  changes,
  clone,
  fieldsOf,
  mapPairs,
  ruleKind,
  ruleText,
  typesOf,
  withField,
  withKind,
  withPairs,
  withType,
} from '@/modules/relay/recipeEdit'
import type { RecipeDoc, RecipeType, RuleKind, Spec } from '@/modules/relay/recipeEdit'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { Alert, AlertDescription } from '@/shared/components/ui/alert'
import { Button } from '@/shared/components/ui/button'
import { Input } from '@/shared/components/ui/input'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/shared/components/ui/table'
import { Textarea } from '@/shared/components/ui/textarea'
import { cn } from '@/shared/lib/utils'

const SELECT = 'border-input bg-background h-8 rounded-md border px-2 text-sm'
const FILE = 'file'
const fail = (caught: unknown) => (caught instanceof Error ? caught : new Error('알 수 없는 오류'))

type Editable = Exclude<RuleKind, 'complex'>
const KINDS = Object.keys(RULE_LABEL) as Editable[]

/** 출발 판의 이름 — 목록의 상태를 붙인다. */
function startLabel(one: RecipeVersion): string {
  if (one.status === 'active') return `사용 중인 판 ${one.version}`
  const state = one.status === 'draft' ? '초안' : '사용 종료'
  return `판 ${one.version}(${state}${one.note ? ` · ${one.note}` : ''})`
}

function MapEditor({ spec, onChange }: { spec: Spec; onChange: (next: Spec) => void }) {
  const [pairs, setPairs] = useState<[string, string][]>(() => mapPairs(spec))
  const commit = (next: [string, string][]) => {
    setPairs(next)
    onChange(withPairs(spec, next))
  }
  return (
    <div className="space-y-1">
      {pairs.map(([raw, out], index) => (
        <div key={index} className="flex items-center gap-1">
          <Input
            aria-label={`대응 ${index + 1} 원천 값`}
            className="h-7 w-28 font-mono text-xs"
            value={raw}
            onChange={(event) =>
              commit(pairs.map((one, at) => (at === index ? [event.target.value, one[1]] : one)))
            }
          />
          <span className="text-muted-foreground text-xs">→</span>
          <Input
            aria-label={`대응 ${index + 1} 대상 값`}
            className="h-7 w-28 text-xs"
            value={out}
            onChange={(event) =>
              commit(pairs.map((one, at) => (at === index ? [one[0], event.target.value] : one)))
            }
          />
          <Button
            size="xs"
            variant="ghost"
            aria-label={`대응 ${index + 1} 삭제`}
            onClick={() => commit(pairs.filter((_one, at) => at !== index))}
          >
            <Trash2 className="size-3" />
          </Button>
        </div>
      ))}
      <Button size="xs" variant="outline" onClick={() => setPairs([...pairs, ['', '']])}>
        <Plus className="size-3" />
        대응 추가
      </Button>
    </div>
  )
}

function RuleCells({
  name,
  spec,
  types,
  columnsId,
  onChange,
}: {
  name: string
  spec: Spec | undefined
  types: string[]
  columnsId: string
  onChange: (next: Spec) => void
}) {
  const kind = ruleKind(spec)
  const [mapOpen, setMapOpen] = useState(false)
  if (kind === 'complex') {
    return (
      <TableCell colSpan={3} className="whitespace-normal">
        <p className="font-mono text-xs break-all">{ruleText(spec)}</p>
        <p className="text-muted-foreground text-xs">
          여러 행 모으기 · 해석기 · 대상 식별자 대조는 「본문(JSON)」 에서 수정합니다.
        </p>
      </TableCell>
    )
  }
  const current = spec ?? { column: '' }
  return (
    <>
      <TableCell>
        <select
          aria-label={`${name} 규칙 종류`}
          className={SELECT}
          value={kind}
          onChange={(event) => onChange(withKind(current, event.target.value as Editable))}
        >
          {KINDS.map((one) => (
            <option key={one} value={one}>
              {RULE_LABEL[one]}
            </option>
          ))}
        </select>
      </TableCell>
      <TableCell className="whitespace-normal">
        {kind === 'column' && (
          <Input
            aria-label={`${name} 열`}
            list={columnsId}
            className="h-8 w-44 font-mono"
            value={String(current.column ?? '')}
            onChange={(event) => onChange({ ...current, column: event.target.value })}
          />
        )}
        {kind === 'key_of' && (
          <select
            aria-label={`${name} 다른 타입`}
            className={SELECT}
            value={String(current.key_of ?? '')}
            onChange={(event) => onChange({ key_of: event.target.value })}
          >
            <option value="">타입 선택</option>
            {types.map((one) => (
              <option key={one} value={one}>
                {one}
              </option>
            ))}
          </select>
        )}
        {kind === 'value' && (
          <Input
            aria-label={`${name} 고정값`}
            className="h-8 w-44"
            value={String(current.value ?? '')}
            onChange={(event) => onChange({ value: event.target.value })}
          />
        )}
      </TableCell>
      <TableCell className="whitespace-normal">
        {kind === 'column' && (
          <div className="space-y-1 text-xs">
            <div className="flex flex-wrap items-center gap-3">
              <label className="flex items-center gap-1">
                <input
                  type="checkbox"
                  aria-label={`${name} 날짜로`}
                  checked={Boolean(current.date)}
                  onChange={(event) => {
                    const next = { ...current }
                    if (event.target.checked) next.date = true
                    else delete next.date
                    onChange(next)
                  }}
                />
                날짜로
              </label>
              <label className="flex items-center gap-1">
                <input
                  type="checkbox"
                  aria-label={`${name} 대문자로`}
                  checked={Boolean(current.upper)}
                  onChange={(event) => {
                    const next = { ...current }
                    if (event.target.checked) next.upper = true
                    else delete next.upper
                    onChange(next)
                  }}
                />
                대문자로
              </label>
              <Button
                size="xs"
                variant="ghost"
                aria-label={`${name} 대응 ${mapOpen ? '접기' : '펼치기'}`}
                onClick={() => setMapOpen(!mapOpen)}
              >
                대응 {mapPairs(current).length}개 {mapOpen ? '접기' : '펼치기'}
              </Button>
            </div>
            {mapOpen && <MapEditor spec={current} onChange={onChange} />}
          </div>
        )}
      </TableCell>
    </>
  )
}

function TypeFields({
  one,
  types,
  columnsId,
  onChange,
}: {
  one: RecipeType
  types: string[]
  columnsId: string
  onChange: (next: RecipeType) => void
}) {
  const [adding, setAdding] = useState('')
  const names = new Set(fieldsOf(one).map(([name]) => name))
  const add = () => {
    const name = adding.trim()
    if (!name || names.has(name)) return
    onChange(withField(one, name, { column: '' }))
    setAdding('')
  }
  return (
    <div className="space-y-2">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>칸</TableHead>
            <TableHead>규칙 종류</TableHead>
            <TableHead>값</TableHead>
            <TableHead>열의 처리</TableHead>
            <TableHead />
          </TableRow>
        </TableHeader>
        <TableBody>
          {fieldsOf(one).map(([name, spec]) => (
            <TableRow key={name} data-testid={`recipe-field-${name}`}>
              <TableCell className="font-mono">{name}</TableCell>
              <RuleCells
                name={name}
                spec={spec}
                types={types}
                columnsId={columnsId}
                onChange={(next) => onChange(withField(one, name, next))}
              />
              <TableCell className="text-right">
                {name !== 'key' && name !== 'label' && (
                  <Button
                    size="xs"
                    variant="ghost"
                    aria-label={`${name} 칸 삭제`}
                    onClick={() => onChange(withField(one, name, null))}
                  >
                    <Trash2 className="size-3" />
                  </Button>
                )}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <div className="flex items-center gap-2">
        <Input
          aria-label="새 칸 이름"
          className="h-8 w-48 font-mono"
          placeholder="새 칸 이름(대상 속성)"
          value={adding}
          onChange={(event) => setAdding(event.target.value)}
        />
        <Button size="sm" variant="outline" disabled={!adding.trim() || names.has(adding.trim())} onClick={add}>
          <Plus className="size-4" />칸 추가
        </Button>
        {names.has(adding.trim()) && <span className="text-destructive text-xs">이미 있는 칸입니다</span>}
      </div>
    </div>
  )
}

export function RecipeEditor({
  slug,
  versions,
  onSaved,
  onClose,
}: {
  slug: string
  versions: RecipeVersion[]
  onSaved: (saved: RecipeSaved) => void
  onClose: () => void
}) {
  const usable = versions.filter((one) => one.status !== 'discarded')
  const active = usable.find((one) => one.status === 'active')
  const [start, setStart] = useState<string>(active ? String(active.version) : FILE)
  const [base, setBase] = useState<RecipeDoc | null>(null)
  const [draft, setDraft] = useState<RecipeDoc | null>(null)
  const [typeSlug, setTypeSlug] = useState('')
  const [mode, setMode] = useState<'fields' | 'json'>('fields')
  const [text, setText] = useState('')
  const [parseError, setParseError] = useState('')
  const [note, setNote] = useState('')
  const [columns, setColumns] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | null>(null)
  const columnsId = `recipe-columns-${slug}`

  async function load(which: string) {
    setBusy(true)
    setError(null)
    try {
      const body = (
        which === FILE
          ? (await relayApi.recipeFile(slug)).body
          : (await relayApi.recipe(slug, Number(which))).body
      ) as RecipeDoc | null
      if (!body) throw new Error('정의 파일에 레시피가 없습니다 — 다른 출발 판을 선택하세요.')
      setBase(clone(body))
      setDraft(clone(body))
      setTypeSlug(typesOf(body)[0]?.type_slug ?? '')
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
    relayApi
      .sample(slug, 1)
      .then((got) => setColumns(got.columns.length ? got.columns : Object.keys(got.rows[0] ?? {})))
      .catch(() => setColumns([]))
    // 처음 열 때 한 번 — 출발 판을 바꾸면 「불러오기」 로 다시 읽는다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slug])

  /** 본문(JSON)에서 칸으로 — 읽히지 않으면 바꾸지 않고 그 까닭을 말한다. */
  function toFields() {
    try {
      const parsed = JSON.parse(text) as RecipeDoc
      setDraft(parsed)
      setParseError('')
      if (!typesOf(parsed).some((one) => one.type_slug === typeSlug)) {
        setTypeSlug(typesOf(parsed)[0]?.type_slug ?? '')
      }
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

  async function save() {
    const doc = mode === 'json' ? toFields() : draft
    if (!doc || blockers(doc).length) return
    setBusy(true)
    setError(null)
    try {
      onSaved(await relayApi.saveRecipe(slug, doc, note.trim()))
    } catch (caught) {
      setError(fail(caught))
    } finally {
      setBusy(false)
    }
  }

  const changed = base && draft && mode === 'fields' ? changes(base, draft) : []
  const blocked = draft && mode === 'fields' ? blockers(draft) : []
  const types = draft ? typesOf(draft) : []
  const one = types.find((each) => each.type_slug === typeSlug)

  return (
    <div className="space-y-3 rounded-md border p-3" data-testid="recipe-editor">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium">초안 작성</span>
        <select
          aria-label="출발 판"
          className={SELECT}
          value={start}
          onChange={(event) => setStart(event.target.value)}
        >
          {active && <option value={String(active.version)}>{startLabel(active)}</option>}
          <option value={FILE}>정의 파일의 레시피</option>
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
      <ErrorNotice error={error} />
      <datalist id={columnsId}>
        {columns.map((column) => (
          <option key={column} value={column} />
        ))}
      </datalist>

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
            {mode === 'fields' && types.length > 0 && (
              <select
                aria-label="편집할 타입"
                className={cn(SELECT, 'ml-2')}
                value={typeSlug}
                onChange={(event) => setTypeSlug(event.target.value)}
              >
                {types.map((each) => (
                  <option key={each.type_slug} value={each.type_slug}>
                    {each.type_slug}
                  </option>
                ))}
              </select>
            )}
          </div>

          {mode === 'fields' && one && (
            <TypeFields
              key={`${typeSlug}-${start}`}
              one={one}
              types={types.map((each) => each.type_slug)}
              columnsId={columnsId}
              onChange={(next) => setDraft(withType(draft, typeSlug, next))}
            />
          )}
          {mode === 'json' && (
            <Textarea
              aria-label="레시피 본문"
              className="min-h-72 font-mono text-xs"
              value={text}
              onChange={(event) => setText(event.target.value)}
            />
          )}
          {parseError && <p className="text-destructive text-sm">{parseError}</p>}

          {mode === 'fields' && (
            <p className="text-muted-foreground text-sm" data-testid="recipe-changes">
              {changed.length
                ? `출발 판에서 바뀐 것: ${changed.join(' · ')}`
                : '출발 판에서 바뀐 것이 없습니다.'}
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
              aria-label="메모"
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
