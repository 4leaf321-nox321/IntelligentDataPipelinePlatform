/**
 * 레시피 판 — **AI(MCP)가 초안을 짓고 시험한다. 켜는 일은 사람이 여기서 한다**(ADR 0001 §2).
 *
 * 서버는 토큰으로 온 「켜기」 를 거절한다. 그래서 켜기 전에 여기서 한 번 더 시험해 「켜면 무엇이
 * 바뀌나」 를 사람이 읽게 한다 — 시험하지 않았거나 막힌 판이면 확인 창이 그렇다고 먼저 말한다.
 */

import { Fragment, useState } from 'react'
import { Loader2 } from 'lucide-react'

import { countsLine, relayApi } from '@/modules/relay/api'
import type { RecipeBody, RecipeTrial, RecipeVersion } from '@/modules/relay/api'
import { RecipeTrialView } from '@/modules/relay/RecipeTrialView'
import { ConfirmDialog } from '@/shared/components/ConfirmDialog'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { StatusBadge } from '@/shared/components/StatusBadge'
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
import { shownDateTime } from '@/shared/lib/datetime'

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
        시험에서 막혔습니다 — 켜면 다음 실행이 멈춥니다. 고친 판을 기다리는 편이 낫습니다.
      </p>
    )
  }
  const line = countsLine(trial.would_send)
  return <p>시험은 깨끗했습니다{line ? ` — 켜면 보낼 것: ${line}` : ''}.</p>
}

export function RecipeVersions({
  slug,
  recipeLabel,
  onActivated,
}: {
  slug: string
  /** 지금 도는 레시피 — 「파일」 이거나 「판 N」. */
  recipeLabel: string
  onActivated: () => void
}) {
  const versions = useResource(() => relayApi.recipes(slug), [slug])
  const [trials, setTrials] = useState<Record<number, RecipeTrial>>({})
  const [opened, setOpened] = useState<number | null>(null)
  const [busy, setBusy] = useState<number | null>(null)
  const [failed, setFailed] = useState<Error | null>(null)
  // 고른 판은 창을 닫아도 든 채로 — 닫히는 동안 제목이 「판  을 켭니다」 로 비지 않게.
  const [confirming, setConfirming] = useState<RecipeVersion | null>(null)
  const [asking, setAsking] = useState(false)
  const [body, setBody] = useState<RecipeBody | null>(null)

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

  const list = versions.data ?? []
  return (
    <section className="space-y-3">
      <h2 className="text-base font-semibold">레시피 판</h2>
      <p className="text-muted-foreground text-sm">
        AI(MCP)가 레시피 초안을 짓고 시험합니다 — 켜는 일은 사람이 여기서 합니다. 켜면 다음
        실행부터 그 판이 돕니다. 지금 도는 것:{' '}
        <span className="text-foreground font-medium">
          {recipeLabel === '파일' ? '정의 파일의 레시피(켜진 판 없음)' : recipeLabel}
        </span>
      </p>
      <ErrorNotice error={versions.error ?? failed} />
      {versions.data && list.length === 0 && (
        <p className="text-muted-foreground text-sm">
          아직 판이 없습니다 — 정의 파일의 레시피가 돕니다. AI(MCP)가 초안을 지으면 여기 한 줄이
          생깁니다.
        </p>
      )}
      {list.length > 0 && (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>판</TableHead>
              <TableHead>상태</TableHead>
              <TableHead>메모</TableHead>
              <TableHead>지은 곳</TableHead>
              <TableHead>만든 시각</TableHead>
              <TableHead>켠 시각</TableHead>
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {list.map((one) => {
              const trial = trials[one.version]
              return (
                <Fragment key={one.version}>
                  <TableRow>
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
                      {one.status !== 'active' && (
                        <Button
                          size="xs"
                          aria-label={`판 ${one.version} 켜기`}
                          disabled={busy !== null}
                          onClick={() => {
                            setConfirming(one)
                            setAsking(true)
                          }}
                        >
                          켜기
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
        title={`판 ${confirming?.version ?? ''} 을 켭니다`}
        description={
          <div className="space-y-2">
            <p>
              다음 실행부터 이 판이 돕니다. 지금 도는 것({recipeLabel})은 내려 둡니다 — 내려 둔
              판은 언제든 다시 켤 수 있습니다. 켜는 것만으로는 대상에 아무것도 안 갑니다.
            </p>
            <TrialNote trial={confirming ? trials[confirming.version] : undefined} />
          </div>
        }
        confirmLabel="켜기"
        onConfirm={() => activate(confirming!.version)}
        onClose={() => setAsking(false)}
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
