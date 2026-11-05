/**
 * 중계 — **어느 원천의 어느 API 가 어느 SP 의 무엇으로 가나**(ADR 0001).
 *
 * 지도와 상세는 서버가 정의(파이프라인 · 레시피 · 대상 스키마)에서 매번 계산해 준다. 여기서
 * 손으로 그림을 들고 있지 않는다 — 정의와 화면이 갈리는 날 화면이 거짓말을 한다.
 * 타입은 생성된 것(`schema.d.ts`)을 쓴다.
 */

import { api } from '@/shared/api/client'
import type { components } from '@/shared/api/schema'

type Schemas = components['schemas']

export type RelayMap = Schemas['RelayMap']
export type MapNode = Schemas['MapNode']
export type MapEdge = Schemas['MapEdge']
export type PipelineDetail = Schemas['PipelineDetail']
export type LineageStep = Schemas['LineageStep']
export type RelayJob = Schemas['RelayJob']
export type QueuedOut = Schemas['QueuedOut']
export type RunIn = Schemas['RunIn']

/** 지도의 열 — 왼쪽에서 오른쪽으로 이 차례다. */
export const NODE_KINDS = ['system', 'endpoint', 'pipeline', 'target', 'type', 'downstream'] as const
export type NodeKind = (typeof NODE_KINDS)[number]

/** 사람이 고친 칸을 어떻게 하나 — 비우면 서버가 keep(비켜 가고 계획에 적음)으로 본다. */
export type HumanEdits = 'keep' | 'overwrite'

/** 파이프라인 상세의 `state` — 서버가 비밀 없는 칸만 골라 준다(`services.detail`). */
export interface PendingPlan {
  job_id: string
  ok: boolean
  summary: string
  at: string
}

export interface PipelineState {
  watermark?: string | null
  applied_run?: string | null
  applied_at?: string | null
  pending?: PendingPlan | null
  incrementals?: number
}

/** 칸마다 건수 — `{objects|relations: {타입: {created, changed, unchanged, gone}}}`. */
export type RunCounts = Record<string, Record<string, Record<string, number>>>

/**
 * 이력 한 줄 — 새것이 먼저 온다. `event` 는 planned · applied · blocked · nothing · auto_skipped.
 * 자동 적용이 적용했으면 `applied` 에 `by: "auto"` 가 붙고(사람의 적용에는 없다), 타이머의 실행이
 * 기준을 못 넘어 계획이 사람을 기다리면 `auto_skipped` 가 그 까닭(`reasons`)을 싣는다.
 */
export interface HistoryEvent {
  at?: string
  event?: string
  job_id?: string
  ok?: boolean
  counts?: RunCounts
  run?: string
  by?: string
  reasons?: string[]
}

/** 지도의 파이프라인 마디가 싣는 것(`services.build_map`). */
export interface PipelineNodeDetail {
  applied_at?: string | null
  watermark?: string | null
  pending_job?: string | null
  /** 대상 SP 의 작업 화면 — 절대 주소다. 새 탭으로 연다. */
  pending_link?: string
  problems?: string[]
  counts?: RunCounts
}

export function pipelineDetailOf(node: MapNode): PipelineNodeDetail {
  return (node.detail ?? {}) as PipelineNodeDetail
}

export function stateOf(detail: PipelineDetail): PipelineState {
  return (detail.state ?? {}) as PipelineState
}

export function historyOf(detail: PipelineDetail): HistoryEvent[] {
  return (detail.history ?? []) as HistoryEvent[]
}

/** 이 플랫폼의 작업 화면에서 그 작업을 펼친 채로 연다. */
export function jobLink(jobId: string): string {
  return `/jobs?job=${encodeURIComponent(jobId)}`
}

/** 대상 SP 의 작업 화면 — 계획은 거기서 읽는다. */
export function targetJobLink(server: string, jobId: string): string {
  return `${server.replace(/\/$/, '')}/jobs?job=${encodeURIComponent(jobId)}`
}

const tally = (group: Record<string, Record<string, number>> | undefined) => {
  const sum = { created: 0, changed: 0, gone: 0 }
  for (const one of Object.values(group ?? {})) {
    sum.created += Number(one.created ?? 0)
    sum.changed += Number(one.changed ?? 0)
    sum.gone += Number(one.gone ?? 0)
  }
  return sum
}

/** 「새 3 · 바뀜 1 · 무덤 0」 — 관계가 움직였으면 뒤에 따로 붙인다. 건수가 없으면 빈 글. */
export function countsLine(counts: RunCounts | undefined | null): string {
  if (!counts || Object.keys(counts).length === 0) return ''
  const objects = tally(counts.objects)
  const line = `새 ${objects.created} · 바뀜 ${objects.changed} · 무덤 ${objects.gone}`
  const relations = tally(counts.relations)
  if (relations.created + relations.changed + relations.gone === 0) return line
  return `${line} (관계 새 ${relations.created} · 바뀜 ${relations.changed} · 무덤 ${relations.gone})`
}

/**
 * 레시피의 판 하나 — draft(초안) · active(켜짐) · retired(내려 둠) · discarded(버림). 새것이 먼저
 * 온다. 버린 판도 목록에 남는다(판 번호는 다시 쓰지 않는다).
 */
export type RecipeVersion = Schemas['RecipeOut']

/** 판의 본문 — 생성 타입은 `{[key]: unknown}` 이라 모양을 여기 적는다(`routes.recipe_body`). */
export interface RecipeBody extends RecipeVersion {
  body: unknown
}

/**
 * 시험 — 미러(마지막으로 받은 원천)에 그 판을 돌려 본 것. **대상에 아무것도 안 보낸다.**
 * 생성 타입은 `{[key]: unknown}` 이라 모양을 여기 적는다(`recipes.trial`).
 */
export interface RecipeTrial {
  version: number
  /** 미해결 없이 다 바뀌었나 — 거짓이면 켜도 그 실행은 멈춘다. */
  clean: boolean
  problems: string[]
  /** 타입마다 만든 줄 수. */
  rows?: Record<string, number>
  unresolved_count?: number
  /** 앞의 스무 개. */
  unresolved?: unknown[]
  /**
   * 켜면 대상에 무엇이 가나 — 무엇과 견줬는지는 `against`. 정의 시험이 원천을 앞부분만 받았으면
   * 없다(덜 받은 채 견주면 나머지가 다 지움으로 보인다) — 그때는 `against` 가 그렇다고 말한다.
   */
  would_send?: RunCounts
  against?: string
  /** 변환 보고(값은 가린 것). */
  report?: string
  /** 변환이 멈춘 까닭 — 있으면 나머지 칸은 없다. */
  error?: string
}

/** 정의의 판 하나 — 레시피 판과 같은 넷(draft · active · retired · discarded). 새것이 먼저 온다. */
export type DefinitionVersion = Schemas['DefinitionOut']
export type DefinitionTestIn = Schemas['DefinitionTestIn']

/** 판의 본문 — `relay-pipeline/1` 그대로. 비밀은 안 든다(토큰은 환경 변수 이름만, `routes.definition_body`). */
export interface DefinitionBody extends DefinitionVersion {
  body: unknown
}

/**
 * 정의의 자동 적용 기준(`relay_core/autoapply.Policy`) — **타이머의 실행에만** 쓴다. 모두 넘은 계획을
 * 사람 없이 적용한다.
 */
export interface AutoApplyPolicy {
  /** 사람이 적용한 것이 이만큼 된 뒤부터 — 처음 몇 번은 사람이 계획을 읽는다. */
  after_manual: number
  /** 한 번에 생성 + 고침이 이것보다 많으면 사람에게 넘긴다. */
  max_changes: number
  /** 지움(사용 중지 · 선 끊기)이 있어도 적용하나. */
  allow_gone: boolean
}

export const AUTO_APPLY_DEFAULT: AutoApplyPolicy = {
  after_manual: 3,
  max_changes: 500,
  allow_gone: false,
}

/**
 * 정의 본문의 `auto_apply` — 없거나 거짓이면 꺼짐(null). `true` 면 기본 기준, 객체면 빠진 칸만 기본.
 * 서버는 틀린 값을 저장 때 거절하므로 여기서는 모양만 읽는다.
 */
export function autoApplyOf(body: unknown): AutoApplyPolicy | null {
  const spec = (body as { auto_apply?: unknown } | null)?.auto_apply
  if (!spec) return null
  if (typeof spec !== 'object') return { ...AUTO_APPLY_DEFAULT }
  const given = spec as Partial<AutoApplyPolicy>
  return {
    after_manual: given.after_manual ?? AUTO_APPLY_DEFAULT.after_manual,
    max_changes: given.max_changes ?? AUTO_APPLY_DEFAULT.max_changes,
    allow_gone: given.allow_gone ?? AUTO_APPLY_DEFAULT.allow_gone,
  }
}

/** 같은 slug 의 정의 파일 — 없으면 `body` 가 null 이다(`routes.definition_file`). */
export interface DefinitionFile {
  path: string
  body: unknown
  problems: string[]
}

/** 정의 시험의 대상 쪽 — 닿나 · 토큰이 듣나 · 레시피가 보낼 타입이 대상에 있나. */
export interface DefinitionTargetCheck {
  server: string
  reachable: boolean
  /** 안 닿은 까닭 — 있으면 나머지 칸은 없다. */
  error?: string
  slug?: string | null
  version?: string | null
  /** 「정상」 이거나 안 들은 까닭. */
  token?: string
  types_present?: string[]
  types_missing?: string[]
  /** 타입마다 대상이 적어 둔 관리 주체 — 다른 원천이 맡은 타입이면 여기 보인다. */
  managed_by?: Record<string, string | null>
}

/** 정의 시험의 원천 쪽 — 그 판의 어댑터로 한 번 받아 본 것. */
export type DefinitionSourceCheck =
  | {
      ok: true
      /** 어댑터가 무엇에 닿았나 한 줄. */
      probe: string
      rows: number
      deleted: number
      complete: boolean
      /**
       * 시험용 상한 — 있으면 원천이 커서 앞의 그 줄만 받았다(`complete` 는 거짓). 그때 레시피 쪽은
       * 「켜면 보낼 것」 을 세지 않는다. 없거나 null 이면 상한에 안 걸렸다.
       */
      capped?: number | null
      watermark?: string | null
      columns: string[]
      /** 정의가 적은 열 중 받은 줄에서 늘 빈 것 — 열 이름을 잘못 적었을 때 여기 뜬다. */
      always_empty: string[]
      sample: Record<string, unknown>[]
    }
  | { ok: false; error: string }

/** 레시피를 못 찾았으면 까닭만 온다(`note`). 찾았으면 레시피 판 시험과 같은 모양에 무엇을 썼나(`used`)가 붙는다. */
export type DefinitionRecipeCheck =
  | { note: string }
  | (Omit<RecipeTrial, 'version'> & { used: string })

/**
 * 정의 판 시험 — 그 판으로 원천을 받아 보고 · 대상을 보고 · 레시피로 바꿔 본다. **대상에 아무것도
 * 안 보낸다.** 생성 타입은 `{[key]: unknown}` 이라 모양을 여기 적는다(`definitions.trial`).
 * 원천에서 멈추면 `recipe` 는 없다.
 */
export interface DefinitionTrial {
  version: number
  problems: string[]
  target?: DefinitionTargetCheck
  source?: DefinitionSourceCheck
  recipe?: DefinitionRecipeCheck
}

const slugPath = (slug: string) => `/relay/pipelines/${encodeURIComponent(slug)}`
const recipePath = (slug: string, version: number) => `${slugPath(slug)}/recipes/${version}`
const definitionPath = (slug: string, version: number) => `${slugPath(slug)}/definitions/${version}`

export const relayApi = {
  map: () => api.get<RelayMap>('/relay/map'),
  pipeline: (slug: string) => api.get<PipelineDetail>(slugPath(slug)),
  /** 작업으로 넣는다(202) — 원천 · 대상이 느리면 요청이 끊기므로 요청 안에서 안 돌린다. */
  run: (slug: string, humanEdits: HumanEdits) =>
    api.post<QueuedOut>(`${slugPath(slug)}/run`, { human_edits: humanEdits } satisfies RunIn),
  apply: (slug: string) => api.post<QueuedOut>(`${slugPath(slug)}/apply`),
  recipes: (slug: string) => api.get<RecipeVersion[]>(`${slugPath(slug)}/recipes`),
  recipe: (slug: string, version: number) => api.get<RecipeBody>(recipePath(slug, version)),
  testRecipe: (slug: string, version: number) =>
    api.post<RecipeTrial>(`${recipePath(slug, version)}/test`),
  /** 켠다 — **화면의 사람만**(서버가 토큰을 거절한다). 다음 실행부터 이 판이 돈다. */
  activateRecipe: (slug: string, version: number) =>
    api.post<RecipeVersion>(`${recipePath(slug, version)}/activate`),
  /** 초안을 버린다 — 초안만(아니면 409). 버린 판은 목록에 「버림」 으로 남고, 그 번호는 다시 안 쓴다. */
  discardRecipe: (slug: string, version: number) =>
    api.post<RecipeVersion>(`${recipePath(slug, version)}/discard`),
  definitions: (slug: string) => api.get<DefinitionVersion[]>(`${slugPath(slug)}/definitions`),
  definition: (slug: string, version: number) =>
    api.get<DefinitionBody>(definitionPath(slug, version)),
  definitionFile: (slug: string) => api.get<DefinitionFile>(`${slugPath(slug)}/definition-file`),
  /** 레시피 판을 안 주면 켜진 판, 그것도 없으면 정의 파일의 레시피로 바꿔 본다. */
  testDefinition: (slug: string, version: number, body: Partial<DefinitionTestIn> = {}) =>
    api.post<DefinitionTrial>(`${definitionPath(slug, version)}/test`, body),
  /** 켠다 — **화면의 사람만**(서버가 토큰을 거절한다). 다음 실행부터 이 판이 같은 slug 의 정의 파일 대신 돈다. */
  activateDefinition: (slug: string, version: number) =>
    api.post<DefinitionVersion>(`${definitionPath(slug, version)}/activate`),
  /**
   * 초안을 버린다 — 초안만(아니면 409). 켜진 판이 없는 새 파이프라인은 초안을 다 버리면 지도에서
   * 빠지고 상세가 404 가 된다.
   */
  discardDefinition: (slug: string, version: number) =>
    api.post<DefinitionVersion>(`${definitionPath(slug, version)}/discard`),
}
