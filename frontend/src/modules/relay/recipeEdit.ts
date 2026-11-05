/**
 * 레시피 편집의 규칙 — 화면이 `sp-table/1` 본문을 어떻게 읽고 고치나. 화면 코드와 떼어 둔다(시험이 쉽게).
 *
 * 칸의 규칙(값 뽑기)은 틀의 변환기(`sp_table`)가 읽는 모양 그대로다:
 *
 *     {"column": "열", "map": {...}, "date": true, "upper": true, "blank": [...]}   열
 *     {"key_of": "타입"}                                                            다른 타입의 식별자
 *     {"value": "글"}                                                               고정값
 *     {"collect" | "join": {...}} · {"parser": ...} · {"match": ...}                화면에서는 읽기만
 *
 * 뒤의 것(여러 행 모으기 · 해석기 · 대상 식별자 대조)은 화면에서 고치지 않는다 — 칸 하나 고치는 일에 비해
 * 틀리기 쉽고, 틀린 것이 시험 전까지 안 보인다. 본문(JSON)에서 고친다.
 */

export type Spec = Record<string, unknown>
export type RecipeDoc = Record<string, unknown>
export type RuleKind = 'column' | 'key_of' | 'value' | 'complex'

export const RULE_LABEL: Record<Exclude<RuleKind, 'complex'>, string> = {
  column: '열',
  key_of: '다른 타입의 식별자',
  value: '고정값',
}

export interface RecipeType {
  type_slug: string
  key?: Spec
  label?: Spec
  fields?: Record<string, Spec>
  [other: string]: unknown
}

export function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T
}

export function typesOf(doc: RecipeDoc): RecipeType[] {
  const types = doc.types
  return Array.isArray(types) ? (types as RecipeType[]) : []
}

export function ruleKind(spec: Spec | undefined): RuleKind {
  if (!spec) return 'column'
  if ('collect' in spec || 'join' in spec || 'parser' in spec || 'match' in spec) return 'complex'
  if ('key_of' in spec) return 'key_of'
  if ('value' in spec) return 'value'
  return 'column'
}

/** 규칙 종류를 바꾼다 — 그 종류에 없는 칸(열의 대응 · 날짜 …)은 버린다. */
export function withKind(spec: Spec | undefined, kind: Exclude<RuleKind, 'complex'>): Spec {
  if (kind === 'key_of') return { key_of: spec?.key_of ?? '' }
  if (kind === 'value') return { value: spec?.value ?? '' }
  const kept: Spec = { column: spec?.column ?? '' }
  for (const name of ['map', 'date', 'upper', 'blank'] as const) {
    if (spec && name in spec) kept[name] = spec[name]
  }
  return kept
}

/** 대응(원천 값 → 대상 값)을 줄로 — 화면의 표. */
export function mapPairs(spec: Spec | undefined): [string, string][] {
  const map = spec?.map
  if (!map || typeof map !== 'object') return []
  return Object.entries(map as Record<string, unknown>).map(([raw, out]) => [raw, String(out ?? '')])
}

/** 줄을 대응으로 — 빈 원천 값은 버린다. 줄이 없으면 `map` 을 지운다. */
export function withPairs(spec: Spec, pairs: [string, string][]): Spec {
  const next = { ...spec }
  const kept = pairs.filter(([raw]) => raw.trim() !== '')
  if (kept.length === 0) delete next.map
  else next.map = Object.fromEntries(kept.map(([raw, out]) => [raw.trim(), out]))
  return next
}

/** 한 타입의 칸들 — 식별자 · 이름 · 속성 순. */
export function fieldsOf(one: RecipeType): [string, Spec | undefined][] {
  return [['key', one.key], ['label', one.label], ...Object.entries(one.fields ?? {})]
}

/** 칸 하나를 바꾼 새 타입. `key` · `label` 은 타입의 칸, 그 밖은 `fields` 의 칸이다. */
export function withField(one: RecipeType, name: string, spec: Spec | null): RecipeType {
  if (name === 'key' || name === 'label') return { ...one, [name]: spec ?? undefined }
  const fields = { ...one.fields }
  if (spec === null) delete fields[name]
  else fields[name] = spec
  return { ...one, fields }
}

export function withType(doc: RecipeDoc, slug: string, next: RecipeType): RecipeDoc {
  return { ...doc, types: typesOf(doc).map((one) => (one.type_slug === slug ? next : one)) }
}

const same = (a: unknown, b: unknown) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null)

/**
 * 출발 판에서 바뀐 것 — 「plm_model.model_kind」 · 「plm_task(타입 추가)」 · 「관계」 · 「그 밖(title …)」.
 * 저장 전에 사람이 읽는다 — 칸 하나 고치려다 다른 데를 건드린 것이 여기서 보인다.
 */
export function changes(before: RecipeDoc, after: RecipeDoc): string[] {
  const out: string[] = []
  const was = new Map(typesOf(before).map((one) => [one.type_slug, one]))
  const now = new Map(typesOf(after).map((one) => [one.type_slug, one]))
  for (const [slug, one] of now) {
    const old = was.get(slug)
    if (!old) {
      out.push(`${slug}(타입 추가)`)
      continue
    }
    const names = new Set([...fieldsOf(old).map(([n]) => n), ...fieldsOf(one).map(([n]) => n)])
    const oldFields = new Map(fieldsOf(old))
    const newFields = new Map(fieldsOf(one))
    for (const name of names) {
      if (!same(oldFields.get(name), newFields.get(name))) {
        if (!oldFields.has(name) || oldFields.get(name) === undefined) out.push(`${slug}.${name}(칸 추가)`)
        else if (!newFields.has(name) || newFields.get(name) === undefined) out.push(`${slug}.${name}(칸 삭제)`)
        else out.push(`${slug}.${name}`)
      }
    }
    const rest = (t: RecipeType) => ({ ...t, key: undefined, label: undefined, fields: undefined })
    if (!same(rest(old), rest(one))) out.push(`${slug}(타입의 그 밖)`)
  }
  for (const slug of was.keys()) if (!now.has(slug)) out.push(`${slug}(타입 삭제)`)
  if (!same(before.relations, after.relations)) out.push('관계')
  const top = (d: RecipeDoc) => ({ ...d, types: undefined, relations: undefined })
  if (!same(top(before), top(after))) out.push('그 밖(title · dictionaries …)')
  return out
}

/** 저장 전에 막을 것 — 서버도 보지만, 빈 열 이름 같은 것은 시험까지 가기 전에 여기서 말한다. */
export function blockers(doc: RecipeDoc): string[] {
  const out: string[] = []
  if (doc.format !== 'sp-table/1') out.push('format 이 sp-table/1 이어야 합니다.')
  const types = typesOf(doc)
  if (types.length === 0) out.push('types 가 비었습니다 — 무엇을 만들지 적어야 합니다.')
  const slugs = new Set(types.map((one) => one.type_slug))
  for (const one of types) {
    for (const [name, spec] of fieldsOf(one)) {
      if (!spec) {
        if (name === 'key') out.push(`${one.type_slug}: 식별자(key) 규칙이 없습니다.`)
        continue
      }
      const kind = ruleKind(spec)
      if (kind === 'column' && !String(spec.column ?? '').trim()) {
        out.push(`${one.type_slug}.${name}: 열 이름이 비었습니다.`)
      }
      if (kind === 'key_of' && !slugs.has(String(spec.key_of ?? ''))) {
        out.push(`${one.type_slug}.${name}: 다른 타입 「${String(spec.key_of ?? '')}」 이 레시피에 없습니다.`)
      }
    }
  }
  return out
}

/** 칸 규칙 한 줄(읽기만 하는 규칙을 보일 때). */
export function ruleText(spec: Spec | undefined): string {
  return spec ? JSON.stringify(spec) : '—'
}
