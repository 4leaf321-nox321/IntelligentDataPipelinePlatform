/**
 * 플랫폼 자기소개(ADR 0019) — **같은 도구를 가진 플랫폼 여럿 가운데 어디에 물을지** 고르는 글.
 *
 * 같은 틀로 띄운 플랫폼 여럿이 한 에이전트(MCP)에 붙으면 도구 이름 · 설명이 전부 같다. 에이전트는
 * 접속 때 안내문 첫머리에 실리는 이 소개로 어느 플랫폼에 물을지 가른다. 두 겹이다:
 *
 *   사람이 쓴 소개    무엇을 하러 오는 곳인가 · 정본은 어디인가 — 쓴 날에 멈춘다
 *   지금 담긴 것      조회할 때마다 센다(기록 · 축과 건수, 들어오는 곳 …) — 늘 지금이다
 *
 * 사람이 쓴 뒤 타입 · 데이터 소스가 생기거나 없어지면 「낡음」 으로 알린다(홈의 「남은 일」 도).
 * 확인하고 저장하면 사라진다 — 한 번 쓰고 끝나는 글이 아니라, 바뀔 때마다 다시 보는 글이다.
 */

import { useState } from 'react'

import { api } from '@/shared/api/client'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { Button } from '@/shared/components/ui/button'
import { Label } from '@/shared/components/ui/label'
import { Textarea } from '@/shared/components/ui/textarea'
import { useResource } from '@/shared/hooks/useResource'
import { shownDateTime } from '@/shared/lib/datetime'

export interface ProfileFact {
  key: string
  label: string
  lines: string[]
}

export interface PlatformProfileLive {
  slug: string
  name: string
  tagline: string
  summary: string
  notes: string
  updated_at: string | null
  /** 조회할 때마다 센 것 — 저장하지 않는다. */
  facts: ProfileFact[]
  /** 사람이 쓴 뒤 달라진 것. 소개가 아직 없으면 그 한 줄. */
  stale: string[]
}

const EMPTY = '자기소개를 아직 안 적었다'
const SUMMARY_MAX = 300
const NOTES_MAX = 2000

export function PlatformProfile() {
  const live = useResource(() => api.get<PlatformProfileLive>('/server/profile/live'), [])
  // 손대기 전에는 저장된 글을 보인다(null) — 다시 받아 와도 고치던 것을 덮지 않는다.
  const [summary, setSummary] = useState<string | null>(null)
  const [notes, setNotes] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState<Error | null>(null)

  const one = live.data
  const shownSummary = summary ?? one?.summary ?? ''
  const shownNotes = notes ?? one?.notes ?? ''
  const empty = one?.stale.length === 1 && one.stale[0] === EMPTY
  const changed = one && !empty ? one.stale : []
  const tooLong = shownSummary.length > SUMMARY_MAX || shownNotes.length > NOTES_MAX

  async function save() {
    setBusy(true)
    setFailed(null)
    try {
      await api.put('/server/profile', { summary: shownSummary, notes: shownNotes })
      setSummary(null)
      setNotes(null)
      live.reload()
    } catch (caught) {
      setFailed(caught as Error)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="space-y-3" id="platform-profile">
      <h2 className="text-base font-semibold">플랫폼 자기소개</h2>
      <p className="text-muted-foreground text-sm">
        같은 틀로 띄운 플랫폼 여럿이 한 에이전트(MCP)에 붙으면 도구가 모두 같아서, 에이전트는 이
        소개로 어느 플랫폼에 물을지 선택합니다. 사람이 쓴 소개와 <b>조회할 때마다 세는</b>「지금
        담긴 것」 이 함께 실립니다. 소개는 <b>로그인 없이도 표시되므로</b> 토큰 · 내부 주소 · 사람
        이름은 입력하지 않습니다. 에이전트의 <code>platform_profile</code> 로 초안을 받을 수도
        있습니다.
      </p>
      <ErrorNotice error={failed ?? live.error} />
      {empty && (
        <div className="rounded-md border border-amber-500/40 bg-amber-500/5 p-3 text-sm">
          아직 소개가 없습니다 — 에이전트는 아래 「지금 담긴 것」 만으로 이 플랫폼을 판단합니다.
        </div>
      )}
      {changed.length > 0 && (
        <div className="rounded-md border border-amber-500/40 bg-amber-500/5 p-3 text-sm">
          <p>소개를 저장한 뒤 바뀐 것이 있습니다. 확인하고 저장하면 이 표시가 사라집니다.</p>
          <ul className="mt-1 list-inside list-disc">
            {changed.map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
        </div>
      )}
      {one && (
        <div className="space-y-3 rounded-md border p-4">
          <div className="space-y-1.5">
            <Label htmlFor="profile-summary">
              담는 것 ({shownSummary.length}/{SUMMARY_MAX}자)
            </Label>
            <Textarea
              id="profile-summary"
              rows={2}
              value={shownSummary}
              placeholder="예: CAE 그룹의 해석 · 보고서 기록과 개발모델 — 보고서와 해석 결과를 묻는 곳"
              onChange={(event) => setSummary(event.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="profile-notes">
              다른 플랫폼과의 사이 ({shownNotes.length}/{NOTES_MAX}자)
            </Label>
            <Textarea
              id="profile-notes"
              rows={3}
              value={shownNotes}
              placeholder="예: 개발모델 · 과제의 정본은 허브입니다. 보고서의 정본은 ReportArchive 입니다."
              onChange={(event) => setNotes(event.target.value)}
            />
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <Button size="sm" disabled={busy || tooLong} onClick={save}>
              저장
            </Button>
            <span className="text-muted-foreground text-xs">
              {one.updated_at ? `마지막 저장 ${shownDateTime(one.updated_at)}` : '저장한 적 없음'}
              {' · '}변경 기록은 관리 › 감사 기록의 <code>server.profile</code>
            </span>
          </div>
          <div className="space-y-1">
            <p className="text-sm font-medium">지금 담긴 것 (조회할 때마다 셉니다)</p>
            {one.facts.length === 0 ? (
              <p className="text-muted-foreground text-sm">아직 담긴 것이 없습니다.</p>
            ) : (
              <ul className="text-muted-foreground space-y-0.5 text-sm">
                {one.facts.flatMap((fact) =>
                  fact.lines.map((line) => (
                    <li key={`${fact.key}:${line}`}>
                      <span className="text-foreground">{fact.label}</span>: {line}
                    </li>
                  )),
                )}
              </ul>
            )}
          </div>
        </div>
      )}
    </section>
  )
}
