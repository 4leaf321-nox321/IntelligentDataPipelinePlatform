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

export const AUTH_KINDS = ['none', 'bearer', 'header', 'basic', 'query', 'oauth2', 'ntlm', 'sasl'] as const
export type AuthKind = (typeof AUTH_KINDS)[number]

/**
 * 어댑터 종류마다 고를 수 있는 인증 — Kafka 는 평문 · SASL · OAuth2(OAUTHBEARER)만, 나머지(HTTP ·
 * 파일)는 SASL 을 뺀 것. 서버도 같은 것을 거절한다(`relay_core.definition.KAFKA_AUTH`).
 */
export function authKindsFor(adapterKind: string): readonly AuthKind[] {
  if (adapterKind === 'kafka') return ['none', 'sasl', 'oauth2']
  return AUTH_KINDS.filter((one) => one !== 'sasl')
}

export const AUTH_LABEL: Record<AuthKind, string> = {
  none: '없음',
  bearer: 'Bearer 토큰',
  header: '사용자 지정 헤더',
  basic: '계정 · 비밀번호(basic)',
  query: '쿼리 문자열 키',
  oauth2: 'OAuth2 client credentials',
  ntlm: 'Windows 통합 인증(NTLM)',
  sasl: 'SASL 계정 · 비밀번호(Kafka)',
}

/** 인증 방식마다 더 적을 칸. */
export const AUTH_EXTRA: Record<AuthKind, { name: string; label: string }[]> = {
  none: [],
  bearer: [],
  header: [{ name: 'header', label: '헤더 이름' }],
  basic: [{ name: 'user', label: '계정' }],
  query: [{ name: 'param', label: '쿼리 파라미터 이름(예: api_key)' }],
  oauth2: [
    { name: 'token_url', label: '토큰 주소' },
    { name: 'user', label: 'client_id' },
    { name: 'scope', label: '범위(scope)' },
  ],
  ntlm: [{ name: 'user', label: '계정(CORP\\svc_relay)' }],
  sasl: [
    { name: 'mechanism', label: '방식(PLAIN, SCRAM-SHA-256, SCRAM-SHA-512)' },
    { name: 'user', label: '계정' },
  ],
}

/**
 * 어느 방식과도 함께 적는 클라이언트 인증서(mTLS) — 서버의 인증서 폴더(`RELAY_CERT_DIR`) 안의 **파일
 * 이름**과, 키에 암호가 있으면 그 암호를 담은 환경 변수의 이름.
 */
export const AUTH_CERT: { name: string; label: string }[] = [
  { name: 'cert', label: '클라이언트 인증서 파일 이름(mTLS)' },
  { name: 'cert_password_env', label: '인증서 키 암호의 환경 변수' },
]

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
    if (!Number.isFinite(number)) throw new Error(`${field.name}: 숫자를 입력하세요(입력값 ${text})`)
    return number
  }
  if (field.type === 'bool') return text === 'true'
  if (field.type.includes('Mapping')) {
    try {
      return JSON.parse(text)
    } catch {
      throw new Error(`${field.name}: JSON 형식으로 입력하세요(예: {"a": "b"}). 입력값: ${text}`)
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
  const cert = AUTH_CERT.filter((one) => (form.auth[one.name] ?? '').trim())
  if (form.auth.kind !== 'none' || cert.length > 0) {
    const auth: Record<string, string> = { kind: form.auth.kind }
    if (form.auth.kind !== 'none' && form.auth.token_env.trim()) auth.token_env = form.auth.token_env.trim()
    for (const extra of [...AUTH_EXTRA[form.auth.kind], ...cert]) {
      const value = (form.auth[extra.name] ?? '').trim()
      if (value) auth[extra.name] = value
    }
    out.auth = auth
  }
  if (partial) return out
  const keys = form.picks.filter((one) => one.key).map((one) => one.path)
  if (keys.length === 0) throw new Error('식별자로 사용할 필드를 하나 이상 선택하세요.')
  out.key = keys.length === 1 ? keys[0] : keys
  const columns: Record<string, string> = {}
  for (const one of form.picks.filter((pick) => pick.include)) {
    const name = one.column.trim()
    if (!name) throw new Error(`${one.path}: 열 이름을 입력하세요.`)
    if (columns[name]) throw new Error(`열 이름이 중복됩니다: ${name}`)
    columns[name] = one.path
  }
  if (Object.keys(columns).length === 0) throw new Error('레시피가 읽을 필드를 하나 이상 선택하세요.')
  out.columns = columns
  return out
}

/** 여러 칸 식별자의 계산 칸 이름 — 어댑터의 열과 겹치면 뒤에 수를 붙인다. */
export const KEY_COLUMN = 'RELAY_KEY'

export interface KeyColumn {
  /** 레시피 · 타입 초안이 식별자로 읽을 열. */
  column: string
  /** 표본 값(타입 초안에). */
  sample: string
  /** 여러 칸 식별자면 그 열을 만드는 정의의 `computed` — 한 칸이면 없다. */
  computed?: Record<string, { template: string }>
}

/**
 * 레시피가 식별자로 읽을 열. 한 칸이면 그 열 그대로. **여러 칸이면 레시피가 읽을 한 열이 없다** —
 * 값을 `|` 로 잇는 계산 칸(`{A}|{B}`)을 정의에 더하고 그 열을 쓴다(미러의 식별자와 같은 모양).
 */
export function keyColumnOf(picks: Picked[]): KeyColumn | null {
  const keys = picks.filter((one) => one.key)
  if (keys.length === 0) return null
  if (keys.length === 1) return { column: keys[0].column.trim(), sample: keys[0].sample }
  for (const one of keys) {
    if (/[{}]/.test(one.column)) throw new Error(`식별자 열 이름에는 { }를 사용할 수 없습니다: ${one.column}`)
  }
  const taken = new Set(picks.filter((one) => one.include).map((one) => one.column.trim()))
  let column = KEY_COLUMN
  for (let n = 2; taken.has(column); n += 1) column = `${KEY_COLUMN}_${n}`
  return {
    column,
    sample: keys.map((one) => one.sample).join('|'),
    computed: { [column]: { template: keys.map((one) => `{${one.column.trim()}}`).join('|') } },
  }
}

export type ScheduleKind = 'manual' | 'daily' | 'every'

export interface DefinitionForm {
  slug: string
  source: string
  system: string
  target: { name: string; server: string; token_env: string }
  schedule: { kind: ScheduleKind; at: string; minutes: string }
}

export function definitionOf(
  form: DefinitionForm,
  adapter: Record<string, unknown>,
  computed?: Record<string, unknown>,
): Record<string, unknown> {
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
  if (computed && Object.keys(computed).length > 0) body.computed = computed
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
