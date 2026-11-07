/**
 * 새 연결 — **원천의 주소와 모양을 받아 SP 로 중계하는 파이프라인을 화면에서 만든다**(시스템 관리자).
 * 그동안은 AI(MCP)가 정의 판을 짓거나 운영자가 서버에 정의 파일을 둬야 했다.
 *
 *     1 원천   어댑터 종류 · 주소 · 인증(비밀은 환경 변수 이름만) → 「탐색」 으로 칸을 본다
 *     2 칸     레시피가 읽을 칸 · 열 이름 · 식별자(여럿이면 여러 칸 식별자)
 *     3 대상   대상 SP · 토큰의 환경 변수 · 일정 → 「정의 판 초안 저장」
 *     4 레시피 대상의 타입을 골라 칸을 잇거나, 대상에 없으면 **SP 타입 정의 초안**을 다운로드해
 *              SP 관리자에게 넘기고 같은 칸으로 레시피를 짓는다 → 「레시피 판 초안 저장」
 *     5 시험   원천을 받아 레시피로 바꿔 본다(대상에는 아무것도 안 보낸다)
 *
 * **사용은 여기서 하지 않는다** — 파이프라인 상세의 「정의 판」 · 「레시피 판」 에서 시험 결과를 읽고
 * 사람이 사용한다(지금까지와 같은 길). 어댑터의 칸은 서버가 정의 모양에서 계산한 것을 그린다
 * (`/relay/adapter-kinds`) — 서버에 칸이 늘면 「더 보기」 에 저절로 선다.
 */

import { useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { Download, Loader2, Save, Search, TestTube } from 'lucide-react'
import { Link } from 'react-router-dom'

import { relayApi } from '@/modules/relay/api'
import type {
  AdapterField,
  DefinitionSaved,
  DefinitionTrial,
  DiscoverOut,
  RecipeSaved,
  TargetSchema,
  TypeDraft,
} from '@/modules/relay/api'
import {
  AUTH_EXTRA,
  AUTH_KINDS,
  AUTH_LABEL,
  SPECIAL,
  adapterOf,
  definitionOf,
  picksOf,
  recipeOf,
} from '@/modules/relay/connect'
import type { AuthValues, DefinitionForm, Picked } from '@/modules/relay/connect'
import { DefinitionTrialView } from '@/modules/relay/DefinitionTrialView'
import { useAuth } from '@/shared/auth/AuthContext'
import { isSystemAdmin } from '@/shared/auth/roles'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { PageHeader } from '@/shared/components/PageHeader'
import { Alert, AlertDescription, AlertTitle } from '@/shared/components/ui/alert'
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
import { useResource } from '@/shared/hooks/useResource'

const BACK = { to: '/relay/pipelines', label: '파이프라인' }
const SELECT = 'border-input bg-background h-9 rounded-md border px-2 text-sm'
const NEW_TYPE = '__new__'
const fail = (caught: unknown) => (caught instanceof Error ? caught : new Error('알 수 없는 오류'))

/** 어댑터 종류마다 맨 위에 둘 칸 — 나머지는 「더 보기」. */
const MAIN: Record<string, string[]> = {
  rest: ['base_url', 'path', 'rows_path', 'paging', 'since_param', 'updated_at', 'deleted', 'explode'],
  odata: ['service_url', 'entity_set', 'version', 'modified_field', 'deleted', 'expand', 'explode'],
  xml: ['url', 'rows_xpath', 'namespaces', 'since_param', 'deleted'],
  file: ['location', 'format', 'sheet', 'rows_path', 'deleted', 'explode'],
}

function Step({ n, title, children }: { n: number; title: string; children: ReactNode }) {
  return (
    <section className="space-y-3 rounded-md border p-4">
      <h2 className="text-base font-semibold">
        {n}. {title}
      </h2>
      {children}
    </section>
  )
}

function Labeled({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="grid gap-1 text-sm sm:grid-cols-[11rem_1fr] sm:items-start">
      <span className="text-muted-foreground pt-2">{label}</span>
      <span className="space-y-0.5">
        {children}
        {hint && <span className="text-muted-foreground block text-xs">{hint}</span>}
      </span>
    </label>
  )
}

function FieldInput({
  field,
  value,
  onChange,
}: {
  field: AdapterField
  value: string
  onChange: (next: string) => void
}) {
  const json = field.type.includes('Mapping')
  const placeholder =
    field.default === null || field.default === undefined || field.default === ''
      ? ''
      : typeof field.default === 'string'
        ? field.default
        : JSON.stringify(field.default)
  return (
    <Labeled label={`${field.name}${field.required ? ' *' : ''}`} hint={field.doc.split('\n')[0]}>
      {json ? (
        <Textarea
          aria-label={field.name}
          className="font-mono text-xs"
          rows={2}
          value={value}
          placeholder={placeholder}
          onChange={(event) => onChange(event.target.value)}
        />
      ) : (
        <Input
          aria-label={field.name}
          className="font-mono text-xs"
          value={value}
          placeholder={placeholder}
          onChange={(event) => onChange(event.target.value)}
        />
      )}
    </Labeled>
  )
}

export default function NewConnectionPage() {
  const { user } = useAuth()
  const kinds = useResource(() => relayApi.adapterKinds(), [])
  const [kind, setKind] = useState('rest')
  const [values, setValues] = useState<Record<string, string>>({})
  const [more, setMore] = useState(false)
  const [auth, setAuth] = useState<AuthValues>({ kind: 'none', token_env: '' })
  const [found, setFound] = useState<DiscoverOut | null>(null)
  const [picks, setPicks] = useState<Picked[]>([])
  const [meta, setMeta] = useState<DefinitionForm>({
    slug: '',
    source: '',
    system: '',
    target: { name: '', server: '', token_env: '' },
    schedule: { kind: 'manual', at: '01:00', minutes: '60' },
  })
  const [savedDefinition, setSavedDefinition] = useState<DefinitionSaved | null>(null)
  const [schema, setSchema] = useState<TargetSchema | null>(null)
  const [typeSlug, setTypeSlug] = useState('')
  const [newType, setNewType] = useState({ slug: '', label: '' })
  const [labelColumn, setLabelColumn] = useState('')
  const [fieldMap, setFieldMap] = useState<Record<string, string>>({})
  const [typeDraft, setTypeDraft] = useState<TypeDraft | null>(null)
  const [savedRecipe, setSavedRecipe] = useState<RecipeSaved | null>(null)
  const [trial, setTrial] = useState<DefinitionTrial | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<Error | null>(null)

  const spec = kinds.data?.kinds.find((one) => one.kind === kind)
  const fields = useMemo(() => (spec?.fields ?? []).filter((one) => !SPECIAL.has(one.name)), [spec])
  const main = fields.filter((one) => MAIN[kind]?.includes(one.name) || one.required)
  const rest = fields.filter((one) => !main.includes(one))
  const chosen = picks.filter((one) => one.include)
  const keyColumns = picks.filter((one) => one.key).map((one) => one.column)
  const form = { kind, fields: spec?.fields ?? [], values, auth, picks }

  if (!isSystemAdmin(user)) {
    return (
      <div className="space-y-4">
        <PageHeader title="새 연결" back={BACK} />
        <p className="text-muted-foreground text-sm">새 연결은 시스템 관리자가 만듭니다.</p>
      </div>
    )
  }

  async function step(name: string, work: () => Promise<void>) {
    setBusy(name)
    setError(null)
    try {
      await work()
    } catch (caught) {
      setError(fail(caught))
    } finally {
      setBusy(null)
    }
  }

  const explore = () =>
    step('discover', async () => {
      const got = await relayApi.discover(adapterOf(form, true))
      setFound(got)
      if (got.found?.paths) setPicks(picksOf(got.found))
    })

  const saveDefinition = () =>
    step('definition', async () => {
      const body = definitionOf(meta, adapterOf(form))
      const saved = await relayApi.saveDefinition(meta.slug.trim(), body, '새 연결')
      setSavedDefinition(saved)
      setSchema(await relayApi.targetSchema(meta.slug.trim()))
    })

  const draftType = () =>
    step('type', async () => {
      const columns: Record<string, string[]> = {}
      for (const one of chosen) columns[one.column] = [one.sample]
      setTypeDraft(
        await relayApi.typeDraft({
          type_slug: newType.slug.trim(),
          label: newType.label.trim(),
          key_column: keyColumns[0] ?? '',
          label_column: labelColumn,
          columns,
          system: meta.system,
        }),
      )
    })

  const saveRecipe = () =>
    step('recipe', async () => {
      let body: Record<string, unknown>
      if (typeSlug === NEW_TYPE) {
        if (!typeDraft) throw new Error('먼저 「타입 정의 초안 작성」 을 클릭하세요.')
        body = typeDraft.recipe
      } else {
        const props = schema?.types[typeSlug] ?? {}
        const dates = new Set(
          Object.values(props)
            .filter((one) => one.data_type === 'date')
            .map((one) => one.key),
        )
        // 여러 칸 식별자면 계산 칸 없이는 한 열이 아니다 — 첫 칸을 쓰고 레시피에서 다듬는다.
        body = recipeOf(typeSlug, keyColumns[0] ?? '', labelColumn, fieldMap, dates)
      }
      setSavedRecipe(await relayApi.saveRecipe(meta.slug.trim(), body, '새 연결 — 첫 판'))
    })

  const runTrial = () =>
    step('trial', async () => {
      if (!savedDefinition) return
      setTrial(
        await relayApi.testDefinition(meta.slug.trim(), savedDefinition.version, {
          recipe_version: savedRecipe?.version,
        }),
      )
    })

  function download() {
    if (!typeDraft) return
    const blob = new Blob([JSON.stringify(typeDraft.ontology, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `${newType.slug || 'type'}.ontology.json`
    anchor.click()
    setTimeout(() => URL.revokeObjectURL(url), 10_000)
  }

  const setPick = (index: number, patch: Partial<Picked>) =>
    setPicks((before) => before.map((one, at) => (at === index ? { ...one, ...patch } : one)))

  return (
    <div className="space-y-4">
      <PageHeader
        title="새 연결"
        back={BACK}
        description="원천의 주소와 모양을 받아 대상 SP 로 중계하는 파이프라인을 만듭니다. 사용은 파이프라인 상세에서 시험 결과를 읽고 합니다."
      />
      <ErrorNotice error={kinds.error} />

      <Step n={1} title="원천">
        <Labeled label="어댑터">
          <select
            aria-label="어댑터 종류"
            className={SELECT}
            value={kind}
            onChange={(event) => {
              setKind(event.target.value)
              setFound(null)
              setPicks([])
            }}
          >
            {(kinds.data?.kinds ?? []).map((one) => (
              <option key={one.kind} value={one.kind}>
                {one.kind}
              </option>
            ))}
          </select>
        </Labeled>
        {main.map((field) => (
          <FieldInput
            key={field.name}
            field={field}
            value={values[field.name] ?? ''}
            onChange={(next) => setValues((before) => ({ ...before, [field.name]: next }))}
          />
        ))}
        <Button size="sm" variant="ghost" onClick={() => setMore(!more)}>
          {more ? '칸 접기' : `칸 더 보기(${rest.length}개 — 쪽 넘김 · 상세 호출 · POST 본문 · 호출 간격 …)`}
        </Button>
        {more &&
          rest.map((field) => (
            <FieldInput
              key={field.name}
              field={field}
              value={values[field.name] ?? ''}
              onChange={(next) => setValues((before) => ({ ...before, [field.name]: next }))}
            />
          ))}
        <Labeled label="인증">
          <select
            aria-label="인증 방식"
            className={SELECT}
            value={auth.kind}
            onChange={(event) => setAuth({ ...auth, kind: event.target.value as AuthValues['kind'] })}
          >
            {AUTH_KINDS.map((one) => (
              <option key={one} value={one}>
                {AUTH_LABEL[one]}
              </option>
            ))}
          </select>
        </Labeled>
        {auth.kind !== 'none' && (
          <Labeled label="비밀의 환경 변수 이름" hint="값은 서버의 .env 에 둡니다 — 여기에는 이름만(예: MES_API_TOKEN).">
            <Input
              aria-label="비밀의 환경 변수 이름"
              className="font-mono text-xs"
              value={auth.token_env}
              onChange={(event) => setAuth({ ...auth, token_env: event.target.value })}
            />
          </Labeled>
        )}
        {AUTH_EXTRA[auth.kind].map((extra) => (
          <Labeled key={extra.name} label={extra.label}>
            <Input
              aria-label={extra.label}
              className="font-mono text-xs"
              value={auth[extra.name] ?? ''}
              onChange={(event) => setAuth({ ...auth, [extra.name]: event.target.value })}
            />
          </Labeled>
        ))}
        <Button size="sm" onClick={() => void explore()} disabled={busy !== null}>
          {busy === 'discover' ? <Loader2 className="animate-spin" /> : <Search />}
          탐색
        </Button>
        {found && (
          <div className="space-y-1 text-sm">
            {found.probe && <p className="text-muted-foreground text-xs">{found.probe}</p>}
            {(found.error || found.probe_error) && (
              <p className="text-destructive text-xs">{found.error || found.probe_error}</p>
            )}
            {(found.found?.entity_sets ?? []).length > 0 && !values.entity_set && (
              <div className="flex flex-wrap gap-1">
                <span className="text-muted-foreground text-xs">엔티티 셋:</span>
                {(found.found?.entity_sets ?? []).map((one) => (
                  <Button
                    key={one.entity_set}
                    size="sm"
                    variant="outline"
                    className="h-auto px-2 py-0.5 text-xs"
                    onClick={() => setValues((before) => ({ ...before, entity_set: one.entity_set }))}
                  >
                    {one.entity_set}
                  </Button>
                ))}
              </div>
            )}
          </div>
        )}
      </Step>

      {picks.length > 0 && (
        <Step n={2} title="칸 — 레시피가 읽을 열 · 식별자">
          <p className="text-muted-foreground text-xs">
            식별자는 안 바뀌는 칸입니다 — 여러 개를 선택하면 값을 `|` 로 이은 여러 칸 식별자가 됩니다(목록을 펼쳤으면
            원소의 칸을 꼭 포함).
          </p>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-14">포함</TableHead>
                <TableHead className="w-16">식별자</TableHead>
                <TableHead>원천의 자리</TableHead>
                <TableHead>보기 값</TableHead>
                <TableHead>열 이름</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {picks.map((one, index) => (
                <TableRow key={one.path}>
                  <TableCell>
                    <input
                      type="checkbox"
                      aria-label={`${one.path} 포함`}
                      checked={one.include}
                      onChange={(event) => setPick(index, { include: event.target.checked })}
                    />
                  </TableCell>
                  <TableCell>
                    <input
                      type="checkbox"
                      aria-label={`${one.path} 식별자`}
                      checked={one.key}
                      onChange={(event) => setPick(index, { key: event.target.checked, include: true })}
                    />
                  </TableCell>
                  <TableCell className="font-mono text-xs">{one.path}</TableCell>
                  <TableCell className="text-muted-foreground max-w-xs truncate text-xs">{one.sample}</TableCell>
                  <TableCell>
                    <Input
                      aria-label={`${one.path} 열 이름`}
                      className="h-8 font-mono text-xs"
                      value={one.column}
                      onChange={(event) => setPick(index, { column: event.target.value })}
                    />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Step>
      )}

      {picks.length > 0 && (
        <Step n={3} title="대상 · 이름 · 일정">
          <Labeled label="파이프라인 slug *" hint="소문자 · 숫자 · '-' (예: mes-lots)">
            <Input
              aria-label="파이프라인 slug"
              className="font-mono text-xs"
              value={meta.slug}
              onChange={(event) => setMeta({ ...meta, slug: event.target.value })}
            />
          </Labeled>
          <Labeled label="원천 이름 *" hint="대상 타입의 관리 주인(source) — plm · mes …">
            <Input
              aria-label="원천 이름"
              className="font-mono text-xs"
              value={meta.source}
              onChange={(event) => setMeta({ ...meta, source: event.target.value })}
            />
          </Labeled>
          <Labeled label="원천 시스템" hint="화면에 쓸 이름(예: MES)">
            <Input
              aria-label="원천 시스템"
              value={meta.system}
              onChange={(event) => setMeta({ ...meta, system: event.target.value })}
            />
          </Labeled>
          <Labeled label="대상 SP 주소 *">
            <Input
              aria-label="대상 SP 주소"
              className="font-mono text-xs"
              value={meta.target.server}
              onChange={(event) => setMeta({ ...meta, target: { ...meta.target, server: event.target.value } })}
            />
          </Labeled>
          <Labeled label="대상 이름">
            <Input
              aria-label="대상 이름"
              value={meta.target.name}
              onChange={(event) => setMeta({ ...meta, target: { ...meta.target, name: event.target.value } })}
            />
          </Labeled>
          <Labeled label="대상 토큰의 환경 변수 *" hint="값은 서버의 .env 에(예: HUB_TOKEN).">
            <Input
              aria-label="대상 토큰의 환경 변수"
              className="font-mono text-xs"
              value={meta.target.token_env}
              onChange={(event) =>
                setMeta({ ...meta, target: { ...meta.target, token_env: event.target.value } })
              }
            />
          </Labeled>
          <Labeled label="일정">
            <span className="flex flex-wrap items-center gap-2">
              <select
                aria-label="일정"
                className={SELECT}
                value={meta.schedule.kind}
                onChange={(event) =>
                  setMeta({ ...meta, schedule: { ...meta.schedule, kind: event.target.value as DefinitionForm['schedule']['kind'] } })
                }
              >
                <option value="manual">수동</option>
                <option value="daily">매일</option>
                <option value="every">N분마다</option>
              </select>
              {meta.schedule.kind === 'daily' && (
                <Input
                  aria-label="매일 시각"
                  className="h-9 w-24"
                  value={meta.schedule.at}
                  onChange={(event) => setMeta({ ...meta, schedule: { ...meta.schedule, at: event.target.value } })}
                />
              )}
              {meta.schedule.kind === 'every' && (
                <Input
                  aria-label="분 간격"
                  className="h-9 w-24"
                  value={meta.schedule.minutes}
                  onChange={(event) =>
                    setMeta({ ...meta, schedule: { ...meta.schedule, minutes: event.target.value } })
                  }
                />
              )}
            </span>
          </Labeled>
          <Button size="sm" onClick={() => void saveDefinition()} disabled={busy !== null}>
            {busy === 'definition' ? <Loader2 className="animate-spin" /> : <Save />}
            정의 판 초안 저장
          </Button>
          {savedDefinition && (
            <Alert>
              <AlertTitle>정의 판 {savedDefinition.version} 초안을 저장했습니다</AlertTitle>
              <AlertDescription>
                사용하기 전에는 실행되지 않습니다.
                {savedDefinition.problems.length > 0 && (
                  <ul className="list-disc pl-4">
                    {savedDefinition.problems.map((one) => (
                      <li key={one}>{one}</li>
                    ))}
                  </ul>
                )}
              </AlertDescription>
            </Alert>
          )}
        </Step>
      )}

      {savedDefinition && schema && (
        <Step n={4} title="레시피 — 대상 타입의 칸에 잇기">
          {schema.note && <p className="text-muted-foreground text-xs">{schema.note}</p>}
          <Labeled label="대상 타입">
            <select
              aria-label="대상 타입"
              className={SELECT}
              value={typeSlug}
              onChange={(event) => {
                setTypeSlug(event.target.value)
                setFieldMap({})
              }}
            >
              <option value="">선택</option>
              {Object.keys(schema.types)
                .sort()
                .map((one) => (
                  <option key={one} value={one}>
                    {one}
                  </option>
                ))}
              <option value={NEW_TYPE}>대상에 없는 새 타입 — 타입 정의 초안 작성</option>
            </select>
          </Labeled>
          <Labeled label="이름(label) 열">
            <select
              aria-label="이름 열"
              className={SELECT}
              value={labelColumn}
              onChange={(event) => setLabelColumn(event.target.value)}
            >
              <option value="">식별자와 같음</option>
              {chosen.map((one) => (
                <option key={one.column} value={one.column}>
                  {one.column}
                </option>
              ))}
            </select>
          </Labeled>
          {typeSlug && typeSlug !== NEW_TYPE && (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>대상의 칸</TableHead>
                  <TableHead>자료형</TableHead>
                  <TableHead>원천의 열</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {Object.values(schema.types[typeSlug] ?? {}).map((prop) => (
                  <TableRow key={prop.key}>
                    <TableCell>
                      <span className="font-mono text-xs">{prop.key}</span>
                      {prop.label && <span className="text-muted-foreground ml-1 text-xs">{prop.label}</span>}
                    </TableCell>
                    <TableCell className="text-xs">{prop.data_type}</TableCell>
                    <TableCell>
                      <select
                        aria-label={`${prop.key} 의 열`}
                        className={SELECT}
                        value={fieldMap[prop.key] ?? ''}
                        onChange={(event) => setFieldMap((before) => ({ ...before, [prop.key]: event.target.value }))}
                      >
                        <option value="">보내지 않음</option>
                        {chosen.map((one) => (
                          <option key={one.column} value={one.column}>
                            {one.column}
                          </option>
                        ))}
                      </select>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
          {typeSlug === NEW_TYPE && (
            <div className="space-y-2">
              <Labeled label="새 타입 slug" hint="소문자 · 숫자 · '_' (예: mes_lot)">
                <Input
                  aria-label="새 타입 slug"
                  className="font-mono text-xs"
                  value={newType.slug}
                  onChange={(event) => setNewType({ ...newType, slug: event.target.value })}
                />
              </Labeled>
              <Labeled label="새 타입 이름">
                <Input
                  aria-label="새 타입 이름"
                  value={newType.label}
                  onChange={(event) => setNewType({ ...newType, label: event.target.value })}
                />
              </Labeled>
              <div className="flex flex-wrap items-center gap-2">
                <Button size="sm" variant="outline" onClick={() => void draftType()} disabled={busy !== null}>
                  {busy === 'type' && <Loader2 className="animate-spin" />}
                  타입 정의 초안 작성
                </Button>
                {typeDraft && (
                  <Button size="sm" variant="outline" onClick={download}>
                    <Download />
                    타입 정의 초안 다운로드
                  </Button>
                )}
              </div>
              {typeDraft && (
                <p className="text-muted-foreground text-xs">
                  칸 {typeDraft.ontology.types[0]?.properties.length ?? 0}개 —{' '}
                  {(typeDraft.ontology.types[0]?.properties ?? []).map((one) => `${one.key}(${one.data_type})`).join(' · ')}.
                  relay 는 대상의 타입을 만들지 않습니다 — 이 파일을 SP 관리자에게 넘겨 대상에 등록한 뒤 시험 · 사용하세요.
                </p>
              )}
            </div>
          )}
          <Button size="sm" onClick={() => void saveRecipe()} disabled={busy !== null || !typeSlug}>
            {busy === 'recipe' ? <Loader2 className="animate-spin" /> : <Save />}
            레시피 판 초안 저장
          </Button>
          {savedRecipe && (
            <p className="text-sm">
              레시피 판 {savedRecipe.version} 초안을 저장했습니다.
              {savedRecipe.problems.length > 0 && ` 알아 둘 것: ${savedRecipe.problems.join(' · ')}`}
            </p>
          )}
        </Step>
      )}

      {savedDefinition && (
        <Step n={5} title="시험 — 대상에는 아무것도 보내지 않습니다">
          <div className="flex flex-wrap items-center gap-3">
            <Button size="sm" variant="outline" onClick={() => void runTrial()} disabled={busy !== null}>
              {busy === 'trial' ? <Loader2 className="animate-spin" /> : <TestTube />}
              정의 시험
            </Button>
            <Link to={`/relay/pipelines/${encodeURIComponent(meta.slug.trim())}`} className="text-sm underline">
              파이프라인 상세로 — 정의 판 · 레시피 판을 사용하면 실행됩니다
            </Link>
          </div>
          {trial && <DefinitionTrialView trial={trial} />}
        </Step>
      )}

      <ErrorNotice error={error} />
    </div>
  )
}
