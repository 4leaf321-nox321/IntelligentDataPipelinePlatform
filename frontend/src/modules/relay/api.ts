/**
 * 중계 — **어느 원천의 어느 API 가 어느 SP 의 무엇으로 가나**(ADR 0001).
 *
 * 지도와 상세는 서버가 정의(파이프라인 · 레시피 · 대상 스키마)에서 매번 계산해 준다. 여기서
 * 손으로 그림을 들고 있지 않는다 — 정의와 화면이 갈리는 날 화면이 거짓말을 한다.
 * 타입은 생성된 것(`schema.d.ts`)을 쓴다.
 */

import { api, downloadFile } from '@/shared/api/client'
// 생성물(`npm run api:types` — 커밋된 backend/openapi.json 에서). git 에 없으므로 `npm run build` 가
// 먼저 만든다(`prebuild`) — 없으면 막 받은 저장소(CI · 번들)에서 tsc 가 멈춘다(2026-11-06 첫 CI).
import type { components } from '@/shared/api/schema'

type Schemas = components['schemas']

export type RelayMap = Schemas['RelayMap']
export type MapNode = Schemas['MapNode']
export type MapEdge = Schemas['MapEdge']
/**
 * 상세 — 대조의 두 칸은 생성 타입보다 먼저 왔을 수 있어 여기 적는다(다시 생성해도 같은 모양이 된다:
 * `reconcile` 은 `{[key]: unknown} | null` 이라 모양은 `reconcileOf` 가 읽는다).
 */
export type PipelineDetail = Schemas['PipelineDetail'] & {
  reconcile?: unknown
  /** 대조의 일정 — 「7일마다 03:00」 · 「수동」. */
  reconcile_label?: string
}
export type LineageStep = Schemas['LineageStep']
export type RelayJob = Schemas['RelayJob']
export type QueuedOut = Schemas['QueuedOut']
export type RunIn = Schemas['RunIn']
/** 실행 추이 — 실행마다 한 줄(오래된 것부터). 작업 폴더의 `trend.json` 이라 작업 기록이 지워져도 남는다. */
export type TrendOut = Schemas['TrendOut']
export type TrendRow = Schemas['TrendRow']
/** 미해결 작업함 — 멈춘 실행의 참조(후보와 함께) · 값 갈림(`app/modules/relay/unresolved.py`). */
export type InboxOut = Schemas['InboxOut']
export type InboxReference = Schemas['ReferenceOut']
export type InboxCandidate = Schemas['CandidateOut']
export type InboxSplit = Schemas['SplitOut']
export type SuggestOut = Schemas['SuggestOut']
export type AliasEntry = Schemas['EntryIn']
/** 거꾸로 보기 — 대상 객체 하나의 값이 어느 원천 행 · 칸 · 레시피 규칙에서 왔나(`relay_core/trace`). */
export type ValueTrace = Schemas['TraceOut']
export type TraceField = Schemas['TraceField']

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
  /** 그 계획이 어디서 왔나 — `run`(원천이 바뀜) · `reconcile`(대조의 보정 계획). 옛 상태에는 없다. */
  origin?: string
  /**
   * 나눠 보내면 조각 폴더들 — 비면 한 묶음이다. `part` 는 지금 조각(0부터)이고 앞 조각들은 적용됐다.
   * `job_id` 가 비면 그 조각은 아직 미리 보지 않았다(「적용」 이 미리 보고 잇는다).
   */
  parts?: string[]
  part?: number
}

/** 나눠 보내는 계획의 조각 수 — 한 묶음이면 1. */
export function partsOf(pending: PendingPlan): number {
  return Math.max(1, pending.parts?.length ?? 0)
}

/** 받기 점검이 멈춘 것(`relay_core.intake`) — 미러 · 워터마크는 그대로이고 사람의 「확인 후 진행」 을 기다린다. */
export interface HeldIntake {
  at: string
  /** 화면 · 알림에 쓰는 까닭 — 한 줄씩(대량 삭제 · 빈 칸). */
  reasons: string[]
  findings: {
    before: number
    /** 기준을 넘은 삭제 — 넘지 않았으면 0. */
    deleted: number
    received: number
    blanks: { column: string; lost: number; filled: number }[]
  }
}

export interface PipelineState {
  watermark?: string | null
  applied_run?: string | null
  applied_at?: string | null
  pending?: PendingPlan | null
  incrementals?: number
  held?: HeldIntake | null
}

/** 칸마다 건수 — `{objects|relations: {타입: {created, changed, unchanged, gone}}}`. */
export type RunCounts = Record<string, Record<string, Record<string, number>>>

/**
 * 이력 한 줄 — 새것이 먼저 온다. `event` 는 planned · applied · part_applied(나눠 보내는 계획의 한
 * 조각) · blocked · held(받기 점검 멈춤 — `reasons`) · held_accepted(확인 후 진행 — `by` · `reasons`)
 * · nothing · auto_skipped · reconciled. 자동 적용이 적용했으면 `applied` 에 `by: "auto"` 가 붙고(사람의 적용에는 없다), 타이머의
 * 실행이 기준을 못 넘어 계획이 사람을 기다리면 `auto_skipped` 가 그 까닭(`reasons`)을 싣는다.
 * 대조는 `reconciled` 에 어긋남 수(`drift`)와 한 줄(`summary`)을, 대조의 보정 계획은 `planned` 에
 * `origin: "reconcile"` 을 싣는다.
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
  origin?: string
  drift?: number
  summary?: string
  /** 나눠 보내는 계획 — `part_applied` · `blocked` 는 그 조각 번호(1부터), `planned` · `applied` 는 조각 수. */
  part?: number
  parts?: number
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
  /** 마지막 대조의 어긋남 수 — 대조한 적이 없거나 대조하지 못했으면 0. */
  drift?: number
  /** 마지막 대조 — 같음 · 어긋남 · 대조 못 함(대상에 타입이 없는 등). 대조한 적이 없으면 null. */
  reconcile_status?: 'same' | 'drift' | 'problem' | null
  reconciled_at?: string | null
  /** 늦음의 까닭 — 일정대로라면 끝났어야 할 실행이 끝나지 않았다. 비면 늦지 않았다. */
  late?: string
  /** 받기 점검이 멈춘 까닭 — 비면 멈추지 않았다. */
  held?: string[]
}

/** 대조의 네 갈래 — 화면의 차례도 이것이다. */
export const DRIFT_KINDS = ['missing', 'deprecated', 'changed', 'extra'] as const
export type DriftKind = (typeof DRIFT_KINDS)[number]

export const DRIFT_LABEL: Record<DriftKind, string> = {
  missing: '빠짐',
  deprecated: '안 씀',
  changed: '다름',
  extra: '대상에만',
}

/** 거꾸로 보기의 「마지막 대조에서」 — 어긋남 넷에 같음 · 견주지 못함을 더한다. */
export function traceStateLabel(state: string): string {
  if (state in DRIFT_LABEL) return DRIFT_LABEL[state as DriftKind]
  return state === 'same' ? '대상과 같음' : '대조하지 못함'
}

/**
 * 대조의 한 타입(`relay_core/reconcile.TypeDrift`). 목록은 상세에서 갈래마다 앞의 20개까지
 * (`counts` 가 참 건수다) — 전부는 `relayApi.reconcileReport`.
 *
 * 둘 다 `interface` 가 아니라 `type` 이다 — 생성 타입의 `reconcile` 은 `{[key]: unknown}` 이고,
 * interface 는 그 꼴(색인 서명)에 들어가지 않는다.
 */
export type ReconcileType = {
  /** 기준의 줄. */
  rows: number
  missing: string[]
  deprecated: string[]
  /** {식별자: 다른 칸}. */
  changed: Record<string, string[]>
  extra: string[]
  counts?: Record<DriftKind, number>
  /** 확인 못 한 참조 칸 — 어긋남으로 세지 않는다. */
  unverified: number
  /** 대상의 그 타입을 이 원천이 관리하나 — 그래야 「대상에만」 을 보정 계획이 사용 중지한다. */
  managed: boolean
  /** 대상에 타입이 없다. */
  absent: boolean
}

/** 대조 보고서(`relay_core/reconcile.Report.to_json`). */
export type ReconcileReport = {
  at: string
  /** 기준 — 지난번 적용된 출력(작업 폴더의 실행 이름). */
  baseline: string
  source: string
  drift: number
  summary: string
  /** 대조하지 못한 까닭 — 있으면 보정 계획이 멈춘다. */
  problems: string[]
  /** 대상을 어떻게 읽었나 — `코어`(코어 API) · `내보내기`(묶음 내보내기 한 번) · `목록`(쪽마다). 옛 보고서에는 없다. */
  via?: string
  /** 대조는 됐지만 알릴 것 — 내보내기를 못 써 목록으로 읽은 까닭. `summary` 에도 붙는다. */
  notes?: string[]
  types: Record<string, ReconcileType>
}

export function reconcileOf(detail: PipelineDetail): ReconcileReport | null {
  const found = detail.reconcile as ReconcileReport | null | undefined
  return found && typeof found === 'object' ? found : null
}

/** 보고서의 결 — `StatusBadge` 의 `relay_reconcile` 값(대조 작업의 결과 머리와 같다). */
export function reconcileStatus(report: ReconcileReport): 'same' | 'drift' | 'problem' {
  if ((report.problems ?? []).length > 0) return 'problem'
  return report.drift > 0 ? 'drift' : 'same'
}

/** 갈래의 참 건수 — `counts` 가 없는 옛 보고서면 목록의 길이. */
export function driftCount(one: ReconcileType, kind: DriftKind): number {
  const counted = one.counts?.[kind]
  if (typeof counted === 'number') return counted
  return kind === 'changed' ? Object.keys(one.changed ?? {}).length : (one[kind] ?? []).length
}

/** 갈래의 본보기 수 — 목록에 실제로 든 것. */
export function driftShown(one: ReconcileType, kind: DriftKind): number {
  return kind === 'changed' ? Object.keys(one.changed ?? {}).length : (one[kind] ?? []).length
}

/** 본보기가 참 건수보다 적은 갈래가 있나 — 그러면 「전체 보고서」 를 권한다. */
export function reportTruncated(report: ReconcileReport): boolean {
  return Object.values(report.types ?? {}).some((one) =>
    DRIFT_KINDS.some((kind) => driftShown(one, kind) < driftCount(one, kind)),
  )
}

/**
 * 보정 계획에 실을 것의 수 — 빠짐 · 안 씀 · 다름은 늘, 대상에만은 이 원천이 관리하는 타입만.
 * 0 이면 서버가 「실을 것이 없습니다」 로 끝낸다(대상에만 있는 것은 사람이 판단한다).
 */
export function fixableCount(report: ReconcileReport): number {
  let sum = 0
  for (const one of Object.values(report.types ?? {})) {
    sum += driftCount(one, 'missing') + driftCount(one, 'deprecated') + driftCount(one, 'changed')
    if (one.managed) sum += driftCount(one, 'extra')
  }
  return sum
}

/** 이 작업이 보정 계획까지였나 — 대조 작업의 결과 한 줄 머리(`relay/jobs.reconcile_job`)로 가른다. */
export function isPlanJob(job: Pick<RelayJob, 'kind' | 'summary'>): boolean {
  return job.kind === 'relay_reconcile' && /^\[(planned|nothing|waiting|blocked)\]/.test(job.summary ?? '')
}

export function pipelineDetailOf(node: MapNode): PipelineNodeDetail {
  return (node.detail ?? {}) as PipelineNodeDetail
}

/** 파이프라인 마디의 마지막 대조 — 어긋남 수 · 그 시각 · 대조하지 못했나. 파이프라인이 아니거나 대조한 적이 없으면 0. */
export function driftOf(node: MapNode): { drift: number; at: string | null; problem: boolean } {
  if (node.kind !== 'pipeline') return { drift: 0, at: null, problem: false }
  const detail = pipelineDetailOf(node)
  const drift = Number(detail.drift ?? 0)
  return {
    drift: Number.isFinite(drift) && drift > 0 ? drift : 0,
    at: detail.reconciled_at ?? null,
    problem: detail.reconcile_status === 'problem',
  }
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

/** 「생성 3 · 변경 1 · 삭제 0」 — 관계가 움직였으면 뒤에 따로 붙인다. 건수가 없으면 빈 글. */
export function countsLine(counts: RunCounts | undefined | null): string {
  if (!counts || Object.keys(counts).length === 0) return ''
  const objects = tally(counts.objects)
  const line = `생성 ${objects.created} · 변경 ${objects.changed} · 삭제 ${objects.gone}`
  const relations = tally(counts.relations)
  if (relations.created + relations.changed + relations.gone === 0) return line
  return `${line} (관계 생성 ${relations.created} · 변경 ${relations.changed} · 삭제 ${relations.gone})`
}

/**
 * 레시피의 판 하나 — draft(초안) · active(사용 중) · retired(사용 종료) · discarded(폐기). 새것이 먼저
 * 온다. 버린 판도 목록에 남는다(판 번호는 다시 쓰지 않는다).
 */
export type RecipeVersion = Schemas['RecipeOut']
/** 초안 저장의 답 — 저장은 했지만 알아야 할 것(어댑터가 내지 않는 열 · 빈 types …)이 `problems`. */
export type RecipeSaved = Schemas['RecipeSaved']

/** 정의 파일이 가리키는 레시피 — 사용 중인 판이 없을 때 쓰는 것. 없으면 `body` 가 null. */
export interface RecipeFile {
  path: string
  body: Record<string, unknown> | null
  problems?: string[]
}

/** 미러(마지막으로 받은 원천)의 앞 몇 줄 — 레시피가 읽는 평평한 열 그대로. */
export interface SourceSample {
  columns: string[]
  total: number
  rows: Record<string, unknown>[]
  note: string
}

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
export type DefinitionSaved = Schemas['DefinitionSaved']

/** 어댑터 종류의 칸 — 서버의 정의 모양(dataclass)에서 계산한다(`definitions.kinds`). 비밀 칸은 없다. */
export interface AdapterField {
  name: string
  type: string
  required: boolean
  default: unknown
  doc: string
}

export interface AdapterKinds {
  kinds: { kind: string; doc: string; required: string[]; fields: AdapterField[] }[]
  auth: AdapterField[]
  secret_fields: string[]
}

/** 탐색 — 어댑터 설정으로 원천에 닿아 칸의 자리 · 보기 값 · 열 이름 제안. 저장하지 않는다. */
export interface DiscoverOut {
  kind: string
  ok: boolean
  probe?: string
  probe_error?: string
  error?: string
  found?: {
    paths?: { path: string; sample: string }[]
    columns?: Record<string, string>
    rows_seen?: number
    entity_sets?: { entity_set: string; entity_type?: string; key?: string[] }[]
    metadata_note?: string
  }
}

/** 대상 SP 의 정의 — 타입 → 칸 → 정의(자료형 · 이름 …). 레시피가 없는 초안이면 대상의 타입 전부. */
export interface TargetSchema {
  target: string
  server: string
  note: string
  types: Record<string, Record<string, { key: string; label?: string; data_type?: string }>>
}

/** SP 타입 정의 초안 — SP 관리자에게 넘길 타입 정의와 그 타입으로 옮기는 레시피의 첫 판. */
export interface TypeDraft {
  ontology: { types: { slug: string; label: string; properties: { key: string; label: string; data_type: string }[] }[] }
  recipe: Record<string, unknown>
}

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
  run: (slug: string, humanEdits: HumanEdits, accept = false, full = false) =>
    api.post<QueuedOut>(`${slugPath(slug)}/run`, {
      human_edits: humanEdits,
      accept,
      full,
    } satisfies RunIn),
  /** 미해결 작업함 — 시스템 관리자만(원천 표기가 그대로 나온다). 대상에 후보를 묻느라 몇 초 걸릴 수 있다. */
  unresolved: (slug: string) => api.get<InboxOut>(`${slugPath(slug)}/unresolved`),
  /** GLM 이 대상의 후보 안에서 하나를 고른다 — 설정하지 않았으면 409. 제안일 뿐이다. */
  suggest: (slug: string, target: string, text: string, column: string) =>
    api.post<SuggestOut>(`${slugPath(slug)}/unresolved/suggest`, { target, text, column }),
  /** 선택한 대응을 지금 레시피의 relay 사전에 더해 **레시피 판 초안**으로 — 사용은 레시피 판에서. */
  draftAliases: (slug: string, entries: AliasEntry[], note = '') =>
    api.post<RecipeSaved>(`${slugPath(slug)}/unresolved/draft`, { entries, note }),
  /** 대표 플랫폼에 별칭 등록을 요청할 목록(CSV) — 지금 레시피의 relay 사전 그대로. */
  aliasCsv: (slug: string) => downloadFile(`${slugPath(slug)}/aliases.csv`, `${slug}-별칭요청.csv`),
  /** 실행 추이 — 끝의 `limit` 줄(서버 상한 400). 읽기만 한다. */
  trend: (slug: string, limit = 90) =>
    api.get<TrendOut>(`${slugPath(slug)}/trend?limit=${limit}`),
  apply: (slug: string) => api.post<QueuedOut>(`${slugPath(slug)}/apply`),
  /**
   * 대조 — 작업으로 넣는다(202). 대상 SP 의 객체를 읽어 기준과 견줄 뿐 아무것도 안 보낸다. `plan` 이면
   * 어긋난 줄의 **보정 계획**(미리보기)까지 대상의 검토함에 올린다 — 적용은 사람이.
   */
  reconcile: (slug: string, plan: boolean) =>
    api.post<QueuedOut>(`${slugPath(slug)}/reconcile`, { plan }),
  /** 마지막 대조의 보고서 전부(상세는 본보기만 싣는다). 대조한 적이 없으면 404. */
  reconcileReport: (slug: string) => api.get<ReconcileReport>(`${slugPath(slug)}/reconcile`),
  /** 거꾸로 보기 — 읽기만 한다. 못 찾으면 404 와 까닭(모르는 타입 · 어디에도 없는 식별자 · 받은 적 없음). */
  trace: (slug: string, typeSlug: string, key: string) =>
    api.get<ValueTrace>(
      `${slugPath(slug)}/trace?${new URLSearchParams({ type_slug: typeSlug, key }).toString()}`,
    ),
  recipes: (slug: string) => api.get<RecipeVersion[]>(`${slugPath(slug)}/recipes`),
  recipe: (slug: string, version: number) => api.get<RecipeBody>(recipePath(slug, version)),
  recipeFile: (slug: string) => api.get<RecipeFile>(`${slugPath(slug)}/recipe-file`),
  /** 레시피 편집의 열 목록 — 원천에 다시 묻지 않는다(실행이 받아 둔 미러). */
  sample: (slug: string, limit = 1) => api.get<SourceSample>(`${slugPath(slug)}/sample?limit=${limit}`),
  /** 초안 저장 — 사용하지 않는다(사용은 목록의 「사용」). 작성 경로는 「화면」 으로 남는다. */
  saveRecipe: (slug: string, body: Record<string, unknown>, note: string) =>
    api.post<RecipeSaved>(`${slugPath(slug)}/recipes`, { body, note }),
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
  /** 어댑터 종류마다 칸 · 설명 — 새 연결의 입력 칸을 이것으로 그린다. */
  adapterKinds: () => api.get<AdapterKinds>('/relay/adapter-kinds'),
  /** 원천 탐색 — 저장하지 않는다. 부를 수 있는 주소는 울타리 안만(서버가 거절한다). */
  discover: (adapter: Record<string, unknown>) =>
    api.post<DiscoverOut>('/relay/discover', { adapter }),
  /** 정의 판 초안 — 새 slug 면 새 파이프라인(사용하기 전까지 실행되지 않는다). */
  saveDefinition: (slug: string, body: Record<string, unknown>, note: string) =>
    api.post<DefinitionSaved>(`${slugPath(slug)}/definitions`, { body, note }),
  targetSchema: (slug: string) => api.get<TargetSchema>(`${slugPath(slug)}/target-schema`),
  /** 대상에 받을 타입이 없을 때 — 원천 표본으로 SP 타입 정의 초안 · 레시피의 첫 판. 저장하지 않는다. */
  typeDraft: (body: {
    type_slug: string
    label: string
    key_column: string
    label_column: string
    columns: Record<string, string[]>
    system: string
  }) => api.post<TypeDraft>('/relay/type-draft', body),
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
