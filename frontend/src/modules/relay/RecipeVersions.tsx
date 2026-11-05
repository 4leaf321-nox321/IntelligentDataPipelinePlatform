/**
 * 레시피 판 — **AI(MCP)가, 또는 사람이 여기 「초안 작성」(`RecipeEditor`)으로 초안을 작성하고 시험한다. 사용
 * 여부는 사람이 여기서 정한다**(ADR 0001 §2).
 *
 * 서버는 토큰으로 온 「켜기」 를 거절한다. 그래서 켜기 전에 여기서 한 번 더 시험해 「켜면 무엇이
 * 바뀌나」 를 사람이 읽게 한다 — 시험하지 않았거나 막힌 판이면 확인 창이 그렇다고 먼저 말한다.
 *
 * 초안은 버릴 수 있다(되돌릴 수 없다 · 번호는 다시 안 쓴다). 버린 판은 목록에 흐리게 남는다.
 */

import { Fragment, useState } from 'react'
import { Loader2 } from 'lucide-react'

import { countsLine, relayApi } from '@/modules/relay/api'
import type { RecipeBody, RecipeSaved, RecipeTrial, RecipeVersion } from '@/modules/relay/api'
import { RecipeEditor } from '@/modules/relay/RecipeEditor'
import { RecipeTrialView } from '@/modules/relay/RecipeTrialView'
import { ConfirmDialog } from '@/shared/components/ConfirmDialog'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { StatusBadge } from '@/shared/components/StatusBadge'
import { Alert, AlertDescription } from '@/shared/components/ui/alert'
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
import type { Resource } from '@/shared/hooks/useResource'
import { shownDateTime } from '@/shared/lib/datetime'
import { cn } from '@/shared/lib/utils'

/** 확인 창의 첫 말 — 이 화면에서 한 시험이 무엇을 보였나. */
function TrialNote({ trial }: { trial: RecipeTrial | undefined }) {
  if (!trial) {
    return (
      <p className="text-amber-700 dark:text-amber-400">
        이 판을 아직 이 화면에서 시험하지 않았습니다 — 먼저 「시험」 으로 무엇이 바뀌는지 보세요.
      </p>
    )
  }
  if (!trial.clean) {
    return (
      <p className="text-destructive">
        시험에서 막혔습니다 — 사용하면 다음 실행이 멈춥니다. 수정한 판을 기다리는 편이 낫습니다.
      </p>
    )
  }
  const line = countsLine(trial.would_send)
  return <p>시험은 깨끗했습니다{line ? ` — 사용하면 보낼 것: ${line}` : ''}.</p>
}

export function RecipeVersions({
  slug,
  recipeLabel,
  versions,
  onActivated,
}: {
  slug: string
  /** 지금 도는 레시피 — 「파일」 이거나 「판 N」. */
  recipeLabel: string
  /** 판 목록 — 정의 판의 「시험에 쓸 레시피」 도 같은 목록을 쓰므로 위에서 한 번 읽어 준다. */
  versions: Resource<RecipeVersion[]>
  onActivated: () => void
}) {
  const [trials, setTrials] = useState<Record<number, RecipeTrial>>({})
  const [opened, setOpened] = useState<number | null>(null)
  const [busy, setBusy] = useState<number | null>(null)
  const [failed, setFailed] = useState<Error | null>(null)
  // 고른 판은 창을 닫아도 든 채로 — 닫히는 동안 제목이 「판  을 켭니다」 로 비지 않게.
  const [confirming, setConfirming] = useState<RecipeVersion | null>(null)
  const [asking, setAsking] = useState(false)
  // 버릴 판도 같은 까닭으로 창을 닫아도 든 채로.
  const [discarding, setDiscarding] = useState<RecipeVersion | null>(null)
  const [askingDiscard, setAskingDiscard] = useState(false)
  const [body, setBody] = useState<RecipeBody | null>(null)
  const [editing, setEditing] = useState(false)
  const [saved, setSaved] = useState<RecipeSaved | null>(null)

  async function act(version: number, run: () => Promise<void>) {
    setBusy(version)
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
      const trial = await relayApi.testRecipe(slug, version)
      setTrials((was) => ({ ...was, [version]: trial }))
      setOpened(version)
    })

  const showBody = (version: number) =>
    act(version, async () => setBody(await relayApi.recipe(slug, version)))

  async function activate(version: number) {
    await relayApi.activateRecipe(slug, version)
    versions.reload()
    onActivated()
  }

  /** 초안을 버린다 — 켜진 판이 그대로 돌므로 상세는 다시 읽지 않는다. */
  async function discard(version: number) {
    await relayApi.discardRecipe(slug, version)
    versions.reload()
  }

  const list = versions.data ?? []
  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-base font-semibold">레시피 판</h2>
        {!editing && (
          <Button
            size="sm"
            variant="outline"
            onClick={() => {
              setSaved(null)
              setEditing(true)
            }}
          >
            초안 작성
          </Button>
        )}
      </div>
      <p className="text-muted-foreground text-sm">
        AI(MCP)가, 또는 여기 「초안 작성」 으로 레시피 초안을 작성하고 시험합니다 — 사용 여부는 사람이
        여기서 정합니다. 사용하면 다음 실행부터 그 판으로 변환합니다. 현재 레시피:{' '}
        <span className="text-foreground font-medium">
          {recipeLabel === '파일' ? '정의 파일의 레시피(사용 중인 판 없음)' : recipeLabel}
        </span>
      </p>
      <ErrorNotice error={versions.error ?? failed} />
      {editing && (
        <RecipeEditor
          slug={slug}
          versions={list}
          onClose={() => setEditing(false)}
          onSaved={(got) => {
            setSaved(got)
            setEditing(false)
            versions.reload()
          }}
        />
      )}
      {saved && (
        <Alert data-testid="recipe-saved">
          <AlertDescription>
            <p>
              판 {saved.version} 초안을 저장했습니다 — 아래 목록에서 「시험」 으로 「사용하면 보낼 것」 을
              확인한 뒤 「사용」 하세요.
            </p>
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
      {versions.data && list.length === 0 && (
        <p className="text-muted-foreground text-sm">
          아직 판이 없습니다 — 정의 파일의 레시피를 사용합니다. AI(MCP)가, 또는 「초안 작성」 으로
          초안을 작성하면 여기 한 줄이 생깁니다.
        </p>
      )}
      {list.length > 0 && (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>판</TableHead>
              <TableHead>상태</TableHead>
              <TableHead>메모</TableHead>
              <TableHead>작성 경로</TableHead>
              <TableHead>만든 시각</TableHead>
              <TableHead>사용 시작</TableHead>
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {list.map((one) => {
              const trial = trials[one.version]
              const discarded = one.status === 'discarded'
              return (
                <Fragment key={one.version}>
                  <TableRow className={cn(discarded && 'text-muted-foreground')}>
                    <TableCell className="font-mono">판 {one.version}</TableCell>
                    <TableCell>
                      <StatusBadge kind="relay_recipe" value={one.status} />
                      {trial && (
                        <StatusBadge
                          kind="relay_trial"
                          value={trial.clean ? 'clean' : 'blocked'}
                          className="ml-1"
                        />
                      )}
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
                          aria-label={`판 ${one.version} 시험`}
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
                          aria-label={`판 ${one.version} 사용`}
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
                          aria-label={`판 ${one.version} 폐기`}
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
                        aria-label={`판 ${one.version} 본문 보기`}
                        disabled={busy !== null}
                        onClick={() => void showBody(one.version)}
                      >
                        본문 보기
                      </Button>
                      {trial && (
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
                  {trial && opened === one.version && (
                    <TableRow className="hover:bg-transparent">
                      <TableCell colSpan={7} className="bg-muted/30 whitespace-normal">
                        <RecipeTrialView trial={trial} />
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
        title={`판 ${confirming?.version ?? ''} 을 사용합니다`}
        description={
          <div className="space-y-2">
            <p>
              다음 실행부터 이 판으로 변환합니다. 지금 사용 중인 것({recipeLabel})은 사용 종료로
              바뀝니다 — 사용 종료한 판은 언제든 다시 사용할 수 있습니다. 사용하는 것만으로는
              대상에 아무것도 보내지 않습니다.
            </p>
            <TrialNote trial={confirming ? trials[confirming.version] : undefined} />
          </div>
        }
        confirmLabel="사용"
        onConfirm={() => activate(confirming!.version)}
        onClose={() => setAsking(false)}
      />

      <ConfirmDialog
        open={askingDiscard}
        title={`판 ${discarding?.version ?? ''} 을 폐기합니다`}
        description={
          <div className="space-y-2">
            <p>
              초안 판 {discarding?.version} 을 폐기합니다 — 복원할 수 없습니다. 판 번호는 다시
              사용하지 않습니다. 폐기한 판은 목록에 「폐기」 로 남고 본문은 볼 수 있습니다.
            </p>
            <p>지금 사용 중인 것({recipeLabel})은 바뀌지 않습니다.</p>
          </div>
        }
        confirmLabel="폐기"
        destructive
        onConfirm={() => discard(discarding!.version)}
        onClose={() => setAskingDiscard(false)}
      />

      <Dialog open={body !== null} onOpenChange={(next) => !next && setBody(null)}>
        <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>판 {body?.version} 본문</DialogTitle>
            <DialogDescription>{body?.note || '메모 없음'}</DialogDescription>
          </DialogHeader>
          <pre className="bg-muted/30 overflow-auto rounded-md border p-2 text-xs">
            {JSON.stringify(body?.body ?? null, null, 2)}
          </pre>
        </DialogContent>
      </Dialog>
    </section>
  )
}
