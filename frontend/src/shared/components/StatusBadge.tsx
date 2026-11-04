/**
 * 상태 배지 — **말과 색을 한 곳에서 정한다.**
 *
 * 계정 상태·공지 급이 화면마다 제 색을 고르면, 같은 "정지" 가 목록에서는 회색이고
 * 상세에서는 빨강이 된다. 그러면 색이 아무 뜻도 못 갖는다.
 *
 * ## 도메인이 자기 상태를 더할 때
 *
 * 아래에 표를 하나 만들고 `TABLES` 에 건다. **화면에서 색을 직접 고르지 않는다** —
 * 한 번 그렇게 하면 그 뒤로 전부 그렇게 되고, 그때 이 파일은 아무것도 보장하지
 * 못한다.
 */

import { cn } from '@/shared/lib/utils'

type Tone = 'neutral' | 'good' | 'warn' | 'bad'

const TONE_CLASS: Record<Tone, string> = {
  neutral: 'bg-muted text-muted-foreground',
  good: 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-400',
  warn: 'bg-amber-500/10 text-amber-700 dark:text-amber-400',
  bad: 'bg-destructive/10 text-destructive',
}

const ACCOUNT: Record<string, { label: string; tone: Tone }> = {
  active: { label: '정상', tone: 'good' },
  // **승인 대기는 나쁨이 아니라 주의다.** 사고가 아니라 누군가 처리해야 하는 일이다.
  pending: { label: '승인 대기', tone: 'warn' },
  suspended: { label: '정지', tone: 'bad' },
}

const NOTICE: Record<string, { label: string; tone: Tone }> = {
  info: { label: '안내', tone: 'neutral' },
  warning: { label: '주의', tone: 'warn' },
  urgent: { label: '긴급', tone: 'bad' },
}

const WORKSPACE: Record<string, { label: string; tone: Tone }> = {
  // **보관은 나쁨이 아니라 중립이다** — 사고가 아니라 조직의 생애다.
  active: { label: '사용', tone: 'good' },
  archived: { label: '보관', tone: 'neutral' },
}

const OBJECT: Record<string, { label: string; tone: Tone }> = {
  active: { label: '사용', tone: 'good' },
  // **더 이상 쓰지 않음은 나쁨이 아니다.** picker 에서 숨을 뿐, 이미 걸린 값과
  // 관계는 그대로 남는다 — 지운 것이 아니다.
  deprecated: { label: '안 씀', tone: 'neutral' },
}

const TOKEN: Record<string, { label: string; tone: Tone }> = {
  active: { label: '사용', tone: 'good' },
  // **만료는 주의다** — 그 토큰을 아직 넣어 둔 연동이 있으면 지금 인증에 실패하고 있다.
  expired: { label: '만료', tone: 'warn' },
  // 폐기는 사고가 아니라 끝낸 일이다.
  revoked: { label: '폐기', tone: 'neutral' },
}

/** 느린 요청 · 큰 응답(ADR 0026) — 경로 틀 한 줄의 표시. */
const PERFORMANCE: Record<string, { label: string; tone: Tone }> = {
  // **느린 것은 고장이 아니라 주의다** — 빨강은 멎은 것(실패 · 오류)의 색이다.
  slow: { label: '느림', tone: 'warn' },
  large: { label: '큼', tone: 'warn' },
  // 파일 응답은 크고 받는 쪽 속도를 타는 것이 정상이다 — 문턱 판정에서 뺀다는 표시.
  file: { label: '파일', tone: 'neutral' },
}

/** HWAX 포털 로그인(ADR 0028) — 서버 화면의 한 줄. */
const PORTAL: Record<string, { label: string; tone: Tone }> = {
  // **꺼짐은 나쁨이 아니다** — 포털에 붙지 않은 설치의 정상이다.
  off: { label: '꺼짐', tone: 'neutral' },
  on: { label: '켜짐', tone: 'good' },
  // 켰는데 공개 키가 안 닿으면 포털로 오는 사람이 전부 튕긴다 — 멎은 것의 색.
  unreachable: { label: '공개 키 못 받음', tone: 'bad' },
}

/** HWAX 게이트웨이 위임(ADR 0028) — 서버 화면의 한 줄. */
const DELEGATION: Record<string, { label: string; tone: Tone }> = {
  off: { label: '꺼짐', tone: 'neutral' },
  on: { label: '켜짐', tone: 'good' },
  // 허용 주소를 못 읽으면 모든 발급을 거절한다 — 그 사람들의 Claude 가 이 설치를 못 부른다.
  blocked: { label: '모두 거절', tone: 'bad' },
}

/** 관계의 유효 기간(ADR 0039) — 기준일에 그 관계가 어디인가. */
const PERIOD: Record<string, { label: string; tone: Tone }> = {
  current: { label: '유효', tone: 'good' },
  // **지난 관계는 나쁨이 아니라 이력이다** — 지난 구성에 들었던 것이다.
  past: { label: '지난 관계', tone: 'neutral' },
  // 예정은 아직 효력이 없다는 표시 — 오늘 화면에서 빠진 이유를 말한다.
  future: { label: '예정 관계', tone: 'warn' },
}

/** BOM 전개(ADR 0040) — 표 위에 붙는 표시. 수는 배지 옆 글이 말한다. */
const EXPLODE: Record<string, { label: string; tone: Tone }> = {
  // **잘린 목록은 「이게 전부」 가 아니다** — 말 안 하면 그렇게 읽힌다.
  truncated: { label: '잘림', tone: 'warn' },
  // 1개로 셌다는 표시 — 안 붙으면 그 총수량이 맞는 수로 읽힌다.
  missing: { label: '수량 없음', tone: 'warn' },
  // 가린 것은 사고가 아니다 — 보는 사람의 범위 밖이거나 지운 것이고, 그 아래는 셌다.
  hidden: { label: '볼 수 없는 객체', tone: 'neutral' },
  deleted: { label: '삭제된 객체', tone: 'neutral' },
}

/** 계보(ADR 0041) — 파생물의 낡음과 계보 패널의 표시. 까닭은 배지 옆 글이 말한다. */
const LINEAGE: Record<string, { label: string; tone: Tone }> = {
  fresh: { label: '낡지 않음', tone: 'good' },
  // **바로 낡음은 여기서 할 일이 있다** — 다시 고정하거나 다시 만든다.
  direct: { label: '낡음', tone: 'warn' },
  // 위에서 낡음도 주의다 — 이 객체가 아니라 원천 쪽을 먼저 다시 고정해야 풀린다.
  upstream: { label: '위에서 낡음', tone: 'warn' },
  // 잘린 사슬은 「이게 전부」 가 아니다.
  truncated: { label: '잘림', tone: 'warn' },
  // 가린 것은 사고가 아니다 — 보는 사람의 범위 밖이다.
  hidden: { label: '볼 수 없는 객체', tone: 'neutral' },
}

/** 제안된 관계(ADR 0042) — 제안의 상태. */
const PROPOSAL: Record<string, { label: string; tone: Tone }> = {
  // **열린 제안은 주의다** — 나쁨이 아니라 누군가 확정하거나 거절해야 하는 일이다(승인 대기와 같다).
  proposed: { label: '제안', tone: 'warn' },
  accepted: { label: '확정', tone: 'good' },
  // 거절은 사고가 아니라 끝낸 판단이다.
  rejected: { label: '거절', tone: 'neutral' },
}

/** 제안 하나의 결과(ADR 0042) — 만들지 않은 까닭도 결과다. */
const PROPOSAL_RESULT: Record<string, { label: string; tone: Tone }> = {
  created: { label: '제안함', tone: 'good' },
  // 이미 있는 것은 실패가 아니다 — 같은 것을 두 번 만들지 않았다는 말이다.
  open: { label: '이미 열린 제안', tone: 'neutral' },
  connected: { label: '이미 연결됨', tone: 'neutral' },
  // 거절된 적 있음은 주의다 — 누가 이미 「아니다」 라고 했다. 다시 하려면 사람이 정한다.
  rejected_before: { label: '거절된 적 있음', tone: 'warn' },
  error: { label: '오류', tone: 'bad' },
}

/** 롤업의 설정(ADR 0040) — 같은 「무게 합계」 라도 무엇을 어떻게 셌나가 다르다. */
const ROLLUP: Record<string, { label: string; tone: Tone }> = {
  weighted: { label: '수량 반영', tone: 'neutral' },
  leaves_only: { label: '말단만', tone: 'neutral' },
}

/** 함께 쓰는 한 장(자주검증 회의 · 줄)의 상태 — 작성 중 · 작성 완료 · 확정. */
const SHEET: Record<string, { label: string; tone: Tone }> = {
  // **작성 중은 나쁨이 아니다** — 아직 쓰고 있다는 말이다(새 회의는 다 작성 중이다).
  draft: { label: '작성 중', tone: 'neutral' },
  done: { label: '작성 완료', tone: 'good' },
  // 확정은 끝낸 일 — 굳었다는 표시라 좋음의 색.
  confirmed: { label: '확정', tone: 'good' },
}

const TABLES = {
  account: ACCOUNT,
  delegation: DELEGATION,
  explode: EXPLODE,
  lineage: LINEAGE,
  object: OBJECT,
  notice: NOTICE,
  performance: PERFORMANCE,
  period: PERIOD,
  portal: PORTAL,
  proposal: PROPOSAL,
  proposal_result: PROPOSAL_RESULT,
  rollup: ROLLUP,
  sheet: SHEET,
  token: TOKEN,
  workspace: WORKSPACE,
} as const

export function StatusBadge({
  kind,
  value,
  className,
}: {
  kind: keyof typeof TABLES
  value: string
  className?: string
}) {
  // 모르는 값도 **그대로 보여 준다.** 빈 칸으로 두면 데이터가 없는 것처럼 읽히는데,
  // 실제로는 표에 없는 새 값이 들어온 것이다.
  const found = TABLES[kind][value] ?? { label: value, tone: 'neutral' as Tone }
  return (
    <span
      className={cn(
        'inline-flex items-center rounded px-1.5 py-0.5 text-xs font-medium',
        TONE_CLASS[found.tone],
        className,
      )}
    >
      {found.label}
    </span>
  )
}
