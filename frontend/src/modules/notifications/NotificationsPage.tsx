/**
 * 알림 — **나에게 온 것.**
 *
 * 공지가 모두에게 가는 방송이라면 알림은 한 사람에게 가는 편지다. 메일이 없는
 * 환경이라 이것이 유일한 전달 경로이고, 그래서 읽음 상태를 서버가 든다.
 */

import { useState } from 'react'
import { Link } from 'react-router-dom'

import { api } from '@/shared/api/client'
import { notifyUnreadChanged } from '@/shared/layout/NotificationBell'
import { EmptyState } from '@/shared/components/EmptyState'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { PageHeader } from '@/shared/components/PageHeader'
import { Button } from '@/shared/components/ui/button'
import { useResource } from '@/shared/hooks/useResource'
import { shownDateTime } from '@/shared/lib/datetime'

interface Notification {
  id: string
  kind: string
  title: string
  body: string | null
  /** 눌렀을 때 갈 곳. **없으면 알림은 읽고 끝나는 글이 된다.** */
  link: string | null
  read_at: string | null
  created_at: string
}

export default function NotificationsPage() {
  const list = useResource(() => api.get<Notification[]>('/notifications'), [])
  const [error, setError] = useState<Error | null>(null)
  const [busy, setBusy] = useState(false)

  /** 읽음 처리 — **실패하면 말하고**, 되면 배지가 곧바로 다시 센다. 삼키던 때는 눌러도 아무
   *  일이 없는 것처럼 보였다(2026-10-08). */
  async function mark(run: () => Promise<unknown>) {
    setError(null)
    setBusy(true)
    try {
      await run()
      notifyUnreadChanged()
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBusy(false)
      list.reload()
    }
  }

  /** 한 건 읽음 — 「보러 가기」 로 떠나도 읽은 것이 된다. 떠나는 길이라 기다리지 않는다. */
  function readOne(one: Notification) {
    if (one.read_at) return
    api
      .post(`/notifications/${one.id}/read`)
      .then(() => notifyUnreadChanged())
      .catch(() => {
        // 떠난 뒤라 보일 자리가 없다 — 안 읽은 채로 남을 뿐이고, 목록에서 다시 누를 수 있다.
      })
  }

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <PageHeader
        title="알림"
        description="가입 승인, 교정 만료처럼 나에게 온 일들입니다."
        actions={
          <Button
            variant="outline"
            disabled={busy}
            onClick={() => void mark(() => api.post('/notifications/read-all'))}
          >
            모두 읽음
          </Button>
        }
      />

      <ErrorNotice error={error ?? list.error} />

      {list.data && list.data.length === 0 ? (
        <EmptyState title="알림이 없습니다" />
      ) : (
        <ul className="space-y-2">
          {(list.data ?? []).map((one) => (
            <li
              key={one.id}
              className={one.read_at ? 'rounded-md border p-3 opacity-60' : 'rounded-md border p-3'}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-sm font-medium">{one.title}</p>
                  {one.body && (
                    <p className="text-muted-foreground mt-1 text-sm whitespace-pre-line">
                      {one.body}
                    </p>
                  )}
                  {one.link && (
                    <Link
                      to={one.link}
                      className="mt-1 inline-block text-xs underline"
                      onClick={() => readOne(one)}
                    >
                      보러 가기
                    </Link>
                  )}
                </div>
                <div className="flex shrink-0 flex-col items-end gap-1">
                  <span className="text-muted-foreground text-xs">
                    {shownDateTime(one.created_at)}
                  </span>
                  {/* 하나씩도 읽음으로 — 「모두」 만 있으면 남겨 둘 것까지 함께 읽힌다. */}
                  {!one.read_at && (
                    <Button
                      variant="ghost"
                      size="xs"
                      disabled={busy}
                      onClick={() => void mark(() => api.post(`/notifications/${one.id}/read`))}
                    >
                      읽음
                    </Button>
                  )}
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
