/**
 * 정의 판 — **AI(MCP)가 파이프라인 정의(relay-pipeline/1)의 초안을 짓고 시험한다. 켜는 일은 사람이
 * 여기서 한다**(ADR 0001 §2). 레시피 판과 같은 길이다.
 *
 * 서버는 토큰으로 온 「켜기」 를 거절한다. 켜면 다음 실행부터 이 판이 같은 slug 의 정의 파일 대신
 * 돈다 — 그래서 켜기 전에 여기서 한 번 더 시험해 원천 · 대상에 닿는지 사람이 읽게 한다. 시험하지
 * 않았거나 막힌 판이면 확인 창이 그렇다고 먼저 말한다.
 *
 * 확인 창은 그 판의 본문도 읽는다 — 본문에 자동 적용(`auto_apply`)이 켜져 있으면 타이머의 실행이
 * 사람 없이 적용하게 된다. AI 가 지은 판일 수 있으므로 기준을 다 읽기 전에는 「켜기」 를 못 누른다.
 *
 * 초안은 버릴 수 있다(되돌릴 수 없다 · 번호는 다시 안 쓴다). 켜진 판이 없는 새 파이프라인은 초안을
 * 다 버리면 지도에서 빠지고 이 상세가 404 가 된다 — 그래서 그때는 다시 읽지 않고 목록으로 돌아간다.
 */

import { Fragment, useState } from 'react'
import { Loader2 } from 'lucide-react'
import { useNavigate } from 'react-router-dom'

import { autoApplyOf, countsLine, relayApi } from '@/modules/relay/api'
import type {
  DefinitionBody,
  DefinitionTrial,
  DefinitionVersion,
  RecipeVersion,
} from '@/modules/relay/api'
import {
  DefinitionTrialView,
  trialBlocks,
  trialCapped,
  trialVerdict,
  triedRecipe,
} from '@/modules/relay/DefinitionTrialView'
import { ConfirmDialog } from '@/shared/components/ConfirmDialog'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { StatusBadge, statusLabel } from '@/shared/components/StatusBadge'
import { Alert, AlertDescription, AlertTitle } from '@/shared/components/ui/alert'
import { Button } from '@/shared/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/shared/components/ui/dialog'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/shared/components/ui/table'
import { useResource } from '@/shared/hooks/useResource'
import type { Resource } from '@/shared/hooks/useResource'
import { shownDateTime } from '@/shared/lib/datetime'
import { cn } from '@/shared/lib/utils'

const SELECT = 'border-input bg-background h-8 rounded-md border px-2 text-sm'
const LIST = '/relay/pipelines'

/** 시험 한 번 — 어느 레시피 판으로 바꿔 봤나도 든다(null 이면 서버가 고른 기본). */
interface Tried {
  result: DefinitionTrial
  recipe: number | null
}

/** 창에 띄울 본문 — 판이든 정의 파일이든 같은 창. */
interface Shown {
  title: string
  note: string
  body: unknown
  problems?: string[]
}

/** 그 판 말고 버리지 않은 판이 남나 — 켜진 판도 정의 파일도 없는 파이프라인은 없으면 사라진다. */
const othersLeft = (list: DefinitionVersion[], version: number) =>
  list.some((one) => one.version !== version && one.status !== 'discarded')

/** 지금 도는 정의를 사람 말로. */
function currentText(label: string): string {
  if (label === '파일') return '정의 파일(사용 중인 판 없음)'
  if (label.startsWith('초안')) return `${label} — 사용 중인 판이 없어 실행되지 않습니다`
  return label
}

/** 확인 창의 첫 말 — 이 화면에서 한 시험이 무엇을 보였나. */
function TrialNote({ tried, recipes }: { tried: Tried | undefined; recipes: RecipeVersion[] }) {
  if (!tried) {
    return (
      <p className="text-amber-700 dark:text-amber-400">
        이 판을 아직 이 화면에서 시험하지 않았습니다 — 먼저 「시험」 으로 원천 · 대상에 닿는지 보세요.
      </p>
    )
  }
  const blocks = trialBlocks(tried.result)
  if (blocks.length > 0) {
    return (
      <p className="text-destructive">
        시험에서 막혔습니다({blocks.join(' · ')}) — 사용하면 다음 실행이 멈춥니다. 수정한 판을
        기다리는 편이 낫습니다.
      </p>
    )
  }
  const recipe = triedRecipe(tried.result)
  if (!recipe) {
    return (
      <p className="text-amber-700 dark:text-amber-400">
        시험에서 원천 · 대상은 닿았지만 레시피가 없습니다 — 사용해도 레시피 판을 사용하기 전에는
        실행되지 않습니다.
      </p>
    )
  }
  const line = countsLine(recipe.would_send)
  const picked = recipes.find((one) => one.version === tried.recipe)
  const rows = tried.result.source?.ok ? tried.result.source.rows : 0
  const capped = trialCapped(tried.result)
  return (
    <>
      {capped ? (
        <>
          <p>
            시험은 깨끗했습니다 — 원천 앞 {capped.toLocaleString()}줄을 {recipe.used}(으)로
            바꿨습니다.
          </p>
          <p className="text-amber-700 dark:text-amber-400">
            원천이 커서 앞부분만 받아 「사용하면 보낼 것」 은 세지 않았습니다 — 무엇을 보내는지는
            사용한 뒤 「지금 실행」 이 대상 SP 에 생성하는 계획에서 확인하세요.
          </p>
        </>
      ) : (
        <p>
          시험은 깨끗했습니다 — 원천 {rows.toLocaleString()}줄을 {recipe.used}(으)로 바꿨습니다
          {line ? `. 사용하면 보낼 것: ${line}` : ''}.
        </p>
      )}
      {picked && picked.status !== 'active' && (
        <p className="text-amber-700 dark:text-amber-400">
          시험에 사용한 레시피 판 {picked.version} 은 아직 사용 중이 아닙니다 — 실행은 사용 중인
          레시피를 따릅니다. 그 판으로 실행하려면 레시피 판에서도 사용하세요.
        </p>
      )}
    </>
  )
}

/** 켜려는 판의 본문을 아직 읽는 중인가 — 그동안은 자동 적용이 켜졌는지 모른다. */
const reading = (found: Resource<DefinitionBody | null>) =>
  found.loading || (found.stale && !found.error)

/** 확인 창의 자동 적용 — 켜져 있으면 기준을 늘어놓는다. 꺼졌으면 아무 말도 안 한다(기본이다). */
function AutoApplyNote({ found }: { found: Resource<DefinitionBody | null> }) {
  if (reading(found)) {
    return (
      <p className="text-muted-foreground">판의 본문을 읽는 중… 자동 적용을 사용하는지 확인합니다.</p>
    )
  }
  if (found.error) {
    return (
      <>
        <p className="text-amber-700 dark:text-amber-400">
          본문을 읽지 못해 자동 적용을 사용하는지 확인하지 못했습니다 — 사용하기 전에
          「본문 보기」 로 auto_apply 를 확인하세요.
        </p>
        <ErrorNotice error={found.error} />
      </>
    )
  }
  const body = found.data?.body
  const policy = autoApplyOf(body)
  if (!policy) return null
  return (
    <Alert className="border-amber-500/40 bg-amber-500/5">
      <AlertTitle className="text-amber-700 dark:text-amber-400">
        이 판은 자동 적용을 사용합니다 — 타이머의 실행이 기준을 넘은 계획을 사람 없이
        적용합니다.
      </AlertTitle>
      <AlertDescription className="space-y-1">
        <ul className="list-disc pl-4">
          <li>타이머의 실행에만 — 「지금 실행」 · AI(MCP)의 실행은 계획만 생성합니다.</li>
          <li>사람이 적용한 것이 {policy.after_manual}번 된 뒤부터</li>
          <li>
            대상의 미리 보기에 오류가 없고 생성 + 변경이 {policy.max_changes.toLocaleString()}건
            이하일 때
          </li>
          <li>
            {policy.allow_gone
              ? '삭제(사용 중지 · 관계 해제)가 있어도 적용'
              : '삭제(사용 중지 · 관계 해제)가 없을 때만'}
          </li>
        </ul>
        <p>못 넘으면 계획은 사람을 기다립니다 — 이력에 「자동 적용 안 함」 과 그 까닭이 남습니다.</p>
        <p className="font-mono text-xs">
          auto_apply: {JSON.stringify((body as { auto_apply?: unknown }).auto_apply)}
        </p>
      </AlertDescription>
    </Alert>
  )
}

export function DefinitionVersions({
  slug,
  definitionLabel,
  draft,
  recipes,
  onChanged,
}: {
  slug: string
  /** 지금 도는 정의 — 「파일」 · 「판 N」 · 「초안 N」. */
  definitionLabel: string
  /** 켜진 판도 정의 파일도 없는 새 파이프라인(`PipelineDetail.draft`) — 초안을 다 버리면 사라진다. */
  draft: boolean
  /** 시험에 쓸 레시피를 고를 것 — 레시피 판 목록(레시피 판 표와 같은 응답). */
  recipes: RecipeVersion[]
  /** 켜거나 버려 상세가 바뀌었다 — 상세를 다시 읽는다. */
  onChanged: () => void
}) {
  const navigate = useNavigate()
  const versions = useResource(() => relayApi.definitions(slug), [slug])
  const [trials, setTrials] = useState<Record<number, Tried>>({})
  const [opened, setOpened] = useState<number | null>(null)
  const [busy, setBusy] = useState<number | 'file' | null>(null)
  const [failed, setFailed] = useState<Error | null>(null)
  // 고른 판은 창을 닫아도 든 채로 — 닫히는 동안 제목이 「판  을 켭니다」 로 비지 않게.
  const [confirming, setConfirming] = useState<DefinitionVersion | null>(null)
  const [asking, setAsking] = useState(false)
  // 켜려는 판의 본문 — 자동 적용을 보려고. 판은 고치지 않으므로 같은 판이면 다시 읽지 않는다.
  const confirmBody = useResource(
    () => (confirming ? relayApi.definition(slug, confirming.version) : Promise.resolve(null)),
    [slug, confirming?.version],
  )
  // 버릴 판도 같은 까닭으로 창을 닫아도 든 채로.
  const [discarding, setDiscarding] = useState<DefinitionVersion | null>(null)
  const [askingDiscard, setAskingDiscard] = useState(false)
  const [shown, setShown] = useState<Shown | null>(null)
  /** 빈 글이면 서버의 기본 — 켜진 레시피 판, 없으면 정의 파일의 레시피. */
  const [recipeChoice, setRecipeChoice] = useState('')
  // 버린 레시피 판은 고를 것에서 뺀다 — 고른 판을 그새 버렸으면 기본으로 돌아간다.
  const pickable = recipes.filter((one) => one.status !== 'discarded')
  const recipePick = pickable.some((one) => String(one.version) === recipeChoice) ? recipeChoice : ''

  async function act(what: number | 'file', run: () => Promise<void>) {
    setBusy(what)
    setFailed(null)
    try {
      await run()
    } catch (caught) {
      setFailed(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBusy(null)
    }
  }

  const test = (version: number) =>
    act(version, async () => {
      const recipe = recipePick ? Number(recipePick) : null
      const result = await relayApi.testDefinition(
        slug,
        version,
        recipe === null ? {} : { recipe_version: recipe },
      )
      setTrials((was) => ({ ...was, [version]: { result, recipe } }))
      setOpened(version)
    })

  const showBody = (version: number) =>
    act(version, async () => {
      const found = await relayApi.definition(slug, version)
      setShown({ title: `판 ${found.version} 본문`, note: found.note || '메모 없음', body: found.body })
    })

  const showFile = () =>
    act('file', async () => {
      const found = await relayApi.definitionFile(slug)
      setShown({
        title: '정의 파일',
        note: found.body
          ? found.path
          : '같은 slug 의 정의 파일이 없습니다 — 이 파이프라인은 정의 판으로만 있습니다.',
        body: found.body,
        problems: found.problems,
      })
    })

  async function activate(version: number) {
    await relayApi.activateDefinition(slug, version)
    versions.reload()
    onChanged()
  }

  const list = versions.data ?? []

  async function discard(version: number) {
    await relayApi.discardDefinition(slug, version)
    if (!draft) {
      // 켜진 판이나 정의 파일이 돈다 — 초안을 버려도 상세는 그대로다.
      versions.reload()
      return
    }
    if (!othersLeft(list, version)) {
      // 마지막 초안이었다 — 이 파이프라인은 지도에서 빠지고 상세는 404 가 된다.
      navigate(LIST, { replace: true })
      return
    }
    // 지금 도는 것(「초안 N」)이 그다음 초안으로 바뀐다.
    versions.reload()
    onChanged()
  }

  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-base font-semibold">정의 판</h2>
        <Button size="xs" variant="ghost" disabled={busy !== null} onClick={() => void showFile()}>
          {busy === 'file' && <Loader2 className="animate-spin" />}
          정의 파일 보기
        </Button>
      </div>
      <p className="text-muted-foreground text-sm">
        AI(MCP)가 파이프라인 정의(원천 · 어댑터 · 대상 · 일정)의 초안을 작성하고 시험합니다 — 사용
        여부는 사람이 여기서 정합니다. 사용하면 다음 실행부터 같은 slug 의 정의 파일 대신 그 판을
        따릅니다. 현재 정의:{' '}
        <span className="text-foreground font-medium">{currentText(definitionLabel)}</span>
      </p>
      <ErrorNotice error={versions.error ?? failed} />
      {versions.data && list.length === 0 && (
        <p className="text-muted-foreground text-sm">
          아직 판이 없습니다 — 정의 파일을 사용합니다. AI(MCP)가 정의 초안을 작성하면 여기 한
          줄이 생깁니다.
        </p>
      )}
      {list.length > 0 && pickable.length > 0 && (
        <label className="flex flex-wrap items-center gap-2 text-sm">
          <span className="text-muted-foreground">시험에 사용할 레시피</span>
          <select
            className={SELECT}
            value={recipePick}
            onChange={(event) => setRecipeChoice(event.target.value)}
          >
            <option value="">사용 중인 레시피(없으면 정의 파일의 것)</option>
            {pickable.map((one) => (
              <option key={one.version} value={String(one.version)}>
                레시피 판 {one.version} · {statusLabel('relay_recipe', one.status)}
              </option>
            ))}
          </select>
        </label>
      )}
      {list.length > 0 && (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>판</TableHead>
              <TableHead>상태</TableHead>
              <TableHead>요약</TableHead>
              <TableHead>메모</TableHead>
              <TableHead>작성 경로</TableHead>
              <TableHead>만든 시각</TableHead>
              <TableHead>사용 시작</TableHead>
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {list.map((one) => {
              const tried = trials[one.version]
              const discarded = one.status === 'discarded'
              return (
                <Fragment key={one.version}>
                  <TableRow className={cn(discarded && 'text-muted-foreground')}>
                    <TableCell className="font-mono">판 {one.version}</TableCell>
                    <TableCell>
                      <StatusBadge kind="relay_definition" value={one.status} />
                      {tried && (
                        <StatusBadge
                          kind="relay_trial"
                          value={trialVerdict(tried.result)}
                          className="ml-1"
                        />
                      )}
                    </TableCell>
                    <TableCell className="max-w-xs font-mono text-xs whitespace-normal">
                      {one.summary || '—'}
                    </TableCell>
                    <TableCell className="max-w-xs text-xs whitespace-normal">
                      {one.note || '—'}
                    </TableCell>
                    <TableCell className="text-xs">{one.client || '—'}</TableCell>
                    <TableCell className="whitespace-nowrap">{shownDateTime(one.created_at)}</TableCell>
                    <TableCell className="whitespace-nowrap">{shownDateTime(one.activated_at)}</TableCell>
                    <TableCell className="space-x-1 text-right">
                      {/* 버린 판은 본문만 — 다시 켜지 않는다(번호도 다시 안 쓴다). */}
                      {!discarded && (
                        <Button
                          size="xs"
                          variant="outline"
                          aria-label={`정의 판 ${one.version} 시험`}
                          disabled={busy !== null}
                          onClick={() => void test(one.version)}
                        >
                          {busy === one.version && <Loader2 className="animate-spin" />}
                          시험
                        </Button>
                      )}
                      {one.status !== 'active' && !discarded && (
                        <Button
                          size="xs"
                          aria-label={`정의 판 ${one.version} 사용`}
                          disabled={busy !== null}
                          onClick={() => {
                            setConfirming(one)
                            setAsking(true)
                          }}
                        >
                          사용
                        </Button>
                      )}
                      {one.status === 'draft' && (
                        <Button
                          size="xs"
                          variant="ghost"
                          aria-label={`정의 판 ${one.version} 폐기`}
                          disabled={busy !== null}
                          onClick={() => {
                            setDiscarding(one)
                            setAskingDiscard(true)
                          }}
                        >
                          폐기
                        </Button>
                      )}
                      <Button
                        size="xs"
                        variant="ghost"
                        aria-label={`정의 판 ${one.version} 본문 보기`}
                        disabled={busy !== null}
                        onClick={() => void showBody(one.version)}
                      >
                        본문 보기
                      </Button>
                      {tried && (
                        <Button
                          size="xs"
                          variant="ghost"
                          onClick={() => setOpened(opened === one.version ? null : one.version)}
                        >
                          {opened === one.version ? '결과 접기' : '결과 펼치기'}
                        </Button>
                      )}
                    </TableCell>
                  </TableRow>
                  {tried && opened === one.version && (
                    <TableRow className="hover:bg-transparent">
                      <TableCell colSpan={8} className="bg-muted/30 whitespace-normal">
                        <DefinitionTrialView trial={tried.result} />
                      </TableCell>
                    </TableRow>
                  )}
                </Fragment>
              )
            })}
          </TableBody>
        </Table>
      )}

      <ConfirmDialog
        open={asking}
        title={`정의 판 ${confirming?.version ?? ''} 을 사용합니다`}
        description={
          <div className="space-y-2">
            <p>
              다음 실행부터 이 판을 따릅니다 — 같은 slug 의 정의 파일이 있으면 그 파일 대신입니다(파일은
              삭제하지 않지만 더는 읽지 않습니다). 일정도 이 판의 것을 따릅니다.
            </p>
            {definitionLabel.startsWith('판') && (
              <p>
                지금 사용 중인 {definitionLabel} 은 사용 종료로 바뀝니다 — 사용 종료한 판은 언제든
                다시 사용할 수 있습니다.
              </p>
            )}
            {definitionLabel.startsWith('초안') && (
              <p>
                이 파이프라인은 지금 초안이라 실행되지 않습니다 — 사용하면 「지금 실행」 이 표시되고,
                일정이 있으면 그 일정대로도 실행됩니다.
              </p>
            )}
            <p>사용하는 것만으로는 대상에 아무것도 보내지 않습니다.</p>
            <AutoApplyNote found={confirmBody} />
            <TrialNote
              tried={confirming ? trials[confirming.version] : undefined}
              recipes={recipes}
            />
          </div>
        }
        confirmLabel="사용"
        confirmDisabled={reading(confirmBody)}
        onConfirm={() => activate(confirming!.version)}
        onClose={() => setAsking(false)}
      />

      <ConfirmDialog
        open={askingDiscard}
        title={`정의 판 ${discarding?.version ?? ''} 을 폐기합니다`}
        description={
          <div className="space-y-2">
            <p>
              초안 판 {discarding?.version} 을 폐기합니다 — 복원할 수 없습니다. 판 번호는 다시
              사용하지 않습니다. 폐기한 판은 목록에 「폐기」 로 남고 본문은 볼 수 있습니다.
            </p>
            {draft && discarding && !othersLeft(list, discarding.version) && (
              <p className="text-amber-700 dark:text-amber-400">
                이 파이프라인의 마지막 초안입니다 — 사용 중인 판도 정의 파일도 없어, 폐기하면
                파이프라인이 지도와 목록에서 사라집니다. 폐기한 뒤 파이프라인 목록으로 돌아갑니다.
              </p>
            )}
          </div>
        }
        confirmLabel="폐기"
        destructive
        onConfirm={() => discard(discarding!.version)}
        onClose={() => setAskingDiscard(false)}
      />

      <Dialog open={shown !== null} onOpenChange={(next) => !next && setShown(null)}>
        <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>{shown?.title}</DialogTitle>
            <DialogDescription className="break-all">{shown?.note}</DialogDescription>
          </DialogHeader>
          {(shown?.problems ?? []).length > 0 && (
            <ul className="text-destructive list-disc pl-4 text-sm">
              {shown!.problems!.map((one) => (
                <li key={one}>{one}</li>
              ))}
            </ul>
          )}
          {shown?.body !== null && shown?.body !== undefined && (
            <pre className="bg-muted/30 overflow-auto rounded-md border p-2 text-xs">
              {JSON.stringify(shown.body, null, 2)}
            </pre>
          )}
        </DialogContent>
      </Dialog>
    </section>
  )
}
