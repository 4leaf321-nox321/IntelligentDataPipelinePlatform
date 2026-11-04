/**
 * 공통 틀이 쓰는 API 타입 — **첫 설치에서는 손으로 적혀 있다.**
 *
 * ## 정본은 서버다
 *
 * 손으로 적은 타입은 반드시 서버와 어긋나고, 어긋난 날 화면은 아무 말도 안 하고
 * undefined 를 그린다. 그래서 이 파일은 **임시**다 — `npm install` 이 끝나면
 * 바로 생성으로 갈아탄다:
 *
 *     cd backend  ; python scripts/export_openapi.py
 *     cd frontend ; npm run api:types
 *
 * 그러면 `src/shared/api/schema.d.ts` 가 생기고, 이 파일의 각 타입을 이렇게 바꾼다:
 *
 *     import type { components } from '@/shared/api/schema'
 *     export type CurrentUser = components['schemas']['UserOut']
 *
 * **여기 있는 것은 그때 전부 사라져야 한다.** 남겨 두면 어느 쪽이 정본인지 알 수
 * 없어지고, 그때부터 두 벌은 갈린다.
 *
 * 이 파일이 처음부터 생성물이 아닌 이유는 하나다: 생성기가 `node_modules` 안에
 * 있어서, 저장소를 막 클론한 사람은 **아직 그것을 돌릴 수 없다.** 빌드가 안 되는
 * 틀은 틀이 아니다.
 */

export interface WorkspaceMembership {
  workspace_id: string
  slug: string
  name: string
  /** 개발본부 / 재료시험팀. 같은 이름의 팀이 본부마다 있을 수 있다. */
  path: string
  depth: number
  role: string
}

export interface CurrentUser {
  id: string
  email: string
  display_name: string
  status: string
  is_system_admin: boolean
  must_change_password: boolean
  home_workspace_slug: string | null
  memberships: WorkspaceMembership[]
}

export interface LoginResponse {
  access_token: string
  /** 초 단위. */
  expires_in: number
  user: CurrentUser
}

export interface Workspace {
  id: string
  slug: string
  name: string
  description: string
  parent_slug: string | null
  depth: number
  path: string
  sort_order: number
  is_active: boolean
  created_at: string
  member_count: number
  my_role: string | null
}

export interface WorkspaceOption {
  slug: string
  name: string
  path: string
  depth: number
}

export interface WorkspaceReference {
  table: string
  label: string
  count: number
  /** 지우려면 먼저 정리해야 하는가. */
  blocks_delete: boolean
}

export interface Member {
  user_id: string
  email: string
  display_name: string
  status: string
  role: string
  joined_at: string
}

/** 소속 한 줄 — **이름과 경로를 함께.** slug 만 있으면 화면은 「hq」 라고 쓰게 된다. */
export interface AccountWorkspace {
  slug: string
  name: string
  /** 개발본부 / 재료시험팀 — 같은 이름의 팀이 본부마다 있을 수 있다. */
  path: string
  role: string
  /** 대표 소속인가 — 이 사람이 로그인해서 처음 서는 부서. */
  is_home: boolean
}

export interface Account {
  id: string
  email: string
  display_name: string
  status: string
  is_system_admin: boolean
  must_change_password: boolean
  home_workspace_slug: string | null
  home_workspace_name: string | null
  requested_workspace_slug: string | null
  requested_workspace_name: string | null
  memberships: string[]
  workspaces: AccountWorkspace[]
  created_at: string
  decided_at: string | null
  decision_note: string | null
}

export interface AccountSummary {
  active_system_admins: number
  pending: number
}

export interface TemporaryPassword {
  account: Account
  /** 이 응답에서 한 번만 나온다. */
  temporary_password: string
}

export interface Notice {
  id: string
  title: string
  body: string
  level: string
  is_popup: boolean
  published_at: string | null
  expires_at: string | null
  author_name: string | null
  read: boolean
  created_at: string
}

export interface Notification {
  id: string
  kind: string
  title: string
  body: string | null
  /** 눌렀을 때 갈 곳. **없으면 알림은 읽고 끝나는 글이 된다.** */
  link: string | null
  read_at: string | null
  created_at: string
}

export interface AuditEntry {
  id: string
  action: string
  /** **그때의 이름이다.** 계정이 지워져도 누가 했는지는 남아야 한다. */
  actor_label: string
  actor_client: string | null
  actor_token: string | null
  target_table: string
  target_id: string | null
  target_label: string
  /** 감사 기록마다 모양이 다르다 — 고치기는 `{before, after}`, 나머지는 수 · 목록 · null 도. */
  changes: Record<string, unknown>
  reason: string | null
  /** 접근 로그·파일 로그와 잇는 끈. */
  request_id: string | null
  created_at: string
}

export interface AccessLog {
  id: string
  user_label: string | null
  action: string
  path: string
  method: string
  status_code: number
  request_id: string | null
  client_ip: string | null
  created_at: string
}

export interface Pat {
  id: string
  name: string
  prefix: string
  scopes: string[]
  created_at: string
  expires_at: string | null
  last_used_at: string | null
  revoked_at: string | null
}

/** 홈의 「남은 일」 한 줄. 서버의 레지스트리가 채운다. */
export interface MaintenanceItem {
  key: string
  label: string
  count: number
  link: string | null
  /** info · warning. 경고는 색이 붙는다. */
  severity: string
}

export interface ExtensionState {
  name: string
  /** 하는 일 한 줄 — 확장이 적은 것(`DESCRIPTION`). 자기소개(MCP 안내문)에도 같은 줄이 실린다. */
  description?: string
  enabled: boolean
  /** 화면에서 지정했나. 거짓이면 `.env` 의 기본값이 답한 것이다. */
  pinned: boolean
  updated_at: string | null
}

export interface ServerStatus {
  app_name: string
  /** 기계가 읽는 이름 — DB · 쿠키 · 유닛 이름이 여기서 나온다. */
  app_slug: string
  /** 지금 켜져 있는 확장. 화면의 ENABLED_EXTENSIONS 와 다르면 새로 고침 전이다. */
  extensions: string[]
  /** `.env` 에 적혔는데 이 번들에 없는 이름 — **오타는 여기서만 드러난다.** */
  extensions_unknown: string[]
  version: string
  app_env: string
  /** 비밀번호를 지운 접속 문자열. **어느 DB 를 보고 있는지가 첫 물음이다.** */
  database_url_safe: string
  schema_head: string | null
  schema_current: string | null
  /** DB 가 코드보다 뒤처져 있나. 뒤처지면 새 칸을 읽는 화면이 500 을 낸다. */
  schema_behind: boolean
  disk: {
    path: string
    total_bytes: number
    free_bytes: number
    used_percent: number
  } | null
  /** 마지막 백업. **「없다」 와 「설정이 없다」 는 다른 일이다.** */
  backup: {
    configured: boolean
    path: string | null
    last_at: string | null
    age_hours: number | null
    stale: boolean
    problem: string | null
  }
  /** 뜻으로 찾기의 바탕(ADR 0050) — pgvector · Ollama · 모델. 배포는 깔다 실패해도 안 멈추므로 여기서 드러난다. */
  embedding: {
    /** ready · off(EMBEDDING_BACKEND=off) · problem */
    state: string
    backend: string
    model: string
    url: string
    vector_version: string | null
    dim: number | null
    problem: string | null
  }
  counts: { label: string; count: number }[]
  started_at: string
}
