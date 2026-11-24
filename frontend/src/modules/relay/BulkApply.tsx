/**
 * 적용 대기 계획의 일괄 적용 — **시스템 관리자만, 화면에서만**(서버도 토큰의 적용을 거부한다).
 *
 * 파이프라인이 많아지면 밤새 쌓인 적용 대기 계획을 하나씩 상세 화면에서 여는 일이 하루 일과가 된다.
 * 목록에서 계획을 고르고 한 번에 적용 작업을 넣는다. 확인 창은 파이프라인마다 계획의 건수를 보이고
 * **삭제가 들어 있는 계획을 따로 표시한다** — 건수만 나열하면 그 줄은 읽히지 않는다.
 *
 * 적용은 파이프라인마다 기존 적용 작업(`POST /pipelines/{slug}/apply`)을 차례로 넣는 것이다. 하나가 거부돼도
 * 나머지는 넣고, 거부된 것은 결과에 사유와 함께 남긴다.
 */

import { useState } from 'react'
import { CheckCheck, ExternalLink } from 'lucide-react'

import { countsLine, pipelineDetailOf, relayApi } from '@/modules/relay/api'
import type { RunCounts } from '@/modules/relay/api'
import type { PipelineRow } from '@/modules/relay/graph'
import { ConfirmDialog } from '@/shared/components/ConfirmDialog'
import { Button } from '@/shared/components/ui/button'

export interface BulkApplyResult {
  slug: string
  jobId?: string
  error?: string
}

/** 계획에 객체 · 관계 삭제가 들어 있나. */
export function hasRemovals(counts: RunCounts | undefined | null): boolean {
  const groups = [counts?.objects, counts?.relations]
  return groups.some((group) => Object.values(group ?? {}).some((one) => (one.gone ?? 0) > 0))
}

export function BulkApply({
  rows,
  onDone,
}: {
  /** 고른 파이프라인 — 적용 대기이고 대상에 계획이 있는 것만 넘긴다(`board.applicable`). */
  rows: PipelineRow[]
  onDone: (results: BulkApplyResult[]) => void
}) {
  const [open, setOpen] = useState(false)
  const removals = rows.filter((row) => hasRemovals(pipelineDetailOf(row.node).counts))

  async function applyAll() {
    const results: BulkApplyResult[] = []
    for (const row of rows) {
      const slug = row.node.label
      try {
        const queued = await relayApi.apply(slug)
        results.push({ slug, jobId: queued.job_id })
      } catch (caught) {
        const message = caught instanceof Error ? caught.message : ''
        results.push({ slug, error: message || '적용 요청이 거부되었습니다.' })
      }
    }
    onDone(results)
  }

  return (
    <>
      <Button size="sm" onClick={() => setOpen(true)} disabled={!rows.length}>
        <CheckCheck />
        선택한 계획 적용({rows.length.toLocaleString()})
      </Button>
      <ConfirmDialog
        open={open}
        title="계획 일괄 적용"
        confirmLabel={`${rows.length.toLocaleString()}개 적용`}
        destructive={removals.length > 0}
        description={
          <div className="space-y-3">
            <p>
              선택한 파이프라인의 적용 대기 계획을 대상 SP에 반영합니다. 계획의 상세 내용은 각 행의 [계획
              보기]에서 대상 SP의 [작업] 화면으로 확인할 수 있습니다.
            </p>
            {removals.length > 0 && (
              <p className="text-destructive">
                삭제가 포함된 계획 {removals.length.toLocaleString()}건이 있습니다. 대상에서 객체는 사용
                중지되고 관계는 해제됩니다.
              </p>
            )}
            <ul className="max-h-64 space-y-1 overflow-y-auto">
              {rows.map((row) => {
                const detail = pipelineDetailOf(row.node)
                return (
                  <li key={row.node.id} className="flex flex-wrap items-center gap-x-2 text-xs">
                    <span className="font-mono">{row.node.label}</span>
                    <span className="text-muted-foreground">{countsLine(detail.counts) || '건수 미확인'}</span>
                    {hasRemovals(detail.counts) && <span className="text-destructive">삭제 포함</span>}
                    {detail.pending_link && (
                      <a
                        href={detail.pending_link}
                        target="_blank"
                        rel="noreferrer"
                        className="text-primary inline-flex items-center gap-0.5 hover:underline"
                      >
                        계획 보기
                        <ExternalLink className="size-3" />
                      </a>
                    )}
                  </li>
                )
              })}
            </ul>
          </div>
        }
        onConfirm={applyAll}
        onClose={() => setOpen(false)}
      />
    </>
  )
}
