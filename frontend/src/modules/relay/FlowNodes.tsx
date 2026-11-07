/**
 * 흐름도의 마디 — 이름 · 작은 글 · 상태. **색은 `StatusBadge` 의 판정을 따른다**(`statusTone`).
 *
 * 파이프라인 마디는 통째로 눌러 상세로 간다. 적용을 기다리면 「계획 보기」 가 대상 SP 의 작업
 * 화면을 새 탭으로 연다 — 계획은 거기서 읽고 거기서(또는 상세의 「적용」 으로) 넣는다. 초안(미사용)은
 * 점선 테두리다 — 상세의 「정의 판」 에서 켜기 전까지 안 돈다.
 *
 * 마지막 대조에서 대상과 어긋났으면 이름 곁에 「어긋남 N건」 을 단다(`DriftBadge`) — 테두리는 실행의
 * 상태 그대로다(대조는 흐름을 멈추지 않는다). 대조 시각은 풍선 도움말이 말한다.
 */

import { Handle, Position } from '@xyflow/react'
import type { Node, NodeProps } from '@xyflow/react'
import { ExternalLink } from 'lucide-react'
import { Link } from 'react-router-dom'

import { pipelineDetailOf } from '@/modules/relay/api'
import type { MapNode } from '@/modules/relay/api'
import { DriftBadge, driftTitle } from '@/modules/relay/DriftBadge'
import { StatusBadge, statusTone } from '@/shared/components/StatusBadge'
import type { StatusKind, Tone } from '@/shared/components/StatusBadge'
import { cn } from '@/shared/lib/utils'

export type RelayFlowNode = Node<{ node: MapNode }, 'relay'>
export type ColumnFlowNode = Node<{ title: string }, 'column'>

/** 마디의 상태를 어느 표로 읽나 — 상태가 있는 것은 파이프라인과 대상뿐이다. */
export function statusKindOf(kind: string): StatusKind | null {
  if (kind === 'pipeline') return 'relay_pipeline'
  if (kind === 'target') return 'relay_target'
  return null
}

export function toneOf(node: Pick<MapNode, 'kind' | 'status'>): Tone {
  const kind = statusKindOf(node.kind)
  return kind && node.status ? statusTone(kind, node.status) : 'neutral'
}

const BORDER: Record<Tone, string> = {
  neutral: 'border-border',
  good: 'border-emerald-500/50',
  warn: 'border-amber-500/70',
  bad: 'border-destructive/70',
}

/** 선은 늘 보이는 점 하나 없이 — 끌어 잇는 그림이 아니다. 자리만 있으면 된다. */
const HIDDEN_HANDLE = { opacity: 0, border: 0, width: 1, height: 1, minWidth: 0, minHeight: 0 }

export function RelayNodeCard({ data }: NodeProps<RelayFlowNode>) {
  const node = data.node
  const statusKind = statusKindOf(node.kind)
  const pending = node.kind === 'pipeline' ? pipelineDetailOf(node) : null
  const planLink = node.status === 'pending' ? pending?.pending_link : ''
  const beyond = node.kind === 'downstream'
  const title = [
    node.label,
    node.sub,
    pending?.late ? `늦음 — ${pending.late}` : '',
    ...(pending?.held ?? []).map((reason) => `받기 점검 멈춤 — ${reason}`),
    driftTitle(node),
  ]
    .filter(Boolean)
    .join('\n')
  return (
    <div
      data-testid={`relay-node-${node.kind}`}
      // **pointer-events 를 되살린다** — 고를 수도 끌 수도 없는 마디는 React Flow 가 감싼 자리에서
      // 이벤트를 끈다(`pointer-events: none`). 그대로 두면 이름 링크 · 「계획 보기」 가 안 눌린다.
      className={cn(
        'bg-card text-card-foreground pointer-events-auto relative flex h-full w-full flex-col justify-between rounded-md border px-2.5 py-1.5 shadow-xs',
        BORDER[toneOf(node)],
        node.kind === 'pipeline' && 'hover:bg-muted/50 border-2',
        // 초안은 아직 안 돈다 — 선처럼 테두리도 점선으로.
        node.kind === 'pipeline' && node.status === 'draft' && 'border-dashed',
        beyond && 'bg-muted/30 text-muted-foreground border-dashed shadow-none',
      )}
      title={title}
    >
      <Handle type="target" position={Position.Left} isConnectable={false} style={HIDDEN_HANDLE} />
      <div className="min-w-0">
        <div className="flex min-w-0 items-center gap-1.5">
          {node.kind === 'pipeline' && node.link ? (
            // **마디 전체가 눌린다** — 이름에 건 링크가 카드 전체를 덮는다(`after:inset-0`).
            <Link
              to={node.link}
              className="block min-w-0 truncate font-mono text-sm font-medium after:absolute after:inset-0 hover:underline"
            >
              {node.label}
            </Link>
          ) : (
            <p className="min-w-0 truncate text-sm font-medium">{node.label}</p>
          )}
          <DriftBadge node={node} />
        </div>
        {node.sub && <p className="text-muted-foreground truncate text-xs">{node.sub}</p>}
      </div>
      <div className="flex min-h-5 items-center gap-2">
        {statusKind && node.status && <StatusBadge kind={statusKind} value={node.status} />}
        {planLink && (
          <a
            href={planLink}
            target="_blank"
            rel="noreferrer"
            className="relative z-10 inline-flex items-center gap-0.5 text-xs underline"
          >
            계획 보기
            <ExternalLink className="size-3" />
          </a>
        )}
      </div>
      <Handle type="source" position={Position.Right} isConnectable={false} style={HIDDEN_HANDLE} />
    </div>
  )
}

export function ColumnTitle({ data }: NodeProps<ColumnFlowNode>) {
  return (
    <p className="text-muted-foreground border-b pb-1 text-xs font-semibold tracking-wide">
      {data.title}
    </p>
  )
}

/** 컴포넌트 바깥에 한 번 — 그릴 때마다 새 객체면 React Flow 가 마디를 전부 다시 만든다. */
export const NODE_TYPES = { relay: RelayNodeCard, column: ColumnTitle }
