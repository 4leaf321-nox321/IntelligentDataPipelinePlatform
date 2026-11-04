/**
 * 조직도 트리 — 끌어 놓기로 상하관계를 바꾼다.
 *
 * ## 놓는 자리는 둘뿐이다
 *
 *     줄 위의 가는 띠   그 부서 **앞**에 형제로 끼운다
 *     줄 본체           그 부서의 **막내 자식**이 된다
 *
 * 「뒤에 끼우기」 를 따로 두지 않은 이유: 셋이 되면 1px 차이로 뜻이 갈리고, 그때
 * 사람은 자기가 무엇을 했는지 손이 아니라 결과를 보고 알게 된다. 맨 뒤로 보내려면
 * 그 부모의 줄 본체에 놓으면 된다 — 두 자리로 트리의 모든 위치에 닿는다.
 *
 * ## 브라우저가 가진 끌기를 쓴다
 *
 * 끌기 라이브러리를 하나 더 들이지 않았다. 이 화면 하나 때문에 번들과 의존성이
 * 늘고, **폐쇄망 배포에서 의존성 하나는 곧 「그날 빌드가 안 되는 이유」** 가 된다.
 * 키보드로도 되게 ↑/↓ 단추를 함께 둔다 — 끌기만 있으면 마우스를 못 쓰는 사람은
 * 조직도를 못 고친다.
 */

import { useState } from 'react'
import {
  ChevronDown,
  ChevronRight,
  ChevronUp,
  GitMerge,
  GripVertical,
  Pencil,
  Trash2,
  Users,
} from 'lucide-react'

import { descendants, movePlan, siblingOrder } from '@/modules/workspaces/tree'
import type { MovePlan, TreeRow } from '@/modules/workspaces/tree'
import type { Workspace } from '@/modules/workspaces/api'
import { StatusBadge } from '@/shared/components/StatusBadge'
import { Button } from '@/shared/components/ui/button'
import { cn } from '@/shared/lib/utils'

interface Props {
  rows: TreeRow[]
  /** 순서를 셀 때 쓰는 전체 목록 — 접히거나 걸러진 줄도 형제로 센다. */
  all: Workspace[]
  busy: boolean
  onToggle: (slug: string) => void
  onMove: (plan: MovePlan) => void
  onReorder: (slug: string, direction: 'up' | 'down') => void
  onEdit: (workspace: Workspace) => void
  onReassign: (workspace: Workspace) => void
  onArchive: (workspace: Workspace) => void
  onDelete: (workspace: Workspace) => void
}

export function WorkspaceTree({
  rows,
  all,
  busy,
  onToggle,
  onMove,
  onReorder,
  onEdit,
  onReassign,
  onArchive,
  onDelete,
}: Props) {
  const [dragging, setDragging] = useState<string | null>(null)
  const [over, setOver] = useState<string | null>(null)

  const blocked = dragging ? descendants(all, dragging) : new Set<string>()

  function drop(target: string, kind: 'before' | 'inside') {
    const from = dragging
    setDragging(null)
    setOver(null)
    if (!from) return
    const plan = movePlan(all, from, target, kind)
    if (plan) onMove(plan)
  }

  function siblingIndex(node: Workspace): { index: number; count: number } {
    const siblings = all
      .filter((one) => (one.parent_slug ?? null) === (node.parent_slug ?? null))
      // 서버의 위 · 아래(`reorder`)와 같은 차례 — `(sort_order, name)`.
      .sort(siblingOrder)
    return { index: siblings.findIndex((one) => one.slug === node.slug), count: siblings.length }
  }

  return (
    <ul className="divide-y rounded-md border">
      {rows.map(({ node, hasChildren, collapsed, hidden }) => {
        const forbidden = blocked.has(node.slug)
        const { index, count } = siblingIndex(node)
        return (
          <li key={node.slug} className="relative">
            {/* 줄 위의 가는 띠 — 여기에 놓으면 그 형제 **앞**으로 간다. */}
            <div
              className={cn(
                '-mb-0.5 h-1 rounded-full transition-colors',
                over === `before:${node.slug}` && !forbidden ? 'bg-primary' : 'bg-transparent',
              )}
              onDragOver={(event) => {
                if (!dragging || forbidden) return
                event.preventDefault()
                setOver(`before:${node.slug}`)
              }}
              onDrop={(event) => {
                event.preventDefault()
                drop(node.slug, 'before')
              }}
            />
            <div
              draggable={!busy}
              onDragStart={() => setDragging(node.slug)}
              onDragEnd={() => {
                setDragging(null)
                setOver(null)
              }}
              onDragOver={(event) => {
                if (!dragging || forbidden) return
                event.preventDefault()
                setOver(`inside:${node.slug}`)
              }}
              onDrop={(event) => {
                event.preventDefault()
                drop(node.slug, 'inside')
              }}
              className={cn(
                'flex items-center gap-2 px-2 py-2 transition-colors',
                over === `inside:${node.slug}` &&
                  !forbidden &&
                  'bg-primary/10 ring-primary/40 ring-1',
                dragging === node.slug && 'opacity-40',
                dragging && forbidden && 'opacity-30',
              )}
              style={{ paddingLeft: `${node.depth * 20 + 8}px` }}
            >
              <GripVertical
                className="text-muted-foreground size-3.5 shrink-0 cursor-grab"
                aria-hidden
              />
              {hasChildren ? (
                <button
                  type="button"
                  className="hover:bg-muted rounded p-0.5"
                  aria-label={collapsed ? '확장' : '접기'}
                  onClick={() => onToggle(node.slug)}
                >
                  {collapsed ? (
                    <ChevronRight className="size-4" />
                  ) : (
                    <ChevronDown className="size-4" />
                  )}
                </button>
              ) : (
                <span className="size-5 shrink-0" />
              )}

              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className={cn('font-medium', !node.is_active && 'text-muted-foreground')}>
                    {node.name}
                  </span>
                  <span className="text-muted-foreground font-mono text-xs">{node.slug}</span>
                  {!node.is_active && <StatusBadge kind="workspace" value="archived" />}
                  {collapsed && hidden > 0 && (
                    <span className="text-muted-foreground text-xs">하위 {hidden}</span>
                  )}
                </div>
                {node.description && (
                  <p className="text-muted-foreground truncate text-xs">{node.description}</p>
                )}
              </div>

              <span className="text-muted-foreground flex shrink-0 items-center gap-1 text-xs tabular-nums">
                <Users className="size-3.5" />
                {node.member_count}
              </span>

              <div className="flex shrink-0 items-center">
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label="위로"
                  title="같은 상위 안에서 위로"
                  disabled={busy || index <= 0}
                  onClick={() => onReorder(node.slug, 'up')}
                >
                  <ChevronUp className="size-4" />
                </Button>
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label="아래로"
                  title="같은 상위 안에서 아래로"
                  disabled={busy || index < 0 || index >= count - 1}
                  onClick={() => onReorder(node.slug, 'down')}
                >
                  <ChevronDown className="size-4" />
                </Button>
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label="수정"
                  title="이름·하는 일·상위 부서"
                  disabled={busy}
                  onClick={() => onEdit(node)}
                >
                  <Pencil className="size-4" />
                </Button>
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label="자료 이동"
                  title="자료를 다른 부서로 통째 이동 (통폐합)"
                  disabled={busy}
                  onClick={() => onReassign(node)}
                >
                  <GitMerge className="size-4" />
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  title="보관하면 자료는 남고 새 활동만 막힙니다"
                  disabled={busy}
                  onClick={() => onArchive(node)}
                >
                  {node.is_active ? '보관' : '되살리기'}
                </Button>
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label="삭제"
                  disabled={busy}
                  onClick={() => onDelete(node)}
                >
                  <Trash2 className="size-4" />
                </Button>
              </div>
            </div>
          </li>
        )
      })}
    </ul>
  )
}
