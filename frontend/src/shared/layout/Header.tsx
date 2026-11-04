/**
 * 상단 바 — 사이드바 토글 · 부서 선택 · 테마 · 알림 · 계정 메뉴.
 *
 * 부서 선택기는 **내가 속한 부서만** 보여 준다. 시스템 관리자라도 여기서는 자기
 * 소속만 오간다 — 전사 목록은 부서 관리 화면의 일이다. 두 목적을 한 위젯에
 * 섞으면 "내 부서" 라는 개념이 흐려진다.
 *
 * **검색 칸은 결과를 그리지 않는다.** 친 말을 `/search?q=` 로 넘길 뿐이다. 좁은
 * 드롭다운에 결과를 떨구면 무엇을 찾았는지 안 보이고, 주소로 공유도 안 되고,
 * 뒤로가기로 돌아갈 수도 없다 — 검색은 화면 하나를 가질 만한 일이다.
 */

import { useState } from 'react'
import { KeyRound, LogOut, Moon, PanelLeft, Search, Sun, User, UserCog } from 'lucide-react'
import { useNavigate, useParams } from 'react-router-dom'

import { useAuth } from '@/shared/auth/AuthContext'
import { Button } from '@/shared/components/ui/button'
import { Input } from '@/shared/components/ui/input'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/shared/components/ui/dropdown-menu'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/shared/components/ui/select'
import { Separator } from '@/shared/components/ui/separator'
import { ChangePasswordDialog } from '@/shared/layout/ChangePasswordDialog'
import { NotificationBell } from '@/shared/layout/NotificationBell'
import { useTheme } from '@/shared/theme/ThemeProvider'

interface HeaderProps {
  onToggleSidebar: () => void
  workspaceSlug: string
}

export function Header({ onToggleSidebar, workspaceSlug }: HeaderProps) {
  const { theme, toggle } = useTheme()
  const { user, logout } = useAuth()
  const [changingPassword, setChangingPassword] = useState(false)
  const [query, setQuery] = useState('')
  const navigate = useNavigate()
  const params = useParams<{ slug?: string }>()

  const memberships = user?.memberships ?? []
  const current = memberships.find((one) => one.slug === workspaceSlug)

  async function signOut() {
    await logout()
    navigate('/login', { replace: true })
  }

  function switchTo(slug: string) {
    // 부서 스코프 화면에 있으면 같은 화면의 다른 부서로, 아니면 그 부서 홈으로.
    const suffix = params.slug ? window.location.pathname.split(`/w/${params.slug}`)[1] : ''
    navigate(`/w/${slug}${suffix ?? ''}`)
  }

  return (
    <header className="bg-background flex h-14 shrink-0 items-center gap-2 border-b px-3 print:hidden">
      <Button
        variant="ghost"
        size="icon"
        onClick={onToggleSidebar}
        aria-label="사이드바 접기/확장"
      >
        <PanelLeft className="size-4" />
      </Button>

      <Separator orientation="vertical" className="mx-1 h-6" />

      {/* 결과는 여기 안 그린다 — 화면으로 넘긴다. 주소에 남아야 공유되고 뒤로가기가 산다. */}
      <form
        className="relative w-40 sm:w-64"
        onSubmit={(event) => {
          event.preventDefault()
          const word = query.trim()
          if (!word) return
          navigate(`/search?q=${encodeURIComponent(word)}`)
          setQuery('')
        }}
      >
        <Search className="text-muted-foreground absolute top-1/2 left-2 size-4 -translate-y-1/2" />
        <Input
          className="h-8 pl-8"
          value={query}
          placeholder="검색"
          aria-label="전체에서 검색"
          onChange={(event) => setQuery(event.target.value)}
        />
      </form>

      <Separator orientation="vertical" className="mx-1 h-6" />

      {/* **경로가 보이는 선택기.** 소속이 여러 곳이면 같은 이름의 팀이 둘일 수
          있고, 이름만 보여 주면 지금 어느 부서에 있는지 알 수 없다. */}
      {memberships.length > 0 ? (
        <Select value={workspaceSlug} onValueChange={switchTo}>
          <SelectTrigger className="h-8 max-w-64 border-0 shadow-none">
            <SelectValue placeholder={workspaceSlug} />
          </SelectTrigger>
          <SelectContent>
            {memberships.map((one) => (
              <SelectItem key={one.slug} value={one.slug}>
                {one.path}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      ) : (
        // **소속이 없다는 것을 말한다.** 빈 칸으로 두면 사람은 목록이 안 뜬 줄
        // 알고 새로고침을 반복한다 — 실제로는 관리자가 배정해 줘야 하는 상태다.
        <span className="text-muted-foreground text-sm">소속된 부서가 없습니다</span>
      )}

      {current?.role === 'manager' && (
        <span className="text-muted-foreground text-xs">부서 관리자</span>
      )}

      <div className="flex-1" />

      <NotificationBell />

      <Button variant="ghost" size="icon" onClick={toggle} aria-label="테마 전환">
        {theme === 'dark' ? <Sun className="size-4" /> : <Moon className="size-4" />}
      </Button>

      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" size="sm">
            <User className="size-4" />
            {user?.display_name ?? '계정'}
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-56">
          <DropdownMenuLabel className="font-normal">
            <p className="text-sm font-medium">{user?.display_name}</p>
            <p className="text-muted-foreground truncate text-xs">{user?.email}</p>
            {user?.is_system_admin && (
              <p className="text-muted-foreground mt-1 text-xs">시스템 관리자</p>
            )}
          </DropdownMenuLabel>
          <DropdownMenuSeparator />
          <DropdownMenuItem onClick={() => navigate('/me')}>
            <UserCog className="size-4" />내 정보
          </DropdownMenuItem>
          <DropdownMenuItem onClick={() => setChangingPassword(true)}>
            <KeyRound className="size-4" />
            비밀번호 변경
          </DropdownMenuItem>
          <DropdownMenuItem onClick={signOut}>
            <LogOut className="size-4" />
            로그아웃
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      {/* 바꾸고 나면 서버가 세션을 전부 끊는다 — 클라이언트 상태도 맞춘다. */}
      <ChangePasswordDialog
        open={changingPassword}
        onClose={() => setChangingPassword(false)}
        onChanged={async () => {
          setChangingPassword(false)
          await logout()
        }}
      />
    </header>
  )
}
