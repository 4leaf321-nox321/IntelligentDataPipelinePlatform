/**
 * 인증 관련 타입.
 *
 * 모양은 `@/shared/api/types` 가 든다 — 그리고 그것은 `npm run api:types` 를
 * 돌리는 순간 생성된 `schema.d.ts` 로 갈아탄다. 여기는 **이름만 다시 내보내는
 * 자리**이므로 그 전환에 아무것도 안 바뀐다.
 */

export type {
  CurrentUser,
  LoginResponse,
  WorkspaceMembership,
} from '@/shared/api/types'
