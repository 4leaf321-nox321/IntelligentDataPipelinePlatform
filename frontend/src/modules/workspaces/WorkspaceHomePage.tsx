/**
 * 부서 홈 — **남은 일이 먼저 온다.**
 *
 * 관리 화면에 들어가야만 보이는 목록은 아무도 안 본다. 멎은 파이프라인 · 아무도 안 누른 계획 ·
 * 실패한 작업이 며칠씩 방치된다.
 *
 * 남은 일은 **화면을 고쳐서 더하지 않는다** — 서버의 레지스트리(`shared/extensions.py`)에
 * 등록하면 저절로 뜬다. 틀(StandardPlatform)의 위젯(저장된 뷰 · 지표 · 눈여겨볼 것 · 지켜보는
 * 것)은 온톨로지와 함께 걷어냈다(ADR 0001). 연결 지도가 생기면 그 요약이 여기 선다.
 */

import { Link, useParams } from 'react-router-dom'

import { api } from '@/shared/api/client'
import type { MaintenanceItem } from '@/shared/api/types'
import { useAuth } from '@/shared/auth/AuthContext'
import { APP_NAME } from '@/shared/branding'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { PageHeader } from '@/shared/components/PageHeader'
import { useResource } from '@/shared/hooks/useResource'

/** 부서마다 새로 세운다 — 다시 쓰면 B 의 제목 아래 A 의 것이 응답이 올 때까지 남는다. */
export default function WorkspaceHomePage() {
  const { slug } = useParams<{ slug?: string }>()
  return <WorkspaceHome key={slug ?? ''} />
}

function WorkspaceHome() {
  const { slug } = useParams<{ slug?: string }>()
  const { user } = useAuth()
  const maintenance = useResource(() => api.get<MaintenanceItem[]>('/server/maintenance'), [])

  const workspace = user?.memberships.find((one) => one.slug === slug)

  return (
    <div className="space-y-8">
      <PageHeader
        title={workspace?.name ?? APP_NAME}
        description={workspace?.path ?? '소속된 부서가 없습니다. 관리자에게 배정을 요청하세요.'}
      />

      <ErrorNotice error={maintenance.error} />

      <section className="space-y-3">
        <h2 className="text-base font-semibold">남은 일</h2>
        {/* **0 건인 항목은 서버가 안 내보낸다.** 다 0 인 목록을 매일 보면 사람은
            그 자리를 아예 안 읽게 되고, 그때 진짜 하나가 떠도 눈에 안 들어온다. */}
        {maintenance.data && maintenance.data.length === 0 ? (
          <p className="text-muted-foreground text-sm">지금 처리할 일이 없습니다.</p>
        ) : (
          <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {(maintenance.data ?? []).map((one) => (
              <li
                key={one.key}
                className={
                  one.severity === 'warning'
                    ? 'rounded-md border border-amber-500/40 bg-amber-500/5 p-4'
                    : 'rounded-md border p-4'
                }
              >
                <p className="text-2xl font-semibold">{one.count}</p>
                <p className="text-sm">{one.label}</p>
                {one.link && (
                  <Link
                    to={one.link}
                    className="text-muted-foreground mt-2 block text-xs underline"
                  >
                    보러 가기
                  </Link>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}
