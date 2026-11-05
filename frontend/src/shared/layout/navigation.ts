/**
 * 사이드바 메뉴 정의 — **화면 목록의 정본이다.**
 *
 * 메뉴를 컴포넌트에서 분리해 두는 이유: 어떤 화면이 있어야 하는지가 한 곳에
 * 적혀 있어야 라우터·사이드바·권한이 서로 어긋나지 않는다. 시험이 검사한다
 * (`backend/tests/architecture/test_boundaries.py`).
 *
 * ## 도메인을 얹을 때
 *
 * 아래 「도메인」 그룹에 항목을 더한다. 화면이 아직 없으면 `pending: true` 로 두면
 * 라우터가 stub 을 만든다 — **자리는 보이되 눌러 보고 알게 하지 않는다.** 제목·
 * 단계·설명을 라우터에 다시 적지 않는 것이 요점이다.
 */

import {
  Bell,
  Building2,
  ChartColumn,
  Database,
  Home,
  ListChecks,
  Megaphone,
  Network,
  ScrollText,
  Server,
  ServerCog,
  UserCog,
  Users,
  Workflow,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

import { iconOf } from '@/shared/icons'

/** 소속 부서를 아직 모를 때 쓰는 임시 slug. 설치 스크립트가 만드는 뿌리 부서다. */
export const DEFAULT_WORKSPACE = 'hq'

/**
 * 누구에게 보이는가.
 *
 * **없으면 누르고 나서 403 을 본다.** 눌러야 권한이 없다는 것을 아는 화면은
 * "할 수 있는 일" 을 알려 주지 못한다.
 *
 * 이것은 **표시**일 뿐 권한이 아니다. 권한은 서버가 판정한다 — 사이드바를 고쳐
 * 우회할 수 있으면 그건 애초에 보안이 아니다.
 */
export type NavAudience = 'everyone' | 'manager' | 'system_admin'

export interface NavItem {
  label: string
  icon: LucideIcon
  /** 고정 경로 */
  to?: string
  /** 부서 스코프 경로 */
  resolve?: (slug: string) => string
  /** NavLink 의 end 옵션 (부모 경로가 자식에도 활성화되지 않게) */
  end?: boolean
  /** 기본은 everyone. */
  audience?: NavAudience
  /** 아직 화면이 없다. 사이드바가 「미구현」 표를 단다 — **자리는 보이되 눌러
   *  보고 알게 하지 않는다.** 화면이 생기면 이 표시를 지운다. */
  pending?: boolean
  /** pending 인 것이 로드맵의 어느 단계에서 들어오는가. */
  phase?: string
  /** 한 줄 설명. 개요 화면과 stub 화면이 같은 말을 하도록 여기 한 번만 적는다. */
  summary?: string
  /** 이름 옆의 작은 표 — 기록 타입이면 「기록」(ADR 0011). */
  badge?: string
}

/** 서버가 주는 동적 묶음 (`modules/ontology/api` 의 NavGroupNode 와 짝). */
export interface DynamicGroup {
  slug: string
  label: string
  icon: string
  audience: string
  /** 상위 묶음의 slug — 없으면 맨 위. 평평하게 와서 `mergeDynamic` 이 두 단계로 세운다. */
  parent?: string | null
  /** 서버가 묶음 안에서 **축을 먼저, 기록을 뒤에** 세워 보낸다(`usage`). */
  items: { label: string; icon: string; to: string; slug: string; usage?: string }[]
}

export interface NavGroup {
  /** 없으면 제목 없이 항목만 선다. **한 항목짜리 그룹에는 제목을 안 단다** —
   *  제목은 「여기 여럿이 있다」 는 신호라서, 하나뿐인데 달면 거짓말이 된다. */
  title?: string
  items: NavItem[]
  /** 그룹 전체가 안 보이는 조건. 항목이 하나도 안 보이면 제목도 지운다. */
  audience?: NavAudience
  /**
   * 하위 묶음 — **한 단계까지다**(상위 묶음 › 묶음 › 타입). 타입이 백 개가 되면 묶음이 평평하게
   * 열 개 늘어서고, 그때 「어디에 속한 것인가」 를 화면이 말해 주지 못한다.
   */
  children?: NavGroup[]
  /**
   * 접힘을 기억할 열쇠. 없으면 제목을 쓴다 — 동적 묶음은 **이름이 겹칠 수 있어**(상위가 다른
   * 두 묶음이 같은 이름) slug 를 준다. 겹치면 한쪽을 접을 때 둘이 함께 접힌다.
   */
  foldKey?: string
}

export const NAV_GROUPS: NavGroup[] = [
  {
    // **제목이 없다.** 홈 하나뿐인데 제목을 달면 「여기 더 있다」 로 읽힌다.
    items: [{ label: '홈', icon: Home, resolve: (s) => `/w/${s}`, end: true }],
  },
  {
    // **이 플랫폼의 일** — 어느 레거시 시스템의 어느 API 가 어느 Standard Platform 의 무엇으로
    // 가나(ADR 0001). 연결 지도가 첫 화면이고, 나머지는 지도에서 눌러 들어가는 자리다.
    title: '중계',
    items: [
      {
        label: '연결 지도',
        icon: Network,
        to: '/relay/map',
        summary:
          '원천 시스템 → API → 파이프라인 → 대상 SP → 타입을 열을 고정한 흐름도로 봅니다. ' +
          '정의(파이프라인 · 레시피 · 대상 스키마)에서 매번 계산하고 손으로 그리지 않습니다.',
      },
      {
        label: '파이프라인',
        icon: Workflow,
        to: '/relay/pipelines',
        summary:
          '원천 + 레시피 + 대상 + 주기 — 칸 대응 표, 실행 이력(받은 건수 · 바뀐 객체 · 삭제 · ' +
          '미해결 · 대상의 계획 링크), 지금 실행.',
      },
      {
        label: '원천 시스템',
        icon: Database,
        to: '/relay/sources',
        summary: 'PLM · MES 같은 레거시 시스템과 그 엔드포인트 — 인증 방식(비밀은 표시하지 않음) · 연결 상태.',
      },
      {
        label: '대상 인스턴스',
        icon: ServerCog,
        to: '/relay/targets',
        summary:
          '받는 Standard Platform(자주검증 Datahub · 제조 데이터허브 …) — 판 · 정의 판 · ' +
          '토큰 만료 · 마지막 연결.',
      },
    ],
  },
  {
    title: '내 활동',
    items: [
      { label: '알림', icon: Bell, to: '/notifications' },
      // **뒤에서 도는 일이 어디까지 됐나.** 파이프라인 실행은 작업이다 — 그 진행과 실패
      // 이유를 볼 자리가 없으면 「대기」 가 영영 대기다.
      { label: '작업', icon: ListChecks, to: '/jobs' },
      { label: '내 정보', icon: UserCog, to: '/me' },
    ],
  },
  {
    title: '공통',
    items: [{ label: '공지', icon: Megaphone, to: '/notices' }],
  },
  {
    title: '내 부서',
    audience: 'manager',
    items: [
      { label: '부서 멤버', icon: Users, resolve: (s) => `/w/${s}/members`, audience: 'manager' },
      {
        // **기록만 쌓이고 볼 자리가 없으면 자산이 아니다.** 여기에는 생성·
        // 수정·삭제가 없다 — 고칠 수 있으면 감사가 아니다.
        label: '변경 이력',
        icon: ScrollText,
        to: '/audit',
        audience: 'manager',
      },
    ],
  },
  {
    title: '관리',
    audience: 'system_admin',
    items: [
      { label: '계정', icon: UserCog, to: '/admin/accounts', audience: 'system_admin' },
      // **전사 부서 목록이다.** 위 '내 부서' 와 헷갈리지 않게 이름을 가른다 —
      // 이쪽은 부서를 만들고 고치는 자리고, 저쪽은 내 부서의 일이다.
      { label: '부서 정보', icon: Building2, to: '/admin/workspaces', audience: 'system_admin' },
      { label: '서버', icon: Server, to: '/admin/server', audience: 'system_admin' },
      // **이 설치가 그릴 수 있는 그림 전부.** 없으면 화면을 얹는 사람이 차트
      // 라이브러리를 제 손으로 부르게 되고, 그때부터 축 색과 범례 규칙이 갈린다.
      {
        label: '차트 보기',
        icon: ChartColumn,
        to: '/admin/charts',
        audience: 'system_admin',
      },
    ],
  },
]

/**
 * 서버가 준 동적 묶음(`/api/ontology/nav`)을 정적 메뉴와 합친다.
 *
 * **정적 화면의 정본은 여전히 이 파일이다.** 동적 그룹은 그 아래 합류할 뿐이고,
 * 라우트는 `/o/:typeSlug` 둘로 받으므로 `router.test.tsx` 의 어긋남 검사가
 * 그대로 선다.
 *
 * 동적 묶음이 하나라도 있으면 **「도메인」 자리표시자를 감춘다** — 실제 도메인
 * 화면 옆에 「(도메인 화면)」 stub 이 함께 서면, 그것을 눌러야 하는지 아닌지를
 * 사람이 매번 판단하게 된다.
 */
export function mergeDynamic(groups: NavGroup[], dynamic: DynamicGroup[]): NavGroup[] {
  if (dynamic.length === 0) return groups

  const asGroup = (group: DynamicGroup): NavGroup => ({
    title: group.label,
    foldKey: group.slug,
    audience: (group.audience as NavAudience) ?? 'everyone',
    items: group.items.map((item) => ({
      label: item.label,
      // 아이콘 이름은 데이터에서 온다(타입 정의의 `icon`). **모르는 이름이면 기본으로
      // 떨어진다** — 메뉴가 통째로 안 뜨는 것보다 낫다. 여기서 전부 같은 그림으로
      // 두면 타입이 열둘쯤 될 때 사이드바가 **이름을 한 자씩 읽어야 하는 목록**이
      // 된다. 눈은 모양을 먼저 잡는데, 모양이 하나뿐이면 그 능력이 안 쓰인다.
      icon: iconOf(item.icon),
      to: item.to,
      // 기록은 축과 같은 묶음에 서되 표를 단다 — 이름만 보고는 「찾는 그것」 인지 「쌓이는
      // 건」 인지 안 갈린다.
      badge: item.usage === 'log' ? '기록' : undefined,
    })),
  })

  // **두 단계로 세운다.** 서버는 평평하게 주고 상위 slug 만 붙여 준다.
  //
  // ⚠️ **먼저 전부 만들고 그다음 붙인다.** 한 바퀴로 하면 상위가 목록에서 **뒤에 오는**
  //    경우(순서는 `sort_order`·이름이 정한다 — 상위를 나중에 만들면 흔하다) 그 자식이
  //    상위를 못 찾아 맨 위에 그대로 섰다. 화면에서는 「상위를 정했는데 사이드바가 안
  //    바뀐다」 로 보인다(실측).
  const made = new Map<string, NavGroup>()
  for (const group of dynamic) made.set(group.slug, asGroup(group))
  const converted: NavGroup[] = []
  for (const group of dynamic) {
    const node = made.get(group.slug)
    if (!node) continue
    const up = group.parent ? made.get(group.parent) : undefined
    // 상위가 목록에 없으면(또는 자기 자신이면) 맨 위로 둔다 — 없는 부모를 기다리다 그 묶음이
    // 화면에서 사라지는 것이 가장 나쁘다.
    if (up && up !== node) {
      up.children = [...(up.children ?? []), node]
      continue
    }
    converted.push(node)
  }

  const withoutPlaceholder = groups.filter(
    (group) => !group.items.every((item) => item.pending && item.to === '/domain'),
  )
  // 홈 바로 아래에 세운다 — **동선이 곧 순서여야 한다.**
  return [withoutPlaceholder[0], ...converted, ...withoutPlaceholder.slice(1)]
}

/**
 * 확장의 그룹을 사이드바에 세운다 — **켜면 그 그룹이 열리고, 끄면 닫힌다.**
 *
 * 확장은 자기 그룹을 제 이름으로 낸다(「확장」 이라는 바구니에 넣지 않는다). 그래야 켜는 일이
 * 「기능 한 덩어리가 생기는 일」 로 읽힌다.
 *
 * **제목이 같으면 합친다.** 확장 둘이 같은 제목을 고르거나 공통 화면과 같은 제목을 쓰면 같은
 * 이름의 그룹이 둘 서고, 사람은 어느 쪽에 무엇이 있는지 매번 다시 찾는다.
 */
export function mergeExtensions(groups: NavGroup[], extension: NavGroup[]): NavGroup[] {
  if (extension.length === 0) return groups

  const fresh: NavGroup[] = []
  const out = groups.map((group) => ({ ...group, items: [...group.items] }))
  for (const group of extension) {
    const twin = group.title ? out.find((one) => one.title === group.title) : undefined
    if (twin) twin.items.push(...group.items)
    else fresh.push(group)
  }
  // 홈 바로 아래에 세운다 — 동선이 곧 순서여야 한다.
  return fresh.length === 0 ? out : [out[0], ...fresh, ...out.slice(1)]
}

export function itemHref(item: NavItem, slug: string): string {
  return item.to ?? item.resolve?.(slug) ?? '/'
}

/** 이 사람에게 보이는가. 서버가 최종 판정을 한다 — 여기는 표시일 뿐이다. */
export function canSee(
  audience: NavAudience | undefined,
  viewer: { isSystemAdmin: boolean; isAnyManager: boolean },
): boolean {
  if (audience === 'system_admin') return viewer.isSystemAdmin
  if (audience === 'manager') return viewer.isSystemAdmin || viewer.isAnyManager
  return true
}

/** 볼 수 있는 것만 남긴 메뉴. **빈 그룹은 제목까지 지운다.** */
export function visibleGroups(
  viewer: {
    isSystemAdmin: boolean
    isAnyManager: boolean
  },
  dynamic: DynamicGroup[] = [],
  /** 이 설치가 켠 확장의 그룹(`src/extensions`). 서버가 켠 것만 온다. */
  extension: NavGroup[] = [],
): NavGroup[] {
  const merged = mergeDynamic(NAV_GROUPS, dynamic)
  // 확장은 동적 묶음 뒤, 공통 화면 앞 — 「이 설치만의 것」 이 「어디에나 있는 것」 보다 앞선다.
  const withExtensions = mergeExtensions(merged, extension)
  // **자식에도 같은 규칙을 적용한다** — 안 그러면 안 보여야 할 묶음이 상위 밑에서 그대로 뜬다.
  const prune = (group: NavGroup): NavGroup | null => {
    if (!canSee(group.audience, viewer)) return null
    const items = group.items.filter((item) => canSee(item.audience, viewer))
    const children = (group.children ?? [])
      .map(prune)
      .filter((one): one is NavGroup => one !== null)
    // 제 항목도 없고 아래도 비면 제목만 남는다 — 그 제목은 「여기 있다」 는 거짓말이다.
    if (items.length === 0 && children.length === 0) return null
    return children.length > 0 ? { ...group, items, children } : { ...group, items }
  }
  return withExtensions.map(prune).filter((one): one is NavGroup => one !== null)
}

/** 아직 화면이 없는 항목들. 라우터가 이것으로 stub 경로를 만든다 — **사이드바가
 *  정본이라** 제목·단계·설명을 두 곳에 적지 않는다. */
export function pendingItems(): NavItem[] {
  return NAV_GROUPS.flatMap((group) => group.items).filter((item) => item.pending && item.to)
}
