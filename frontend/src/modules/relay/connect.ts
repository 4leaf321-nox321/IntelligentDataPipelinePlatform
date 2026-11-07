/**
 * 새 연결 — 화면의 입력을 정의(`relay-pipeline/1`) · 레시피(`sp-table/1`)로 옮기는 규칙. 화면
 * (`NewConnectionPage`)은 그리기만 하고, 모양은 여기서 정한다(시험이 여기를 본다).
 *
 * 어댑터의 칸은 서버가 정의 모양에서 계산해 준 것(`/relay/adapter-kinds`)을 따른다 — 서버에 칸이
 * 늘면 화면에도 저절로 선다. 비밀은 **환경 변수의 이름**(`token_env`)만 받는다.
 */

import type { AdapterField, DiscoverOut } from '@/modules/relay/api'

/** 입력 칸이 따로 있는 것 — 일반 칸 목록에서 뺀다. */
export const SPECIAL = new Set(['key', 'columns', 'auth'])

export const AUTH_KINDS = ['none', 'bearer', 'header', 'basic', 'query', 'oauth2'] as const
export type AuthKind = (typeof AUTH_KINDS)[number]

export const AUTH_LABEL: Record<AuthKind, string> = {
  none: '없음',
  bearer: 'Bearer 토큰',
  header: '머리글 하나',
  basic: '계정 · 비밀번호(basic)',
  query: '질의 문자열의 키',
  oauth2: 'OAuth2 client credentials',
}

/** 인증 방식마다 더 적을 칸. */
export const AUTH_EXTRA: Record<AuthKind, { name: string; label: string }[]> = {
  none: [],
  bearer: [],
  header: [{ name: 'header', label: '머리글 이름' }],
  basic: [{ name: 'user', label: '계정' }],
  query: [{ name: 'param', label: '질의 이름(예: api_key)' }],
  oauth2: [
    { name: 'token_url', label: '토큰 주소' },
    { name: 'user', label: 'client_id' },
    { name: 'scope', label: '범위(scope)' },
  ],
}

export interface AuthValues {
  kind: AuthKind
  token_env: string
  [extra: string]: string
}

/** 탐색이 찾은 칸 하나 — 레시피가 읽을 열로 넣을지 · 그 이름 · 식별자인지. */
export interface Picked {
  path: string
  sample: string
  column: string
  include: boolean
  key: boolean
}

/** 어댑터 칸의 입력(글) → 정의의 값. 비면 안 적는다(서버의 기본값). */
export function valueOf(field: AdapterField, raw: string): unknown {
  const text = raw.trim()
  if (!text) return undefined
  if (field.type === 'int' || field.type === 'float') {
    const number = Number(text)
    if (!Number.isFinite(number)) throw new Error(`${field.name} 은 수입니다: ${text}`)
    return number
  }
  if (field.type === 'bool') return text === 'true'
  if (field.type.includes('Mapping')) {
    try {
      return JSON.parse(text)
    } catch {
      throw new Error(`${field.name} 은 JSON 입니다(예: {"a": "b"}): ${text}`)
    }
  }
  return text
}

/** 탐색 결과 → 고를 칸들. 서버의 열 이름 제안(`TASK_CODE` ← `task.code`)을 기본으로. */
export function picksOf(found: DiscoverOut['found'], keyHint = ''): Picked[] {
  const names = new Map(Object.entries(found?.columns ?? {}).map(([column, path]) => [path, column]))
  return (found?.paths ?? []).map((one) => ({
    path: one.path,
    sample: one.sample,
    column: names.get(one.path) ?? one.path,
    include: true,
    key: keyHint !== '' && one.path === keyHint,
  }))
}

export interface AdapterForm {
  kind: string
  fields: AdapterField[]
  values: Record<string, string>
  auth: AuthValues
  picks: Picked[]
}

/** 어댑터 설정 — `partial` 이면 탐색용(식별자 · 열을 아직 모른다). */
export function adapterOf(form: AdapterForm, partial = false): Record<string, unknown> {
  const out: Record<string, unknown> = { kind: form.kind }
  for (const field of form.fields) {
    if (SPECIAL.has(field.name)) continue
    const value = valueOf(field, form.values[field.name] ?? '')
    if (value !== undefined) out[field.name] = value
  }
  if (form.auth.kind !== 'none') {
    const auth: Record<string, string> = { kind: form.auth.kind }
    if (form.auth.token_env.trim()) auth.token_env = form.auth.token_env.trim()
    for (const extra of AUTH_EXTRA[form.auth.kind]) {
      const value = (form.auth[extra.name] ?? '').trim()
      if (value) auth[extra.name] = value
    }
    out.auth = auth
  }
  if (partial) return out
  const keys = form.picks.filter((one) => one.key).map((one) => one.path)
  if (keys.length === 0) throw new Error('식별자로 쓸 칸을 하나 이상 선택하세요.')
  out.key = keys.length === 1 ? keys[0] : keys
  const columns: Record<string, string> = {}
  for (const one of form.picks.filter((pick) => pick.include)) {
    const name = one.column.trim()
    if (!name) throw new Error(`${one.path} 의 열 이름이 비었습니다.`)
    if (columns[name]) throw new Error(`열 이름 ${name} 이 둘입니다.`)
    columns[name] = one.path
  }
  if (Object.keys(columns).length === 0) throw new Error('레시피가 읽을 칸을 하나 이상 선택하세요.')
  out.columns = columns
  return out
}

export type ScheduleKind = 'manual' | 'daily' | 'every'

export interface DefinitionForm {
  slug: string
  source: string
  system: string
  target: { name: string; server: string; token_env: string }
  schedule: { kind: ScheduleKind; at: string; minutes: string }
}

export function definitionOf(form: DefinitionForm, adapter: Record<string, unknown>): Record<string, unknown> {
  const body: Record<string, unknown> = {
    format: 'relay-pipeline/1',
    slug: form.slug.trim(),
    source: form.source.trim(),
    system: form.system.trim() || form.source.trim().toUpperCase(),
    adapter,
    target: {
      name: form.target.name.trim(),
      server: form.target.server.trim().replace(/\/+$/, ''),
      token_env: form.target.token_env.trim(),
    },
  }
  if (form.schedule.kind === 'daily') body.schedule = { daily_at: form.schedule.at }
  if (form.schedule.kind === 'every') body.schedule = { every_minutes: Number(form.schedule.minutes) }
  return body
}

/** 레시피의 첫 판 — 타입 하나: 식별자 · 이름 · 칸마다 열. 날짜 칸은 날짜로 바꾼다. */
export function recipeOf(
  typeSlug: string,
  keyColumn: string,
  labelColumn: string,
  fields: Record<string, string>,
  dates: Set<string>,
): Record<string, unknown> {
  const specs: Record<string, unknown> = {}
  for (const [prop, column] of Object.entries(fields)) {
    if (!column) continue
    specs[prop] = dates.has(prop) ? { column, date: true } : { column }
  }
  return {
    format: 'sp-table/1',
    title: `${typeSlug}(새 연결)`,
    types: [
      {
        type_slug: typeSlug,
        key: { column: keyColumn },
        label: { column: labelColumn || keyColumn },
        fields: specs,
      },
    ],
  }
}
