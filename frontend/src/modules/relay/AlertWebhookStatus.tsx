/**
 * 알림 웹훅 — 설정 여부 · 호스트 · 마지막 전송 결과와 [시험 전송]. **시스템 관리자만.**
 *
 * 주소는 서버 `.env`(`RELAY_ALERT_WEBHOOK_URL`)에만 둔다 — 수신 웹훅 주소에는 대개 토큰이 들어 있다. 그래서
 * 화면은 호스트만 표시한다. 주소를 틀리게 넣어도 서버 로그에만 남으면 아무도 모르므로, 마지막 전송이 실패했으면
 * 여기와 홈의 '확인 필요'에 표시한다(2026-11-25).
 */

import { useState } from 'react'
import { Send } from 'lucide-react'

import { relayApi } from '@/modules/relay/api'
import type { AlertWebhookTestOut } from '@/modules/relay/api'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { Button } from '@/shared/components/ui/button'
import { useResource } from '@/shared/hooks/useResource'
import { shownDateTime } from '@/shared/lib/datetime'

export function AlertWebhookStatus() {
  const status = useResource(() => relayApi.alertWebhook(), [])
  const [tested, setTested] = useState<AlertWebhookTestOut | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | null>(null)
  const data = status.data

  async function test() {
    setBusy(true)
    setError(null)
    try {
      setTested(await relayApi.alertWebhookTest())
      status.reload()
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBusy(false)
    }
  }

  if (!data) return <ErrorNotice error={status.error} />
  return (
    <section className="text-muted-foreground flex flex-wrap items-center gap-x-3 gap-y-1 border-t pt-3 text-xs">
      <span className="text-foreground font-medium">알림 웹훅</span>
      {data.configured ? (
        <>
          <span>
            사용 중 · <span className="font-mono">{data.host}</span>
          </span>
          {data.last_at && (
            <span className={data.last_ok ? undefined : 'text-destructive'}>
              마지막 전송 {shownDateTime(data.last_at)} {data.last_ok ? '성공' : `실패(${data.last_error})`}
            </span>
          )}
          <Button size="sm" variant="outline" onClick={test} disabled={busy}>
            <Send />
            시험 전송
          </Button>
        </>
      ) : (
        <span>미사용 · 서버 .env의 RELAY_ALERT_WEBHOOK_URL에 사내 메신저의 수신 웹훅 주소를 입력하면 사용됩니다.</span>
      )}
      {tested && <span className={tested.ok ? 'text-emerald-600 dark:text-emerald-400' : 'text-destructive'}>{tested.message}</span>}
      <ErrorNotice error={error} />
    </section>
  )
}
