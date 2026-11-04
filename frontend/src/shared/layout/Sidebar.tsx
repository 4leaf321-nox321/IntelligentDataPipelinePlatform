/**
 * 사이드바 — 접으면 폭 0으로 줄어들고 본문이 전체 폭을 쓴다.
 *
 * 폭을 0으로 만들되 내부 래퍼는 고정폭을 유지한다. 그래야 접힐 때 글자가
 * 찌그러지지 않고 그대로 잘려 나간다.
 *
 * **묶음은 하나씩 접는다.** 타입이 늘면 사이드바가 길어지고, 그때 사람은 자기가 쓰는 묶음을
 * 찾으려고 매번 스크롤한다 — 안 쓰는 묶음을 접어 두면 그 일이 없어진다. 접은 것은 브라우저에
 * 기억한다(사람마다 쓰는 묶음이 다르니 서버가 알 일이 아니다).
 */

import { useCallback, useState } from 'react'
import { ChevronRight } from 'lucide-react'
import { NavLink, useLocation } from 'react-router-dom'

import { UNKNOWN_VERSION, systemApi } from '@/shared/api/system'
import { useAuth } from '@/shared/auth/AuthContext'
import { isAnyManager, isSystemAdmin } from '@/shared/auth/roles'
import { APP_NAME, APP_TAGLINE, STORAGE_PREFIX } from '@/shared/branding'
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '@/shared/components/ui/sheet'
import { useResource } from '@/shared/hooks/useResource'
import { extensionNavGroups, useEnabledExtensions } from '@/extensions'
import type { NavGroup } from '@/shared/layout/navigation'
import { itemHref, visibleGroups } from '@/shared/layout/navigation'
import { cn } from '@/shared/lib/utils'

/** 접어 둔 묶음. **한 서버에 두 플랫폼이 있을 수 있어** 키에 slug 를 붙인다. */
const STORAGE_KEY = `${STORAGE_PREFIX}.sidebar.folded`

function readFolded(): string[] {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    const parsed: unknown = raw ? JSON.parse(raw) : []
    return Array.isArray(parsed)
      ? parsed.filter((one): one is string => typeof one === 'string')
      : []
  } catch {
    // 사생활 보호 창이나 저장을 막은 브라우저 — 접힘은 편의일 뿐이라 그냥 다 펼친다.
    return []
  }
}

/** 묶음 접기 상태. 제목이 열쇠다 — 동적 묶음은 서버가 준 이름을 쓴다. */
function useFoldedGroups() {
  const [folded, setFolded] = useState<string[]>(readFolded)
  const toggle = useCallback((title: string) => {
    setFolded((current) => {
      const next = current.includes(title)
        ? current.filter((one) => one !== title)
        : [...current, title]
      try {
        window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next))
      } catch {
        // 위와 같다 — 이번 화면에서만 기억한다.
      }
      return next
    })
  }, [])
  return { folded, toggle }
}

/** 이 묶음(또는 그 아래)에 **지금 보는 화면**이 있나 — 접었을 때 점을 찍는 근거. */
function holdsActive(group: NavGroup, pathname: string, workspaceSlug: string): boolean {
  const mine = group.items.some((item) => {
    const href = itemHref(item, workspaceSlug)
    return item.end ? pathname === href : pathname.startsWith(href)
  })
  return mine || (group.children ?? []).some((one) => holdsActive(one, pathname, workspaceSlug))
}

/**
 * 묶음 한 덩어리 — 제 항목과 **하위 묶음**을 그린다.
 *
 * 깊이는 **한 단계까지**다(상위 묶음 › 묶음 › 타입). 더 깊이 가면 접힘 상태 · 현재 경로 표시 ·
 * 들여쓰기가 단계마다 곱해지고, 사이드바는 고장나면 **나갈 길이 통째로 사라지는** 자리다.
 */
function GroupBlock({
  group,
  depth,
  folded,
  toggle,
  pathname,
  workspaceSlug,
  onNavigate,
}: {
  group: NavGroup
  depth: number
  folded: string[]
  toggle: (key: string) => void
  pathname: string
  workspaceSlug: string
  onNavigate?: () => void
}) {
  // **제목이 있어야 접을 수 있다.** 제목이 없는 묶음은 홈 하나뿐이라 접을 것도 없고,
  // 접는 단추를 둘 자리도 없다.
  const title = group.title
  const key = group.foldKey ?? title
  const isFolded = Boolean(key && folded.includes(key))
  // 접힌 묶음 안에 **지금 보는 화면**이 있으면 점을 찍는다 — 접었다고 「어디 있는지」 를
  // 모르게 두면, 사람은 묶음을 하나씩 펴 가며 찾는다.
  const hasActive = holdsActive(group, pathname, workspaceSlug)
  const children = group.children ?? []
  return (
    <div className={cn(depth > 0 && 'mt-2 ml-2 border-l pl-2')}>
      {/* **제목이 없으면 자리도 안 남긴다.** 빈 문단을 두면 홈 위에 설명 없는 여백이
          생겨 「뭔가 안 나온다」 로 읽힌다. */}
      {title && key && (
        <button
          type="button"
          aria-expanded={!isFolded}
          onClick={() => toggle(key)}
          className={cn(
            // **글자 크기는 한 벌이다**(`text-sm` — 항목과 같다). 제목만 작게 두면 한 열에
            // 크기가 둘 섞이고, 단계가 둘이 되면서 그것이 세 줄마다 번갈아 나온다 — 단계는
            // **들여쓰기 · 굵기 · 색**으로 가른다.
            'hover:text-foreground flex w-full items-center gap-1 rounded px-2 py-1 text-sm',
            depth === 0 && children.length > 0
              ? // 상위 묶음은 그 구역의 머리다 — 굵기로 선다.
                'text-foreground/80 font-semibold'
              : 'text-muted-foreground font-medium',
          )}
        >
          <ChevronRight
            className={cn('size-3.5 shrink-0 transition-transform', !isFolded && 'rotate-90')}
          />
          <span className="truncate">{title}</span>
          {isFolded && hasActive && (
            <span
              className="bg-primary ml-auto size-1.5 shrink-0 rounded-full"
              title="지금 보는 화면이 이 묶음 안에 있습니다"
            />
          )}
        </button>
      )}
      {/* **접으면 그리지 않는다.** CSS 로 숨기면 접힌 묶음의 링크가 탭 이동과 읽기
          프로그램에는 그대로 남아, 접었는데도 거기로 갈 수 있다. */}
      {!isFolded && (
        <>
          {group.items.length > 0 && (
            <ul className="space-y-0.5">
              {group.items.map((item) => (
                <li key={item.label}>
                  <NavLink
                    to={itemHref(item, workspaceSlug)}
                    end={item.end}
                    onClick={onNavigate}
                    className={({ isActive }) =>
                      cn(
                        'flex items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors',
                        isActive
                          ? 'bg-sidebar-accent text-sidebar-accent-foreground font-medium'
                          : 'text-muted-foreground hover:bg-sidebar-accent/60 hover:text-foreground',
                      )
                    }
                  >
                    <item.icon className="size-4 shrink-0" />
                    <span className="truncate">{item.label}</span>
                    {item.pending && (
                      <span className="text-muted-foreground/70 ml-auto shrink-0 rounded border px-1 text-[10px] leading-4">
                        미구현
                      </span>
                    )}
                    {item.badge && !item.pending && (
                      <span className="text-muted-foreground/70 ml-auto shrink-0 rounded border px-1 text-[10px] leading-4">
                        {item.badge}
                      </span>
                    )}
                  </NavLink>
                </li>
              ))}
            </ul>
          )}
          {children.map((child) => (
            <GroupBlock
              key={child.foldKey ?? child.title}
              group={child}
              depth={depth + 1}
              folded={folded}
              toggle={toggle}
              pathname={pathname}
              workspaceSlug={workspaceSlug}
              onNavigate={onNavigate}
            />
          ))}
        </>
      )}
    </div>
  )
}

interface SidebarProps {
  collapsed: boolean
  workspaceSlug: string
  onNavigate?: () => void
}

function SidebarBody({ workspaceSlug, onNavigate }: Omit<SidebarProps, 'collapsed'>) {
  const { user } = useAuth()
  // **서버가 정본이다.** 번들에 박으면 그것은 빌드된 버전이지 지금 도는 서버가
  // 아니다 — 배포가 반쯤 끝난 상태에서 둘이 갈리고, 그때 화면이 거짓말을 한다.
  const health = useResource(() => systemApi.health(), [])
  const release = health.data?.version
  // 서버가 이 빌드와 다른 버전인가 — 여기서는 **개발에서만 본다**(개발 화면이 옛 서버에
  // 붙어 있는 것). 운영에서도 갈린다 — 업데이트 전부터 열어 둔 탭, 이중화를 한 대씩 올리는
  // 동안. 그것은 맨 위의 「새 판이 나왔습니다」 띠(`NewBuildBanner`)가 말한다.
  const stale =
    import.meta.env.DEV && !!release && release !== UNKNOWN_VERSION && release !== __APP_VERSION__

  // **볼 수 있는 것만 보여 준다.** 눌러야 403 을 아는 메뉴는 "할 수 있는 일" 을
  // 알려 주지 못한다. 권한은 서버가 판정한다 — 여기는 표시일 뿐이다.
  const enabled = useEnabledExtensions()

  const groups = visibleGroups(
    {
      isSystemAdmin: isSystemAdmin(user),
      isAnyManager: isAnyManager(user),
    },
    // 틀(StandardPlatform)의 동적 묶음(온톨로지 정의가 만든 메뉴)은 없다 — 온톨로지를 안
    // 가져왔다(ADR 0001).
    [],
    // **켜진 목록은 서버가 답한다** — 메타만 믿으면 껐는데 메뉴가 남는다(실측).
    extensionNavGroups(enabled),
  )

  const { folded, toggle } = useFoldedGroups()
  const { pathname } = useLocation()

  return (
    <div className="flex h-full w-60 flex-col">
      <div className="flex h-14 shrink-0 flex-col justify-center border-b px-4">
        <span className="text-base leading-tight font-semibold tracking-tight">{APP_NAME}</span>
        <span className="text-muted-foreground text-xs leading-tight">
          {APP_TAGLINE}
          {/* **못 찾았으면 안 적는다.** unknown 을 그대로 띄우면 버전 자리가
              고장난 것처럼 보이는데, 실제로는 개발 경로에서 돈다는 뜻이다. */}
          {release && release !== UNKNOWN_VERSION && (
            <span
              className={cn(
                'font-mono',
                APP_TAGLINE && 'ml-1.5',
                stale && 'font-semibold text-amber-600',
              )}
              title={
                stale
                  ? `이 화면은 ${__APP_VERSION__} 인데 서버는 ${release} 입니다. ` +
                    '다른 서버에 붙어 있을 수 있습니다.'
                  : '지금 도는 서버의 버전입니다'
              }
            >
              {/* **버전 글자는 제 노드에 둔다.** 배지를 형제로 붙이면 글자가
                  이어져 버전만으로는 찾을 수 없게 된다. */}
              <span>{release}</span>
              {stale && <span className="ml-1">!= {__APP_VERSION__}</span>}
            </span>
          )}
        </span>
      </div>

      <nav className="flex-1 space-y-5 overflow-y-auto px-2 py-4">
        {groups.map((group) => (
          <GroupBlock
            key={group.foldKey ?? group.title ?? group.items[0]?.label}
            group={group}
            depth={0}
            folded={folded}
            toggle={toggle}
            pathname={pathname}
            workspaceSlug={workspaceSlug}
            onNavigate={onNavigate}
          />
        ))}
      </nav>
    </div>
  )
}

/**
 * 좁은 화면의 메뉴 — **서랍으로 연다.**
 *
 * 옆의 사이드바는 md 미만에서 아예 안 그려진다. 상단의 접기 단추는 폭만 바꾸므로,
 * **좁은 화면에서는 눌러도 아무 일이 없고 메뉴로 가는 길이 하나도 없다** — 주소를
 * 직접 치지 않으면 다른 화면에 못 간다.
 *
 * 고르면 닫는다. 서랍이 덮은 채로 두면 방금 연 화면을 못 본다.
 */
export function SidebarDrawer({
  open,
  onOpenChange,
  workspaceSlug,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  workspaceSlug: string
}) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="left" className="bg-sidebar w-72 p-0 md:hidden">
        <SheetHeader className="sr-only">
          <SheetTitle>메뉴</SheetTitle>
        </SheetHeader>
        <SidebarBody workspaceSlug={workspaceSlug} onNavigate={() => onOpenChange(false)} />
      </SheetContent>
    </Sheet>
  )
}

export function Sidebar({ collapsed, workspaceSlug }: SidebarProps) {
  return (
    <aside
      data-collapsed={collapsed}
      aria-hidden={collapsed}
      className={cn(
        // 인쇄에는 본문만 — 한 장 보고서(자주검증 …)에 메뉴가 찍히면 그 자리만큼 표가 좁아진다.
        'bg-sidebar hidden h-full shrink-0 flex-col overflow-hidden md:flex print:hidden',
        'transition-[width] duration-200 ease-in-out',
        collapsed ? 'w-0 border-r-0' : 'w-60 border-r',
      )}
    >
      <SidebarBody workspaceSlug={workspaceSlug} />
    </aside>
  )
}
