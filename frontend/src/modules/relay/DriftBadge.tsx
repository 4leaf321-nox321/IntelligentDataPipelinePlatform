/**
 * 「어긋남 N건」 — 마지막 대조에서 대상 SP 가 기준(지난번 적용된 출력)과 다른 객체 수. 지도의 마디와
 * 파이프라인 목록이 같은 것을 그린다. 대조하지 못했으면(대상에 타입이 없는 등) 「대조 못 함」. 0 이면
 * (대조한 적이 없거나 대상과 같으면) 아무것도 안 그린다.
 *
 * 말과 색은 `StatusBadge` 의 `relay_reconcile` 이 정하고, 수는 배지 옆 글이 말한다.
 */

import { driftOf } from '@/modules/relay/api'
import type { MapNode } from '@/modules/relay/api'
import { StatusBadge } from '@/shared/components/StatusBadge'
import { shownDateTime } from '@/shared/lib/datetime'

/** 마디의 풍선 도움말 한 줄 — 어긋남이 없으면 빈 글. */
export function driftTitle(node: MapNode): string {
  const { drift, at, problem } = driftOf(node)
  if (problem) return `마지막 대조 ${shownDateTime(at)} — 대조하지 못했습니다(상세에서 까닭 확인)`
  return drift ? `마지막 대조 ${shownDateTime(at)} — 대상과 어긋남 ${drift}건` : ''
}

export function DriftBadge({ node }: { node: MapNode }) {
  const { drift, problem } = driftOf(node)
  if (problem) {
    return (
      <span data-testid="relay-drift" title={driftTitle(node)} className="inline-flex shrink-0 items-center text-xs">
        <StatusBadge kind="relay_reconcile" value="problem" />
      </span>
    )
  }
  if (!drift) return null
  return (
    <span
      data-testid="relay-drift"
      title={driftTitle(node)}
      className="inline-flex shrink-0 items-center gap-1 text-xs"
    >
      <StatusBadge kind="relay_reconcile" value="drift" />
      <span>{drift.toLocaleString()}건</span>
    </span>
  )
}
