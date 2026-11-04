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

/** 이력 한 줄 — 새것이 먼저 온다. */
export interface HistoryEvent {
  at?: string
  event?: string
  job_id?: string
  ok?: boolean
  counts?: RunCounts
  run?: string
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

/** 레시피의 판 하나 — draft(초안) · active(켜짐) · retired(내려 둠). 새것이 먼저 온다. */
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
  /** 켜면 대상에 무엇이 가나 — 무엇과 견줬는지는 `against`. */
  would_send?: RunCounts
  against?: string
  /** 변환 보고(값은 가린 것). */
  report?: string
  /** 변환이 멈춘 까닭 — 있으면 나머지 칸은 없다. */
  error?: string
}

const slugPath = (slug: string) => `/relay/pipelines/${encodeURIComponent(slug)}`
const recipePath = (slug: string, version: number) => `${slugPath(slug)}/recipes/${version}`

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
}
