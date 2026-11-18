/**
 * 허용 주소 — AI(MCP)의 탐색 · 시험이 닿아도 되는 곳을 **재시작 없이** 더하고 뺀다.
 *
 * 원천 주소와 대상 SP 주소를 따로 둔다 — 대상 주소는 원천 주소로 못 쓴다(원천의 비밀이 대상 쪽으로
 * 나가지 않게). 서버 설정(`RELAY_SOURCE_HOSTS` · `RELAY_TARGET_HOSTS`)과 사용 중인 정의의 주소는
 * 보이기만 하고, 화면에서 더한 것만 고친다. 고치는 것은 사람만(서버가 토큰을 거절한다).
 */

import { useEffect, useState } from 'react'

import { relayApi } from '@/modules/relay/api'
import type { AllowedList } from '@/modules/relay/api'
import { ApiError } from '@/shared/api/client'
import { useAuth } from '@/shared/auth/AuthContext'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { PageHeader } from '@/shared/components/PageHeader'
import { Button } from '@/shared/components/ui/button'
import { Label } from '@/shared/components/ui/label'
import { Textarea } from '@/shared/components/ui/textarea'
import { useResource } from '@/shared/hooks/useResource'
import { shownDateTime } from '@/shared/lib/datetime'

const KINDS = [
  {
    kind: 'source' as const,
    title: '원천',
    hint: 'PLM · MES 의 API · 공유 폴더 · SFTP · Kafka 브로커 · 토큰 주소. 대상 SP 로도 쓸 수 있다(예전부터의 뜻).',
  },
  {
    kind: 'target' as const,
    title: '대상 SP',
    hint: '받는 Standard Platform 인스턴스 — 정의 초안의 시험 · 한 번에 초안이 그 정의를 읽고 대조한다. 원천 주소로는 못 쓴다.',
  },
]

/** 줄마다 하나(쉼표도) — 빈 것 · 겹친 것은 서버가 걸러 준다. */
export function hostsOf(text: string): string[] {
  return text
    .split(/[\n,]/)
    .map((one) => one.trim())
    .filter(Boolean)
}

function Fixed({ label, hosts }: { label: string; hosts: string[] }) {
  return (
    <div className="space-y-1">
      <p className="text-muted-foreground text-xs">{label}</p>
      {hosts.length ? (
        <ul className="flex flex-wrap gap-1">
          {hosts.map((one) => (
            <li key={one} className="bg-muted rounded px-1.5 py-0.5 font-mono text-xs">
              {one}
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-muted-foreground text-xs">없음</p>
      )}
    </div>
  )
}

export default function AllowedHostsPage() {
  const { user } = useAuth()
  const admin = Boolean(user?.is_system_admin)
  const listed = useResource(() => relayApi.allowedHosts(), [])
  const [drafts, setDrafts] = useState({ source: '', target: '' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ApiError | Error | null>(null)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    if (listed.data) {
      setDrafts({
        source: listed.data.source.screen.join('\n'),
        target: listed.data.target.screen.join('\n'),
      })
    }
  }, [listed.data])

  async function save() {
    setBusy(true)
    setError(null)
    setSaved(false)
    try {
      await relayApi.putAllowedHosts(hostsOf(drafts.source), hostsOf(drafts.target))
      setSaved(true)
      listed.reload()
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-4">
      <PageHeader
        title="허용 주소"
        description="AI 의 탐색 · 시험이 닿아도 되는 주소입니다. 화면에서 더한 것은 재시작 없이 바로 쓰입니다. 사용 중인 정의의 주소는 저절로 허용됩니다."
      />
      <ErrorNotice error={listed.error} />
      {listed.loading && !listed.data && <p className="text-muted-foreground text-sm">읽는 중…</p>}
      {listed.data && (
        <div className="grid gap-4 lg:grid-cols-2">
          {KINDS.map(({ kind, title, hint }) => {
            const one: AllowedList = listed.data![kind]
            return (
              <section key={kind} className="space-y-3 rounded-md border p-4" data-testid={`allowed-${kind}`}>
                <div>
                  <h2 className="text-base font-semibold">{title}</h2>
                  <p className="text-muted-foreground text-xs">{hint}</p>
                </div>
                <Fixed label="서버 설정(.env — 화면에서 못 바꿈)" hosts={one.settings} />
                <Fixed label="사용 중인 정의가 쓰는 주소(저절로)" hosts={one.definitions} />
                <div className="space-y-1">
                  <Label htmlFor={`hosts-${kind}`}>화면에서 더한 주소 — 줄마다 host 또는 host:port</Label>
                  <Textarea
                    id={`hosts-${kind}`}
                    rows={5}
                    className="font-mono text-sm"
                    value={drafts[kind]}
                    disabled={!admin}
                    placeholder={kind === 'target' ? 'jvhub.corp.local:8040' : 'mes-api.corp.local'}
                    onChange={(event) => {
                      const value = event.target.value
                      // 앞 상태를 바탕으로 — 닫힌 `drafts` 를 쓰면 막 읽어 채운 다른 칸을 지운다.
                      setDrafts((before) => ({ ...before, [kind]: value }))
                    }}
                  />
                </div>
              </section>
            )
          })}
        </div>
      )}
      {listed.data && admin && (
        <div className="flex items-center gap-3">
          <Button onClick={save} disabled={busy}>
            {busy ? '저장하는 중…' : '저장'}
          </Button>
          {saved && <span className="text-sm">저장했습니다 — 지금부터 쓰입니다.</span>}
          {listed.data.updated_at && (
            <span className="text-muted-foreground text-xs">
              마지막 변경 {shownDateTime(listed.data.updated_at)} · {listed.data.updated_by}
            </span>
          )}
        </div>
      )}
      <ErrorNotice error={error} />
    </div>
  )
}
