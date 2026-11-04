/** 부서 API. */

import type { Job } from '@/modules/jobs/api'
import { api, downloadFile } from '@/shared/api/client'
import type { Member, Workspace, WorkspaceOption, WorkspaceReference } from '@/shared/api/types'

export type { Member, Workspace, WorkspaceOption, WorkspaceReference }

/** 이 부서가 **가진** 것 한 종류 — 통폐합 때 다른 부서로 넘길 수 있다. */
export interface WorkspaceContent {
  kind: string
  label: string
  count: number
}

/** 자료 이동의 한 종류 — 계획이면 옮길 수(`count`), 적용이면 옮긴 수(`moved`)까지. */
export interface ReassignItem {
  kind: string
  label: string
  count: number
  moved?: number
}

/**
 * 부서 자료 이동(통폐합)의 계획 · 적용 — 작업 `workspace_reassign` 의 `result`
 * (`workspaces/services._reassign_out`). 통폐합 창과 작업 화면이 같은 모양을 그린다.
 *
 * `errors` 는 **막히는 것**(부서 안에서 하나여야 할 식별자가 대상 부서와 겹침) — 하나라도 있으면
 * 적용하지 않는다. 적용할 것이 있나는 `ok`.
 */
export interface ReassignResult {
  reassign: true
  applied: boolean
  ok: boolean
  source_slug: string
  source_name: string
  target_slug: string
  target_name: string
  items: ReassignItem[]
  total: number
  moved: Record<string, number>
  errors: string[]
  summary: string
}

/** 작업 결과가 자료 이동의 것인가 — 아니면 null. */
export function reassignOf(result: unknown): ReassignResult | null {
  if (!result || typeof result !== 'object') return null
  const found = result as Partial<ReassignResult>
  return found.reassign === true && Array.isArray(found.items) && Array.isArray(found.errors)
    ? (found as ReassignResult)
    : null
}

export interface WorkspaceImportRow {
  row: number
  slug: string
  action: 'create' | 'update' | 'unchanged' | 'skip' | 'error'
  label: string
  changes: string[]
  message: string
}

export interface WorkspaceImportPlan {
  applied: boolean
  rows: WorkspaceImportRow[]
  errors: string[]
  counts: Record<'create' | 'update' | 'unchanged' | 'skip' | 'error', number>
}

export const workspaceApi = {
  /** 붙여 넣은 표(다른 플랫폼의 내보내기)로 부서를 넣는다 — 계획(apply=false) 또는 적용. */
  importText: (text: string, apply: boolean) =>
    api.post<WorkspaceImportPlan>('/workspaces/import', { text, apply }),
  /**
   * 부서 정보를 CSV 로 내려받는다.
   *
   * 평범한 `<a href>` 로는 안 된다. access 토큰은 메모리에만 있어서 브라우저가
   * 스스로 여는 주소에는 안 실리고, 그러면 새 탭에서 401 이 나는데 **화면에는
   * 아무 표시도 안 뜬다** — 사용자는 아무 일도 안 일어난 것처럼 본다.
   */
  exportCsv: () => downloadFile('/workspaces/export.csv', '부서정보.csv'),

  options: () => api.get<WorkspaceOption[]>('/workspaces/options'),
  list: (all = false) => api.get<Workspace[]>(`/workspaces${all ? '?all=true' : ''}`),
  create: (body: {
    slug: string
    name: string
    description?: string
    parent_slug?: string | null
  }) => api.post<Workspace>('/workspaces', body),
  update: (slug: string, body: Record<string, unknown>) =>
    api.patch<Workspace>(`/workspaces/${slug}`, body),
  /**
   * 상위와 **형제 사이 자리까지 한 번에.** 끌어 놓기가 이것을 부른다.
   *
   * 형제 순서를 화면이 제 손으로 다시 매겨 여러 번 저장하면, 중간에 하나가 실패했을
   * 때 트리가 반쯤 뒤섞인 채로 남는다 — 서버가 한 트랜잭션으로 한다.
   */
  move: (slug: string, parentSlug: string | null, position?: number) =>
    api.post<Workspace>(`/workspaces/${slug}/move`, {
      parent_slug: parentSlug,
      ...(position === undefined ? {} : { position }),
    }),
  reorder: (slug: string, direction: 'up' | 'down') =>
    api.post<Workspace>(`/workspaces/${slug}/reorder`, { direction }),
  /** **누르기 전에 무엇이 딸려 있는지 보여 준다.** */
  references: (slug: string) => api.get<WorkspaceReference[]>(`/workspaces/${slug}/references`),
  /** 옮길 수 있는 것 — **0 건도 온다.** 빠지면 사람은 그 종류가 안 옮겨지는 줄 안다. */
  contents: (slug: string) => api.get<WorkspaceContent[]>(`/workspaces/${slug}/contents`),
  /**
   * 자료를 다른 부서로 통째 옮긴다 — 부서 통폐합의 앞 단계. **작업의 계획을 넣는다**(202) —
   * 무엇이 몇 건 옮겨지나 · 무엇이 막히나를 워커가 세고, 사람이 읽고 `jobsApi.apply` 로 적용한다.
   *
   * 예전에는 요청 안에서 옮기고 옮긴 수를 돌려줬다 — 한 부서 객체 20만 개면 90초 남짓이라
   * 브라우저 · 이중화의 nginx 가 먼저 끊었다(2026-10-10).
   */
  reassign: (slug: string, targetSlug: string, kinds: string[]) =>
    api.post<Job>(`/workspaces/${slug}/reassign`, {
      target_slug: targetSlug,
      kinds,
    }),
  remove: (slug: string) => api.delete<void>(`/workspaces/${slug}`),

  members: (slug: string) => api.get<Member[]>(`/workspaces/${slug}/members`),
  addMember: (slug: string, email: string, role: string) =>
    api.post<Member>(`/workspaces/${slug}/members`, { email, role }),
  setRole: (slug: string, userId: string, role: string) =>
    api.patch<Member>(`/workspaces/${slug}/members/${userId}`, { role }),
  removeMember: (slug: string, userId: string) =>
    api.delete<void>(`/workspaces/${slug}/members/${userId}`),
}
