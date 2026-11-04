/**
 * 앱 껍데기 — 사이드바 + 헤더 + 본문.
 *
 * **본문만 스크롤한다.** 헤더·사이드바가 함께 스크롤되면 긴 화면에서 지금 어디
 * 있는지를 잃는다.
 */

import { Suspense, useState } from 'react'
import { Outlet, useLocation, useParams } from 'react-router-dom'

import { ExtensionsProvider } from '@/extensions'
import { useAuth } from '@/shared/auth/AuthContext'
import { ErrorBoundary } from '@/shared/components/ErrorBoundary'
import { Skeleton } from '@/shared/components/ui/skeleton'
import { Header } from '@/shared/layout/Header'
import { DEFAULT_WORKSPACE } from '@/shared/layout/navigation'
import { NewBuildBanner } from '@/shared/layout/NewBuildBanner'
import { NoticePopup } from '@/modules/notices/NoticePopup'
import { Sidebar, SidebarDrawer } from '@/shared/layout/Sidebar'

/** 화면 조각을 받아 오는 동안. **빈 화면을 보이지 않는다.** */
function PageSkeleton() {
  return (
    <div className="space-y-3">
      <Skeleton className="h-8 w-56" />
      <Skeleton className="h-4 w-80" />
      <Skeleton className="h-64 w-full" />
    </div>
  )
}

export function AppShell() {
  const [collapsed, setCollapsed] = useState(false)
  const [drawer, setDrawer] = useState(false)
  const { slug } = useParams<{ slug?: string }>()
  const { pathname } = useLocation()
  const { user } = useAuth()

  // 부서 스코프가 아닌 화면(공지·관리)에서도 사이드바의 '홈'·'부서 멤버' 는
  // **어느 부서인지** 정해야 한다. 고정값으로 두면 자기 부서가 아닌 곳을 가리켜
  // 목록이 비어 보이고, 그것은 데이터가 없는 것과 구별이 안 된다.
  const workspaceSlug =
    slug ?? user?.home_workspace_slug ?? user?.memberships[0]?.slug ?? DEFAULT_WORKSPACE

  return (
    // **켜진 확장은 서버가 답한다**(ExtensionsProvider) — 사이드바와 확장 경로가 같은 목록을
    // 보므로, 켜고 끈 것이 새로 고침 없이 따라온다.
    <ExtensionsProvider>
      {/* **인쇄는 본문만, 쪽을 넘겨 끝까지** — 화면의 틀(높이 고정 · 본문만 스크롤)을 그대로 찍으면
          첫 쪽의 보이는 만큼만 나온다. 메뉴 · 머리는 각자 `print:hidden` 이다. */}
      <div className="flex h-svh overflow-hidden print:block print:h-auto print:overflow-visible">
      <Sidebar collapsed={collapsed} workspaceSlug={workspaceSlug} />
      <SidebarDrawer open={drawer} onOpenChange={setDrawer} workspaceSlug={workspaceSlug} />

      <div className="flex min-w-0 flex-1 flex-col">
        {/* 서버가 새 판이면 맨 위에 「새로 고침」 띠 — 열어 둔 탭이 옛 조각을 찾다 멈추기 전에. */}
        <NewBuildBanner />
        <Header
          // **같은 단추가 화면 폭에 따라 다른 일을 한다.** 넓으면 붙박이
          // 사이드바를 접고, 좁으면(md 미만, 사이드바가 아예 없다) 서랍을 연다.
          onToggleSidebar={() => {
            if (window.matchMedia('(min-width: 768px)').matches) {
              setCollapsed((value) => !value)
            } else {
              setDrawer(true)
            }
          }}
          workspaceSlug={workspaceSlug}
        />
        <main className="flex-1 overflow-auto p-6 print:overflow-visible print:p-0">
          {/* **본문은 폭을 다 쓴다.** 상한을 두면 넓은 표가 접히고, 그때마다
              「이 화면도 예외로」 가 반복돼 목록이 곧 전부가 된다. 좁아야 하는
              화면은 자기 안에서 다시 좁힌다. */}
          {/* **경계는 본문에만 두른다.** 사이드바와 헤더는 살아 있어야 사람이
              다른 화면으로 나갈 수 있다 — 앱 전체를 감싸면 한 화면이 터졌을 때
              나갈 길까지 함께 사라진다.

              주소가 바뀌면 경계가 풀린다(resetKey). 안 풀면 한 번 터진 뒤로 앱이
              오류 화면에 갇힌다. */}
          <div className="w-full">
            <ErrorBoundary resetKey={pathname}>
              <Suspense fallback={<PageSkeleton />}>
                <Outlet />
              </Suspense>
            </ErrorBoundary>
          </div>
        </main>
      </div>

        {/* 읽지 않은 팝업 공지는 스스로 뜬다 — 공지 화면에 들어가야만 보이면
            "배포 없이 안내를 전한다" 는 목적이 성립하지 않는다. */}
        <NoticePopup />
      </div>
    </ExtensionsProvider>
  )
}
