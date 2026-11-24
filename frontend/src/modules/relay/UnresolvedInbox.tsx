/**
 * 미해결 작업함 — 실행이 미해결로 멈추면 그 미해결을 여기서 푼다. **시스템 관리자만**(원천 표기가
 * 그대로 나온다).
 *
 * - **참조**(참조 대조가 대상에서 못 찾은 원천 표기)는 대상의 키를 선택한다. 후보는 대상에 있는 것
 *   안에서만 — 식별자 · 이름 · 별칭과 견준 규칙의 후보, 대상이 「여럿과 맞음」 으로 준 후보, 그리고
 *   설정했으면 GLM 의 제안. **정하는 것은 사람이다** — 선택한 대응은 「레시피 초안으로 저장」 이 지금
 *   레시피의 relay 사전에 더해 레시피 판 초안이 되고, 시험 · 사용은 아래 「레시피 판」 에서 한다.
 * - **값 갈림**(같은 식별자인데 행마다 값이 다름)은 고를 키가 없다 — 레시피(모으는 칸)나 원천에서
 *   수정한다. 객체마다 같은 칸이 갈리면(**값이 갈리는 칸**) 그 칸을 모으는 칸(「한 글로」 join ·
 *   「목록으로」 collect)으로 바꾸는 것을 대응과 함께 같은 초안에 담는다.
 * - **별칭 요청 목록**은 relay 사전 그대로를 CSV 로 — 대표 플랫폼에 별칭 등록을 요청한다. 등록돼
 *   대상이 그 표기를 풀면 목록이 「예」 로 말한다 — 그 줄은 relay 사전에서 걷는다. 정본은 SP 의 별칭이고 relay
 *   사전은 그때까지의 임시 지름길이다(SP ADR 0063).
 */

import { useState } from 'react'
import { Download, Loader2, Save, Sparkles } from 'lucide-react'

import { relayApi } from '@/modules/relay/api'
import type {
  AggregateEntry,
  AliasEntry,
  InboxReference,
  InboxSplitField,
  RecipeSaved,
  SuggestOut,
} from '@/modules/relay/api'
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
/** 값이 갈리는 칸의 자리 — 타입.칸. */
const fieldOf = (one: Pick<InboxSplitField, 'type_slug' | 'field'>) => `${one.type_slug}.${one.field}`

type Mode = 'join' | 'collect'
const MODES: { mode: Mode; label: string; title: string }[] = [
  { mode: 'join', label: '문자열 결합', title: '값을 구분자로 결합해 하나의 문자열로 전송합니다.' },
  { mode: 'collect', label: '목록', title: '값을 목록으로 전송합니다. 대상 속성이 목록이어야 합니다.' },
]

function Suggestion({ got }: { got: SuggestOut }) {
  return (
    <p className={cn('text-xs', got.key ? 'text-foreground' : 'text-muted-foreground')}>
      {got.key
        ? `GLM 제안: ${got.key}${got.label ? ` ${got.label}` : ''} (신뢰도 ${got.confidence.toFixed(2)}). ${got.reason}`
        : got.note || 'GLM 제안 없음'}
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
  const [folded, setFolded] = useState<Record<string, { mode: Mode; separator: string }>>({})
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
  const splitFields = data?.split_fields ?? []
  const aggregates: AggregateEntry[] = splitFields
    .filter((one) => folded[fieldOf(one)])
    // 목록으로 모으면 구분자는 쓰지 않는다 — 서버의 기본값을 그대로 싣는다.
    .map((one) => ({ type_slug: one.type_slug, field: one.field, ...folded[fieldOf(one)] }))
  const picks = entries.length + aggregates.length

  function fold(one: InboxSplitField, mode: Mode) {
    const slot = fieldOf(one)
    setFolded((before) => {
      const { [slot]: was, ...rest } = before
      return was?.mode === mode ? rest : { ...rest, [slot]: { mode, separator: was?.separator ?? '; ' } }
    })
  }

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
      setSaved(await relayApi.draftAliases(slug, entries, aggregates))
      setChosen({})
      setFolded({})
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
          <p className="text-muted-foreground text-sm">불러오는 중… 대상에 후보를 조회하므로 몇 초 걸릴 수 있습니다.</p>
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
            현재 레시피의 relay 사전을 대표 플랫폼에 별칭 등록을 요청하는 목록(CSV)으로 다운로드합니다.
          </span>
        </div>
        <ErrorNotice error={failed} />
      </div>
    )
  }

  return (
    <div className="space-y-3">
      <p className="text-sm">
        {shownDateTime(data.at)} 실행이 미해결로 중단되었습니다(참조 {references.length}건, 값 충돌{' '}
        {data.splits.length}건). 선택한 키는 <b>{data.recipe_label || '현재 레시피'}</b>에 추가되어 레시피
        버전 초안으로 저장됩니다.
      </p>
      {data.problems.length > 0 && (
        <Alert className="border-amber-500/40 bg-amber-500/5">
          <AlertTitle>후보를 조회할 수 없는 항목이 있습니다</AlertTitle>
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
              <TableHead>필드 · 행</TableHead>
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
                        relay 사전에 등록된 키({one.in_dictionary})로도 해결되지 않았습니다. 해당 키가 대상에
                        없거나, 실행 시점이 이 레시피 버전보다 이전입니다.
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
                        대상에서 유사한 후보를 찾을 수 없습니다. 대상에 없는 값이면 원천에서 수정하거나 대표
                        플랫폼에 먼저 등록하세요.
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
                        aria-label={`${one.text} 대상 키`}
                        value={picked}
                        placeholder="키 선택 · 입력"
                        className="h-8 font-mono text-xs"
                        onChange={(event) => setChosen((before) => ({ ...before, [slot]: event.target.value }))}
                      />
                      {data.llm && (
                        <Button
                          size="sm"
                          variant="ghost"
                          title="GLM이 후보 중 하나를 제안합니다. 결정은 사람이 합니다."
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

      {splitFields.length > 0 && (
        <div className="space-y-1">
          <h3 className="text-sm font-medium">값 충돌 속성 {splitFields.length}개 — 집계 속성으로 변경 가능</h3>
          <p className="text-muted-foreground text-xs">
            여러 객체에서 같은 속성의 값이 충돌하면, 해당 속성은 행 단위 속성이거나 여러 값을 갖는 속성입니다.
            집계 속성으로 변경하지 않으면 원천에서 수정해야 합니다.
          </p>
          <ul className="divide-y rounded-md border text-sm">
            {splitFields.map((one) => {
              const slot = fieldOf(one)
              const picked = folded[slot]
              return (
                <li key={slot} data-testid="inbox-split-field" className="flex flex-wrap items-center gap-2 px-3 py-2">
                  <span className="font-mono text-xs">{slot}</span>
                  <span className="text-muted-foreground text-xs">객체 {one.entities}개에서 충돌</span>
                  <div className="ml-auto flex flex-wrap items-center gap-1">
                    {MODES.map((choice) => (
                      <Button
                        key={choice.mode}
                        size="sm"
                        variant={picked?.mode === choice.mode ? 'default' : 'outline'}
                        className="h-7 text-xs"
                        title={choice.title}
                        aria-pressed={picked?.mode === choice.mode}
                        aria-label={`${slot} ${choice.label}`}
                        onClick={() => fold(one, choice.mode)}
                      >
                        {choice.label}
                      </Button>
                    ))}
                    {picked?.mode === 'join' && (
                      <Input
                        aria-label={`${slot} 구분자`}
                        value={picked.separator}
                        maxLength={10}
                        className="h-7 w-16 font-mono text-xs"
                        onChange={(event) =>
                          setFolded((before) => ({ ...before, [slot]: { mode: 'join', separator: event.target.value } }))
                        }
                      />
                    )}
                  </div>
                </li>
              )
            })}
          </ul>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-3">
        <Button size="sm" onClick={() => void save()} disabled={saving || picks === 0}>
          {saving ? <Loader2 className="animate-spin" /> : <Save />}
          레시피 초안 저장({picks}개)
        </Button>
        {csvButton}
        <span className="text-muted-foreground text-xs">
          대상에 있는 키만 저장됩니다. 저장한 초안은 아래 [레시피 버전]에서 테스트하고 사용해야 실행에
          반영됩니다.
        </span>
      </div>
      <ErrorNotice error={failed} />
      {saved && (
        <Alert>
          <AlertTitle>레시피 버전 {saved.version} 초안을 저장했습니다</AlertTitle>
          <AlertDescription>
            아래 [레시피 버전]에서 테스트하고 [사용]을 클릭하면 다음 실행부터 선택한 키로 처리합니다.
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
          <h3 className="text-sm font-medium">값 충돌 {data.splits.length}건 — 레시피(집계 속성) 또는 원천에서 수정</h3>
          <ul className="divide-y rounded-md border text-sm">
            {data.splits.map((one) => (
              <li key={one.what} className="space-y-0.5 px-3 py-2">
                <p className="font-mono text-xs">{one.what}</p>
                <p className="text-muted-foreground text-xs">{one.question}</p>
                <p className="text-xs">
                  값: {one.options.map((value) => `'${value}'`).join(', ')} · {one.rows}행
                </p>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
