/**
 * 미해결 작업함 — 실행이 미해결로 멈추면 그 미해결을 여기서 푼다. **시스템 관리자만**(원천 표기가
 * 그대로 나온다).
 *
 * - **참조**(참조 대조가 대상에서 못 찾은 원천 표기)는 대상의 키를 선택한다. 후보는 대상에 있는 것
 *   안에서만 — 식별자 · 이름 · 별칭과 견준 규칙의 후보, 대상이 「여럿과 맞음」 으로 준 후보, 그리고
 *   설정했으면 GLM 의 제안. **정하는 것은 사람이다** — 선택한 대응은 「레시피 초안으로 저장」 이 지금
 *   레시피의 relay 사전에 더해 레시피 판 초안이 되고, 시험 · 사용은 아래 「레시피 판」 에서 한다.
 * - **값 갈림**(같은 식별자인데 행마다 값이 다름)은 고를 키가 없다 — 레시피(모으는 칸)나 원천에서
 *   수정한다.
 * - **별칭 요청 목록**은 relay 사전 그대로를 CSV 로 — 대표 플랫폼에 별칭 등록을 요청한다. 등록돼
 *   대상이 그 표기를 풀면 목록이 「예」 로 말한다(relay 사전에서 삭제해도 된다).
 */

import { useState } from 'react'
import { Download, Loader2, Save, Sparkles } from 'lucide-react'

import { relayApi } from '@/modules/relay/api'
import type { AliasEntry, InboxReference, RecipeSaved, SuggestOut } from '@/modules/relay/api'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { Alert, AlertDescription, AlertTitle } from '@/shared/components/ui/alert'
import { Button } from '@/shared/components/ui/button'
import { Input } from '@/shared/components/ui/input'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/shared/components/ui/table'
import { useResource } from '@/shared/hooks/useResource'
import { shownDateTime } from '@/shared/lib/datetime'
import { cn } from '@/shared/lib/utils'

/** 참조 한 줄의 자리 — 같은 대상 타입의 같은 표기는 한 줄이다. */
const slotOf = (one: Pick<InboxReference, 'target' | 'text'>) => `${one.target} ${one.text}`

function Suggestion({ got }: { got: SuggestOut }) {
  return (
    <p className={cn('text-xs', got.key ? 'text-foreground' : 'text-muted-foreground')}>
      {got.key
        ? `GLM: ${got.key}${got.label ? ` ${got.label}` : ''} (확신 ${got.confidence.toFixed(2)}) — ${got.reason}`
        : got.note || 'GLM 이 고르지 않았습니다.'}
    </p>
  )
}

export function UnresolvedInbox({
  slug,
  onDrafted,
}: {
  slug: string
  /** 레시피 판 초안을 저장했다 — 레시피 판 목록을 새로 읽게. */
  onDrafted: () => void
}) {
  const inbox = useResource(() => relayApi.unresolved(slug), [slug])
  const [chosen, setChosen] = useState<Record<string, string>>({})
  const [asked, setAsked] = useState<Record<string, SuggestOut>>({})
  const [asking, setAsking] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState<RecipeSaved | null>(null)
  const [failed, setFailed] = useState<Error | null>(null)

  const data = inbox.data
  const references = data?.references ?? []
  const entries: AliasEntry[] = references
    .filter((one) => (chosen[slotOf(one)] ?? '').trim())
    .map((one) => ({ target: one.target, text: one.text, key: chosen[slotOf(one)].trim() }))

  async function suggest(one: InboxReference) {
    setAsking(slotOf(one))
    setFailed(null)
    try {
      const got = await relayApi.suggest(slug, one.target, one.text, one.columns[0] ?? '')
      setAsked((before) => ({ ...before, [slotOf(one)]: got }))
      if (got.key) setChosen((before) => ({ ...before, [slotOf(one)]: got.key ?? '' }))
    } catch (caught) {
      setFailed(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setAsking(null)
    }
  }

  async function save() {
    setSaving(true)
    setFailed(null)
    setSaved(null)
    try {
      setSaved(await relayApi.draftAliases(slug, entries))
      setChosen({})
      onDrafted()
    } catch (caught) {
      setFailed(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setSaving(false)
    }
  }

  async function download() {
    setFailed(null)
    try {
      await relayApi.aliasCsv(slug)
    } catch (caught) {
      setFailed(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    }
  }

  const csvButton = (
    <Button size="sm" variant="outline" onClick={() => void download()}>
      <Download />
      별칭 요청 목록 다운로드
    </Button>
  )

  if (!data) {
    return (
      <div className="space-y-2">
        <ErrorNotice error={inbox.error} />
        {inbox.loading && (
          <p className="text-muted-foreground text-sm">읽는 중… 대상에 후보를 묻느라 몇 초 걸릴 수 있습니다.</p>
        )}
      </div>
    )
  }

  if (!data.run) {
    return (
      <div className="space-y-2">
        <p className="text-muted-foreground text-sm">{data.note}</p>
        <div className="flex flex-wrap items-center gap-3">
          {csvButton}
          <span className="text-muted-foreground text-xs">
            지금 레시피의 relay 사전을 대표 플랫폼에 별칭 등록을 요청할 목록으로 다운로드합니다.
          </span>
        </div>
        <ErrorNotice error={failed} />
      </div>
    )
  }

  return (
    <div className="space-y-3">
      <p className="text-sm">
        {shownDateTime(data.at)} 실행이 미해결로 멈췄습니다 — 참조 {references.length} · 값 갈림{' '}
        {data.splits.length}. 선택한 대응은 <b>{data.recipe_label || '지금 레시피'}</b>에 더해 레시피 판
        초안이 됩니다.
      </p>
      {data.problems.length > 0 && (
        <Alert className="border-amber-500/40 bg-amber-500/5">
          <AlertTitle>후보를 내지 못한 것이 있습니다</AlertTitle>
          <AlertDescription>
            <ul className="list-disc pl-4">
              {data.problems.map((one) => (
                <li key={one}>{one}</li>
              ))}
            </ul>
          </AlertDescription>
        </Alert>
      )}

      {references.length > 0 && (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>대상 타입</TableHead>
              <TableHead>원천 표기</TableHead>
              <TableHead>칸 · 행</TableHead>
              <TableHead>후보</TableHead>
              <TableHead className="w-56">선택한 키</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {references.map((one) => {
              const slot = slotOf(one)
              const picked = chosen[slot] ?? ''
              return (
                <TableRow key={slot} data-testid="inbox-reference">
                  <TableCell className="font-mono text-xs">{one.target}</TableCell>
                  <TableCell>
                    <span className="font-mono">{one.text}</span>
                    {one.in_dictionary && (
                      <p className="text-destructive text-xs">
                        relay 사전에 {one.in_dictionary} 로 있는데도 미해결입니다 — 그 키가 대상에 없거나 실행이 그
                        판보다 먼저였습니다.
                      </p>
                    )}
                  </TableCell>
                  <TableCell className="text-xs">
                    {one.places.join(' · ')}
                    <p className="text-muted-foreground">
                      {one.columns.join(' · ') || '—'} · {one.rows}행{one.more ? ' 이상' : ''}
                    </p>
                  </TableCell>
                  <TableCell className="max-w-md">
                    {one.candidates.length === 0 ? (
                      <span className="text-muted-foreground text-xs">
                        대상에서 비슷한 것을 못 찾았습니다 — 대상에 없는 것이면 원천에서 수정하거나 대표 플랫폼에
                        먼저 만들어야 합니다.
                      </span>
                    ) : (
                      <div className="flex flex-wrap gap-1">
                        {one.candidates.map((candidate) => (
                          <Button
                            key={candidate.key}
                            size="sm"
                            variant={picked === candidate.key ? 'default' : 'outline'}
                            className="h-auto px-2 py-0.5 text-xs"
                            title={`${candidate.why} · 점수 ${candidate.score.toFixed(2)}`}
                            onClick={() => setChosen((before) => ({ ...before, [slot]: candidate.key }))}
                          >
                            <span className="font-mono">{candidate.key}</span>
                            {candidate.label && <span className="opacity-70">{candidate.label}</span>}
                          </Button>
                        ))}
                      </div>
                    )}
                    {asked[slot] && <Suggestion got={asked[slot]} />}
                  </TableCell>
                  <TableCell>
                    <div className="flex items-center gap-1">
                      <Input
                        aria-label={`${one.text} 의 대상 키`}
                        value={picked}
                        placeholder="키 선택 · 입력"
                        className="h-8 font-mono text-xs"
                        onChange={(event) => setChosen((before) => ({ ...before, [slot]: event.target.value }))}
                      />
                      {data.llm && (
                        <Button
                          size="sm"
                          variant="ghost"
                          title="GLM 이 후보 안에서 하나를 고릅니다(제안일 뿐입니다)"
                          aria-label={`${one.text} GLM 제안`}
                          disabled={asking !== null}
                          onClick={() => void suggest(one)}
                        >
                          {asking === slot ? <Loader2 className="animate-spin" /> : <Sparkles />}
                        </Button>
                      )}
                    </div>
                  </TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      )}

      <div className="flex flex-wrap items-center gap-3">
        <Button size="sm" onClick={() => void save()} disabled={saving || entries.length === 0}>
          {saving ? <Loader2 className="animate-spin" /> : <Save />}
          레시피 초안으로 저장({entries.length}개)
        </Button>
        {csvButton}
        <span className="text-muted-foreground text-xs">
          대상에 있는 키만 저장됩니다. 저장한 초안은 아래 「레시피 판」 에서 시험하고 사용해야 실행에 쓰입니다.
        </span>
      </div>
      <ErrorNotice error={failed} />
      {saved && (
        <Alert>
          <AlertTitle>레시피 판 {saved.version} 초안을 저장했습니다</AlertTitle>
          <AlertDescription>
            아래 「레시피 판」 에서 시험하고 「사용」 을 클릭하면 다음 실행부터 그 대응으로 풉니다.
            {saved.problems.length > 0 && (
              <ul className="list-disc pl-4">
                {saved.problems.map((one) => (
                  <li key={one}>{one}</li>
                ))}
              </ul>
            )}
          </AlertDescription>
        </Alert>
      )}

      {data.splits.length > 0 && (
        <div className="space-y-1">
          <h3 className="text-sm font-medium">값 갈림 {data.splits.length}건 — 레시피(모으는 칸)나 원천에서 수정</h3>
          <ul className="divide-y rounded-md border text-sm">
            {data.splits.map((one) => (
              <li key={one.what} className="space-y-0.5 px-3 py-2">
                <p className="font-mono text-xs">{one.what}</p>
                <p className="text-muted-foreground text-xs">{one.question}</p>
                <p className="text-xs">
                  값: {one.options.map((value) => `「${value}」`).join(' · ')} · {one.rows}행
                </p>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
