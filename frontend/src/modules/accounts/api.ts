/** 계정 관리 API — 시스템 관리자 전용. */

import { api } from '@/shared/api/client'
import type { Page } from '@/shared/api/paging'
import type {
  Account,
  AccountSummary,
  AccountWorkspace,
  Pat,
  TemporaryPassword,
} from '@/shared/api/types'

export type { Account, AccountSummary, AccountWorkspace, Pat, TemporaryPassword }

export const accountApi = {
  summary: () => api.get<AccountSummary>('/accounts/summary'),
  /**
   * 한 쪽씩 — **전체 수(`total`)와 함께 온다.** 맨 리스트로 받던 때는 처음 50명만 그리고
   * 끝이었다(서버 상한, 2026-10-08). `status` 를 주면 그 상태만 세고 읽는다.
   */
  list: (query: { status?: string | null; limit: number; offset: number }) => {
    const params = new URLSearchParams({
      limit: String(query.limit),
      offset: String(query.offset),
    })
    if (query.status) params.set('status', query.status)
    return api.get<Page<Account>>(`/accounts?${params}`)
  },
  create: (body: Record<string, unknown>) => api.post<TemporaryPassword>('/accounts', body),
  approve: (id: string, body: { workspace_slug?: string | null; role?: string }) =>
    api.post<Account>(`/accounts/${id}/approve`, body),
  reject: (id: string, note: string) => api.post<Account>(`/accounts/${id}/reject`, { note }),
  suspend: (id: string) => api.post<Account>(`/accounts/${id}/suspend`),
  activate: (id: string) => api.post<Account>(`/accounts/${id}/activate`),
  /**
   * 소속을 **통째로** 정한다 — 적힌 부서가 소속의 전부가 된다.
   *
   * 「빼기」 와 「넣기」 를 따로 보내지 않는 이유: 화면은 지금 소속을 보고 고친 결과를
   * 보낸다. 차이를 화면이 계산하면, 그 사이에 다른 관리자가 넣은 부서를 모른 채 지운다.
   */
  setWorkspaces: (
    id: string,
    body: { workspace_slugs: string[]; home_workspace_slug?: string | null },
  ) => api.put<Account>(`/accounts/${id}/workspaces`, body),
  setHome: (id: string, workspaceSlug: string) =>
    api.post<Account>(`/accounts/${id}/home-workspace`, { workspace_slug: workspaceSlug }),
  setSystemAdmin: (id: string, grant: boolean) =>
    api.post<Account>(`/accounts/${id}/system-admin`, { is_system_admin: grant }),
  resetPassword: (id: string) => api.post<TemporaryPassword>(`/accounts/${id}/reset-password`),
  remove: (id: string) => api.delete<Account>(`/accounts/${id}`),
  /** 그 사람의 액세스 토큰 — **폐기된 것까지**(언제 끊겼는지도 이 목록이 답한다). */
  tokens: (id: string) => api.get<Pat[]>(`/accounts/${id}/tokens`),
  /**
   * 그 사람의 토큰 **하나**를 폐기한다 — 계정은 그대로 두고 그 연동만 끊는다. 사유는 감사
   * 기록과 토큰 주인의 알림에 실린다. 이미 폐기된 것은 409.
   */
  revokeToken: (id: string, tokenId: string, reason: string) =>
    api.post<Pat>(`/accounts/${id}/tokens/${tokenId}/revoke`, {
      reason: reason.trim() || null,
    }),
}
