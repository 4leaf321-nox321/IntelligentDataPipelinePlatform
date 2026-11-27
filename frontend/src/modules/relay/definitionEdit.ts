/**
 * 정의 판 편집의 규칙 — 화면(`DefinitionEditor`)은 그리기만 하고, 정의(`relay-pipeline/1`)의 칸을 경로
 * (`['adapter', 'auth', 'kind']`)로 읽고 쓰는 것 · 출발 판에서 바뀐 칸 · 저장 전에 막을 것은 여기서 정한다
 * (시험이 여기를 본다). 모양의 최종 판정은 서버다(`definitions._check`) — 여기는 시험까지 가기 전에 말할
 * 것만.
 */

import type { AdapterField } from '@/modules/relay/api'
import { AUTH_CERT, AUTH_EXTRA, AUTH_KINDS } from '@/modules/relay/connect'
import type { AuthKind } from '@/modules/relay/connect'

export type DefinitionDoc = Record<string, unknown>

export const clone = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T

export const isPlain = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

/** 키 차례와 상관없이 같은 값인가. */
function stable(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stable).join(',')}]`
  if (isPlain(value)) {
    return `{${Object.keys(value)
      .filter((key) => value[key] !== undefined)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${stable(value[key])}`)
      .join(',')}}`
  }
  return JSON.stringify(value) ?? 'undefined'
}

export const same = (a: unknown, b: unknown) => stable(a) === stable(b)

/** 경로의 값 — 중간이 객체가 아니면 없다. */
export function at(doc: unknown, path: string[]): unknown {
  let here: unknown = doc
  for (const part of path) {
    if (!isPlain(here)) return undefined
    here = here[part]
  }
  return here
}

/**
 * 경로의 값을 바꾼 새 본문 — `undefined` 면 그 칸을 지우고, 그래서 비게 된 객체도 지운다(빈 `auth: {}` ·
 * `computed: {}` 를 남기지 않는다). 본문은 고치지 않는다.
 */
export function withAt(doc: DefinitionDoc, path: string[], value: unknown): DefinitionDoc {
  const out = clone(doc)
  const chain: Record<string, unknown>[] = [out]
  let here: Record<string, unknown> = out
  for (const part of path.slice(0, -1)) {
    if (!isPlain(here[part])) {
      if (value === undefined) return out
      here[part] = {}
    }
    here = here[part] as Record<string, unknown>
    chain.push(here)
  }
  const last = path[path.length - 1]
  if (value === undefined) delete here[last]
  else here[last] = value
  for (let depth = path.length - 1; depth > 0; depth -= 1) {
    if (Object.keys(chain[depth]).length > 0) break
    delete chain[depth - 1][path[depth - 1]]
  }
  return out
}

/**
 * 출발 판에서 바뀐 칸의 경로 — 객체는 안으로 들어가 견주고(`depth` 겹까지: `adapter.auth.kind`), 목록 ·
 * 값은 통째로. 칸 하나 고치려다 다른 데를 건드린 것이 저장 전에 보인다.
 */
export function changes(before: unknown, after: unknown, prefix = '', depth = 3): string[] {
  if (same(before, after)) return []
  const label = prefix || '원문'
  if (depth === 0 || !isPlain(before) || !isPlain(after)) {
    if (before === undefined) return [`${label}(추가)`]
    if (after === undefined) return [`${label}(삭제)`]
    return [label]
  }
  const keys = [...new Set([...Object.keys(before), ...Object.keys(after)])]
  return keys.flatMap((key) => changes(before[key], after[key], prefix ? `${prefix}.${key}` : key, depth - 1))
}

/** 어댑터 칸의 값 → 입력 글. 객체 · 목록은 JSON 으로. */
export function textOf(value: unknown): string {
  if (value === undefined || value === null) return ''
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

/** 식별자 — 한 칸이면 글, 여럿(`,` 로 나눔)이면 목록. */
export function keyOf(text: string): string | string[] | undefined {
  const parts = text
    .split(',')
    .map((one) => one.trim())
    .filter(Boolean)
  if (parts.length === 0) return undefined
  return parts.length === 1 ? parts[0] : parts
}

export const keyText = (value: unknown) => (Array.isArray(value) ? value.join(', ') : textOf(value))

/** 인증 방식을 바꾼다 — 비밀의 환경 변수 · 인증서는 남기고, 앞 방식에만 있던 칸은 지운다. */
export function withAuthKind(auth: Record<string, unknown> | undefined, kind: AuthKind): Record<string, unknown> | undefined {
  const keep = new Set(['token_env', ...AUTH_CERT.map((one) => one.name), ...AUTH_EXTRA[kind].map((one) => one.name)])
  const out: Record<string, unknown> = { kind }
  for (const [name, value] of Object.entries(auth ?? {})) {
    if (name !== 'kind' && keep.has(name)) out[name] = value
  }
  if (kind === 'none') delete out.token_env
  return kind === 'none' && Object.keys(out).length === 1 ? undefined : out
}

export const authKindOf = (value: unknown): AuthKind => {
  const kind = String(at(value, ['kind']) ?? 'none')
  return (AUTH_KINDS as readonly string[]).includes(kind) ? (kind as AuthKind) : 'none'
}

export type ScheduleKind = 'manual' | 'daily' | 'every'

export function scheduleKindOf(schedule: unknown): ScheduleKind {
  if (isPlain(schedule) && schedule.daily_at !== undefined) return 'daily'
  if (isPlain(schedule) && schedule.every_minutes !== undefined) return 'every'
  return 'manual'
}

/** 일정을 바꾼다 — 그 밖의 칸(예: 요일)은 남긴다. 수동이면 일정이 없다. */
export function withSchedule(schedule: unknown, kind: ScheduleKind, value: string): Record<string, unknown> | undefined {
  const rest = isPlain(schedule) ? { ...schedule } : {}
  delete rest.daily_at
  delete rest.every_minutes
  if (kind === 'manual') return undefined
  if (kind === 'daily') return { ...rest, daily_at: value }
  const minutes = Number(value)
  return { ...rest, every_minutes: Number.isFinite(minutes) && value.trim() ? minutes : value }
}

/** 대조 일정(정의의 `reconcile`) — 서버 기본(칸 없음) · 매주 요일 · N일마다 · 사용 안 함(false). */
export type ReconcileKind = 'server' | 'weekly' | 'every' | 'off'

export const WEEKDAYS = [
  ['mon', '월'],
  ['tue', '화'],
  ['wed', '수'],
  ['thu', '목'],
  ['fri', '금'],
  ['sat', '토'],
  ['sun', '일'],
] as const

const WEEKDAY_OF: Record<string, string> = Object.fromEntries(WEEKDAYS.map(([day, ko]) => [ko, day]))

export function reconcileKindOf(reconcile: unknown): ReconcileKind {
  if (reconcile === false) return 'off'
  if (isPlain(reconcile) && reconcile.weekdays !== undefined) return 'weekly'
  if (isPlain(reconcile) && reconcile.every_days !== undefined) return 'every'
  return 'server'
}

/** 정의의 요일(영어 세 글자 · 한 글자 한국어, 하나 또는 목록) → 영어 세 글자. */
export function weekdaysOf(reconcile: unknown): string[] {
  const given = isPlain(reconcile) ? reconcile.weekdays : undefined
  const list = Array.isArray(given) ? given : given === undefined ? [] : [given]
  return list.map((one) => WEEKDAY_OF[String(one)] ?? String(one).toLowerCase())
}

/** 대조 일정의 방식을 바꾼다 — 시각(`at`)은 남긴다. 서버 기본이면 칸이 없고, 사용 안 함이면 false. */
export function withReconcileKind(reconcile: unknown, kind: ReconcileKind): unknown {
  if (kind === 'server') return undefined
  if (kind === 'off') return false
  const time = isPlain(reconcile) && reconcile.at !== undefined ? { at: reconcile.at } : {}
  return kind === 'weekly' ? { weekdays: ['sun'], ...time } : { every_days: 7, ...time }
}

/** 대조 요일 하나를 켜고 끈다 — 월요일부터의 차례로 둔다. */
export function withWeekday(reconcile: unknown, day: string, on: boolean): Record<string, unknown> {
  const rest = isPlain(reconcile) ? { ...reconcile } : {}
  const kept = weekdaysOf(rest).filter((one) => one !== day)
  const next = on ? [...kept, day] : kept
  rest.weekdays = WEEKDAYS.map(([one]) => one as string).filter((one) => next.includes(one))
  return rest
}

/** 열 대응 — 표의 줄(열 이름 → 원천의 자리). 열이 `"*"`(파일의 모든 열)이면 표가 아니다(null). */
export function columnRows(columns: unknown): [string, string][] | null {
  if (typeof columns === 'string') return null
  if (!isPlain(columns)) return []
  return Object.entries(columns).map(([name, path]) => [name, textOf(path)])
}

/** 표의 줄 → 열 대응. 빈 줄은 뺀다. */
export function columnsOf(rows: [string, string][]): Record<string, string> | undefined {
  const out: Record<string, string> = {}
  for (const [name, path] of rows) {
    if (name.trim() || path.trim()) out[name.trim()] = path.trim()
  }
  return Object.keys(out).length ? out : undefined
}

/** 고칠 수 있는 어댑터 칸 — 식별자 · 열 · 인증은 따로 그린다. */
export const adapterFields = (fields: AdapterField[]) =>
  fields.filter((one) => !['key', 'columns', 'auth'].includes(one.name))

/** 비밀이 들었을 칸 — 어디에 있든(서버의 `guard.check_no_secrets` 도 본다). */
function leaks(value: unknown, names: Set<string>, path: string, out: string[]) {
  if (!isPlain(value)) return
  for (const [key, inner] of Object.entries(value)) {
    const here = path ? `${path}.${key}` : key
    if (names.has(key.toLowerCase()) && inner !== undefined && inner !== null && inner !== '') out.push(here)
    leaks(inner, names, here, out)
  }
}

/** 저장 전에 막을 것 — 서버도 보지만, 시험까지 가기 전에 여기서 말한다. */
export function blockers(doc: unknown, slug: string, secretFields: string[]): string[] {
  if (!isPlain(doc)) return ['정의는 JSON 객체여야 합니다.']
  const out: string[] = []
  if (doc.format !== 'relay-pipeline/1') out.push('format은 relay-pipeline/1이어야 합니다.')
  if (doc.slug !== slug) {
    out.push(`slug는 변경할 수 없습니다(${slug}). 다른 파이프라인은 [새 연결]에서 생성하세요.`)
  }
  if (!isPlain(doc.adapter) || !doc.adapter.kind) out.push('adapter.kind(어댑터 종류)가 필요합니다.')
  else if (!at(doc, ['adapter', 'key'])) out.push('adapter.key(식별자)가 필요합니다.')
  if (!isPlain(doc.target) || !doc.target.server || !doc.target.token_env) {
    out.push('target에 server(대상 SP 주소)와 token_env(토큰의 환경 변수 이름)가 필요합니다.')
  }
  const found: string[] = []
  leaks(doc, new Set(secretFields.map((one) => one.toLowerCase())), '', found)
  for (const one of found) {
    out.push(`${one}: 인증 정보의 값은 정의에 입력할 수 없습니다. 환경 변수 이름(token_env)만 입력하세요.`)
  }
  return out
}
