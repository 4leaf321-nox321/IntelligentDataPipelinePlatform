/**
 * 흐름도 — 원천 시스템 | API | 파이프라인 | 대상 SP | 타입 | 그 너머.
 *
 * **읽기 전용이다.** 끌어 옮기거나 이을 수 없다 — 그림은 정의에서 나오므로 여기서 옮긴 자리는
 * 다음 읽기에 사라지고, 사람은 저장된 줄 안다. 끌기 · 굴리기로 둘러보고 확대만 한다.
 *
 * 색은 앱의 토큰(`--border` · `--muted-foreground` …)으로 칠한다 — 밝게/어둡게를 바꾸면
 * 그 토큰이 바뀌므로 흐름도도 따라간다.
 */

import { useMemo } from 'react'
import type { CSSProperties } from 'react'
import { Background, BackgroundVariant, Controls, ReactFlow } from '@xyflow/react'
import type { Edge } from '@xyflow/react'
import '@xyflow/react/dist/style.css'

import { NODE_TYPES } from '@/modules/relay/FlowNodes'
import type { ColumnFlowNode, RelayFlowNode } from '@/modules/relay/FlowNodes'
import { edgeWeight } from '@/modules/relay/graph'
import type { FlowEdge, RelayGraph } from '@/modules/relay/graph'
import {
  HEADER_OFFSET,
  NODE_HEIGHT,
  NODE_WIDTH,
  columnHeaders,
  edgeWidth,
  layout,
} from '@/modules/relay/layout'
import { statusTone } from '@/shared/components/StatusBadge'
import type { Tone } from '@/shared/components/StatusBadge'

/** React Flow 의 기본 색을 앱 토큰으로 갈아 끼운다. */
const FLOW_THEME = {
  '--xy-background-color': 'transparent',
  '--xy-background-pattern-color': 'var(--border)',
  '--xy-controls-button-background-color': 'var(--background)',
  '--xy-controls-button-background-color-hover': 'var(--muted)',
  '--xy-controls-button-color': 'var(--foreground)',
  '--xy-controls-button-color-hover': 'var(--foreground)',
  '--xy-controls-button-border-color': 'var(--border)',
  '--xy-attribution-background-color': 'transparent',
} as CSSProperties

/** 확대 단추의 이름 — 화면은 한국어다(읽어 주는 프로그램도 같은 말을 듣게). */
const ARIA_LABELS = {
  'controls.ariaLabel': '확대 단추',
  'controls.zoomIn.ariaLabel': '확대',
  'controls.zoomOut.ariaLabel': '축소',
  'controls.fitView.ariaLabel': '전체 보기',
  'handle.ariaLabel': '연결점',
}

/**
 * 선의 색 — 선은 `currentColor` 로 긋고 색은 글자색 클래스로 준다. 앱 토큰 · 밝게/어둡게를 그대로
 * 타고, 판정은 배지와 같은 표(`statusTone`)에서 온다.
 */
const EDGE_TONE: Record<Tone, string> = {
  neutral: 'text-muted-foreground/60',
  good: 'text-muted-foreground/60',
  warn: 'text-amber-500 dark:text-amber-400',
  bad: 'text-destructive',
}

function toFlowEdge(edge: FlowEdge, graph: RelayGraph, visible: Set<string> | null): Edge {
  const beyond = graph.byId.get(edge.target)?.kind === 'downstream'
  const tone = edge.status ? statusTone('relay_pipeline', edge.status) : 'neutral'
  const weight = edgeWeight(edge, visible ?? undefined)
  return {
    id: edge.id,
    source: edge.source,
    target: edge.target,
    className: beyond ? 'text-muted-foreground/40' : EDGE_TONE[tone],
    // 적용 대기 · 그 너머는 점선 — 아직 안 간 것, 이 플랫폼이 보내지 않는 것.
    style: {
      stroke: 'currentColor',
      strokeWidth: beyond ? 1 : edgeWidth(weight),
      strokeDasharray: beyond || edge.status === 'pending' ? '5 4' : undefined,
    },
    focusable: false,
    selectable: false,
    ariaLabel: weight ? `${weight.toLocaleString()}건` : undefined,
  }
}

export function RelayFlow({
  graph,
  visible,
}: {
  graph: RelayGraph
  /** 거르기에 남은 마디 — null 이면 전부. */
  visible: Set<string> | null
}) {
  const { nodes, edges } = useMemo(() => {
    const shown = visible ? graph.nodes.filter((one) => visible.has(one.id)) : graph.nodes
    const shownEdges = graph.edges.filter(
      (one) => !visible || (visible.has(one.source) && visible.has(one.target)),
    )
    const placed = layout(shown, shownEdges)
    const cards: RelayFlowNode[] = shown.map((one) => ({
      id: one.id,
      type: 'relay',
      position: placed.get(one.id) ?? { x: 0, y: 0 },
      data: { node: one },
      width: NODE_WIDTH,
      height: NODE_HEIGHT,
      draggable: false,
      selectable: false,
      connectable: false,
    }))
    const titles: ColumnFlowNode[] = columnHeaders(shown).map((one) => ({
      id: `column:${one.kind}`,
      type: 'column',
      position: { x: one.x, y: -HEADER_OFFSET },
      data: { title: one.title },
      width: NODE_WIDTH,
      height: 24,
      draggable: false,
      selectable: false,
      connectable: false,
      focusable: false,
    }))
    return {
      nodes: [...titles, ...cards],
      edges: shownEdges.map((one) => toFlowEdge(one, graph, visible)),
    }
  }, [graph, visible])

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={NODE_TYPES}
      style={FLOW_THEME}
      fitView
      fitViewOptions={{ padding: 0.1, maxZoom: 1 }}
      minZoom={0.2}
      maxZoom={1.5}
      nodesDraggable={false}
      nodesConnectable={false}
      nodesFocusable={false}
      edgesFocusable={false}
      elementsSelectable={false}
      deleteKeyCode={null}
      ariaLabelConfig={ARIA_LABELS}
    >
      <Background variant={BackgroundVariant.Dots} gap={24} size={1} />
      <Controls showInteractive={false} />
    </ReactFlow>
  )
}

export default RelayFlow
