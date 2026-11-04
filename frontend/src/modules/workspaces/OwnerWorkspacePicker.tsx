/**
 * 어느 부서 것으로 할까 — **내가 관리자인 부서 중에서** 고른다.
 *
 * 객체 생성 · 일괄 입력 · 홈 게시가 늘 대표 소속으로 보내던 때, B 의 관리자인데 대표 소속 A
 * 에서는 멤버인 사람은 단추를 누르고 403 을 봤다(서버의 `require_manager`). 시스템 관리자는
 * 보고 있던 부서가 아니라 자기 대표 소속(hq)에 올렸다(2026-10-08). 그래서 고를 수 있게 하되
 * **고를 수 있는 것만** 내놓는다 — 고를 수 있다고 보여 주고 나서 거절하지 않는다.
 *
 * 시스템 관리자는 어느 부서에든 만든다 — 소속만 내놓으면 남의 부서에 만들 길이 없다.
 *
 * **기록은 구성원이면 넣는다**(ADR 0036) — `usage` 가 `log` 면 관리하는 곳이 아니라 소속 전부다.
 */

import { useEffect, useState } from 'react'

import { workspaceApi } from '@/modules/workspaces/api'
import type { WorkspaceOption } from '@/modules/workspaces/api'
import { useAuth } from '@/shared/auth/AuthContext'
import { isSystemAdmin, ownableMemberships } from '@/shared/auth/roles'
import { SearchablePicker } from '@/shared/components/SearchablePicker'
import type { PickerOption } from '@/shared/components/SearchablePicker'

interface Props {
  value: string | null
  onChange: (slug: string) => void
  id?: string
  className?: string
  /** 만드는 것의 용도 — `log`(기록)면 소속 전부, 아니면 관리하는 소속. */
  usage?: string | null
}

export function OwnerWorkspacePicker({ value, onChange, id, className, usage }: Props) {
  const { user } = useAuth()
  const admin = isSystemAdmin(user)
  // 전체 부서는 시스템 관리자만 받는다 — 나머지는 내 소속(로그인 때 이미 받은 것)으로 끝난다.
  const [everyone, setEveryone] = useState<WorkspaceOption[] | null>(null)
  useEffect(() => {
    if (!admin) return
    let cancelled = false
    workspaceApi
      .options()
      .then((found) => {
        if (!cancelled) setEveryone(found)
      })
      .catch(() => {
        // 못 받으면 내 소속으로라도 고르게 둔다 — 고르개가 비면 만들 길이 없다.
        if (!cancelled) setEveryone([])
      })
    return () => {
      cancelled = true
    }
  }, [admin])
  const source: { slug: string; name: string; path: string }[] = admin
    ? everyone?.length
      ? everyone
      : (user?.memberships ?? [])
    : ownableMemberships(user, usage)
  const options: PickerOption[] = source.map((one) => ({
    value: one.slug,
    label: one.name,
    hint: one.path,
    keywords: one.slug,
  }))
  // 고른 값이 아직 안 받아진 목록 밖이면(시스템 관리자의 첫 순간) 이름 대신 주소라도 보인다.
  const pinned =
    value && !options.some((one) => one.value === value) ? { value, label: value } : null

  return (
    <SearchablePicker
      id={id}
      className={className}
      options={options}
      value={value}
      onChange={onChange}
      pinned={pinned}
      loading={admin && everyone === null}
      ariaLabel="소유 부서"
      placeholder="부서를 선택하세요"
      searchPlaceholder="부서 이름이나 주소로 검색"
      emptyText={usage === 'log' ? '소속 부서가 없습니다' : '관리하는 부서가 없습니다'}
    />
  )
}
