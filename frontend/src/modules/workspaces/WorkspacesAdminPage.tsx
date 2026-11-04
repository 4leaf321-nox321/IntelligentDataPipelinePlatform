/**
 * 부서 정보 — 전사 조직도.
 *
 * **트리 순서는 서버가 정한다.** 화면이 평면 목록을 받아 스스로 세우면, 부서
 * 선택기와 이 화면의 순서가 달라진다 — 같은 목록이 화면마다 다르게 보인다.
 *
 * **부서를 지우는 것은 예외다.** 기본은 보관(is_active=false)이고, 삭제는 잘못
 * 만든 부서처럼 자료가 아예 없는 경우를 위한 것이다. 자료가 있는 부서를 없애려면
 * 먼저 「자료 이동」 로 다른 부서에 넘긴다.
 *
 * ## 표가 아니라 트리인 이유
 *
 * 조직도의 물음은 거의 언제나 「이게 어디 밑이냐」 다. 평면 표에 깊이만큼 들여쓰면
 * 그 답이 눈에 겨우 보이지만, **바꿀 수는 없다** — 상위를 고치려면 주소를 외워
 * 다른 칸에 쳐 넣어야 했다. 끌어 놓기는 그 일을 손이 아는 방식으로 바꾼다.
 */

import { useMemo, useState } from 'react'
import { ClipboardPaste, Download, Plus, Search } from 'lucide-react'

import { WorkspaceEditDialog } from '@/modules/workspaces/WorkspaceEditDialog'
import { WorkspaceImportDialog } from '@/modules/workspaces/WorkspaceImportDialog'
import { WorkspaceReassignDialog } from '@/modules/workspaces/WorkspaceReassignDialog'
import { WorkspaceTree } from '@/modules/workspaces/WorkspaceTree'
import { workspaceApi } from '@/modules/workspaces/api'
import type { Workspace, WorkspaceReference } from '@/modules/workspaces/api'
import { parentOptions, visibleRows } from '@/modules/workspaces/tree'
import type { MovePlan } from '@/modules/workspaces/tree'
import { ApiError } from '@/shared/api/client'
import { ConfirmDialog } from '@/shared/components/ConfirmDialog'
import { EmptyState } from '@/shared/components/EmptyState'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { PageHeader } from '@/shared/components/PageHeader'
import { SearchablePicker } from '@/shared/components/SearchablePicker'
import { Button } from '@/shared/components/ui/button'
import { Input } from '@/shared/components/ui/input'
import { Label } from '@/shared/components/ui/label'
import { useResource } from '@/shared/hooks/useResource'

const ROOT = '__root__'

export default function WorkspacesAdminPage() {
  const list = useResource(() => workspaceApi.list(true), [])
  const [slug, setSlug] = useState('')
  const [name, setName] = useState('')
  const [parent, setParent] = useState(ROOT)
  const [error, setError] = useState<ApiError | Error | null>(null)
  const [busy, setBusy] = useState(false)

  const [query, setQuery] = useState('')
  const [showArchived, setShowArchived] = useState(false)
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())

  const [importing, setImporting] = useState(false)
  const [editing, setEditing] = useState<Workspace | null>(null)
  const [reassigning, setReassigning] = useState<Workspace | null>(null)

  // 삭제 확인 — **누르기 전에 무엇이 딸려 있는지 보여 준다.**
  const [deleting, setDeleting] = useState<Workspace | null>(null)
  const [references, setReferences] = useState<WorkspaceReference[] | null>(null)

  const all = useMemo(() => list.data ?? [], [list.data])
  const archivedCount = all.filter((one) => !one.is_active).length
  // 보관된 부서는 기본으로 숨긴다. 개편이 잦으면 보관이 쌓이고, 그때 트리에서 지금
  // 쓰는 부서를 찾는 일이 어려워진다.
  const shownSource = showArchived ? all : all.filter((one) => one.is_active)
  const rows = useMemo(
    () => visibleRows(shownSource, { collapsed, query }),
    [shownSource, collapsed, query],
  )

  async function act(run: () => Promise<unknown>) {
    setBusy(true)
    setError(null)
    try {
      await run()
      list.reload()
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBusy(false)
    }
  }

  function toggle(target: string) {
    setCollapsed((before) => {
      const next = new Set(before)
      if (next.has(target)) next.delete(target)
      else next.add(target)
      return next
    })
  }

  async function askDelete(workspace: Workspace) {
    setError(null)
    setReferences(null)
    setDeleting(workspace)
    try {
      setReferences(await workspaceApi.references(workspace.slug))
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="부서 정보"
        description="조직도를 만들고 수정합니다. 끌어 놓아 상하관계를 바꿔도 자료는 하나도 움직이지 않습니다."
        actions={
          <div className="flex gap-2">
            <Button variant="outline" onClick={() => setImporting(true)}>
              <ClipboardPaste className="size-4" />
              붙여넣기 추가
            </Button>
            <Button
              variant="outline"
              onClick={() =>
                act(async () => {
                  await workspaceApi.exportCsv()
                })
              }
            >
              <Download className="size-4" />
              CSV 내보내기
            </Button>
          </div>
        }
      />

      <form
        className="flex flex-wrap items-end gap-2 rounded-md border p-4"
        onSubmit={(event) => {
          event.preventDefault()
          act(async () => {
            await workspaceApi.create({
              slug,
              name,
              parent_slug: parent === ROOT ? null : parent,
            })
            setSlug('')
            setName('')
            setParent(ROOT)
          })
        }}
      >
        <div className="space-y-2">
          <Label htmlFor="slug">주소 이름</Label>
          {/* URL 에 들어가므로 소문자·숫자·하이픈만. 한글 이름은 옆 칸이 갖는다. */}
          <Input
            id="slug"
            value={slug}
            onChange={(event) => setSlug(event.target.value)}
            placeholder="material-lab"
            pattern="[a-z0-9][a-z0-9-]{1,49}"
            required
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="name">부서 이름</Label>
          <Input
            id="name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="재료시험팀"
            required
          />
        </div>
        <div className="w-64 space-y-2">
          <Label htmlFor="parent">상위 부서</Label>
          {/* **주소를 외워 치게 하지 않는다.** 손으로 치면 오타가 조용히 최상위
              부서를 만들고, 그 부서는 조직도 맨 아래에 혼자 선다. */}
          <SearchablePicker
            id="parent"
            options={[{ value: ROOT, label: '최상위 (상위 없음)' }, ...parentOptions(all, null)]}
            value={parent}
            onChange={setParent}
            searchPlaceholder="부서 이름이나 주소로 검색"
          />
        </div>
        <Button type="submit" disabled={busy}>
          <Plus className="size-4" />
          부서 추가
        </Button>
      </form>

      <ErrorNotice error={error ?? list.error} />

      <div className="flex flex-wrap items-center gap-2">
        <div className="relative w-72">
          <Search className="text-muted-foreground absolute top-1/2 left-2 size-4 -translate-y-1/2" />
          <Input
            className="pl-8"
            value={query}
            placeholder="부서 이름·주소·하는 일로 검색"
            onChange={(event) => setQuery(event.target.value)}
          />
        </div>
        <Button
          variant={showArchived ? 'secondary' : 'outline'}
          size="sm"
          disabled={archivedCount === 0 && !showArchived}
          onClick={() => setShowArchived((before) => !before)}
        >
          {showArchived ? '보관 숨기기' : `보관 포함${archivedCount ? ` (${archivedCount})` : ''}`}
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={() =>
            setCollapsed((before) =>
              before.size > 0 ? new Set() : new Set(all.map((one) => one.slug)),
            )
          }
        >
          {collapsed.size > 0 ? '모두 확장' : '모두 접기'}
        </Button>
        <span className="text-muted-foreground text-sm">
          {rows.length}개 보임 / 전체 {all.length}개
        </span>
      </div>

      {rows.length === 0 ? (
        <EmptyState
          title={query ? '맞는 부서가 없습니다' : '아직 부서가 없습니다'}
          hint={
            query
              ? '다른 말로 찾아 보거나, 보관된 부서를 포함해 보세요.'
              : '위에서 하나 만들거나, 다른 플랫폼의 부서 정보를 붙여넣으세요.'
          }
        />
      ) : (
        <WorkspaceTree
          rows={rows}
          all={shownSource}
          busy={busy}
          onToggle={toggle}
          onMove={(plan: MovePlan) =>
            act(() => workspaceApi.move(plan.slug, plan.parentSlug, plan.position))
          }
          onReorder={(target, direction) => act(() => workspaceApi.reorder(target, direction))}
          onEdit={setEditing}
          onReassign={setReassigning}
          onArchive={(one) =>
            act(() => workspaceApi.update(one.slug, { is_active: !one.is_active }))
          }
          onDelete={askDelete}
        />
      )}

      <p className="text-muted-foreground text-xs">
        줄을 끌어 다른 줄 <strong>위의 가는 띠</strong>에 놓으면 그 부서 앞에 형제로 서고,{' '}
        <strong>줄 본체</strong>에 놓으면 그 부서의 막내가 됩니다. 마우스를 쓰지 않으면 ↑/↓ 단추로
        같은 상위 안에서 순서를 바꾸고, 수정(✏️)에서 상위 부서를 선택할 수 있습니다.
      </p>

      <ConfirmDialog
        open={deleting !== null}
        title={`${deleting?.name ?? ''} 부서 삭제`}
        description={
          <div className="space-y-2">
            {/* **무엇이 사라지는지 적는다.** "정말 삭제하시겠습니까" 만 묻는 창은
                아무도 안 읽고 예를 누른다 — 읽을 것이 없기 때문이다. */}
            {references === null ? (
              <p className="text-muted-foreground">무엇이 걸려 있는지 확인 중…</p>
            ) : references.length === 0 ? (
              <p>이 부서를 가리키는 것이 없습니다. 지워도 잃는 자료가 없습니다.</p>
            ) : (
              <>
                <p>이 부서를 가리키는 것들입니다:</p>
                <ul className="space-y-1">
                  {references.map((one) => (
                    <li key={one.table}>
                      {one.label} {one.count}건
                      {one.blocks_delete && (
                        <span className="text-destructive ml-1">— 먼저 정리해야 합니다</span>
                      )}
                    </li>
                  ))}
                </ul>
                <p className="text-muted-foreground">
                  <strong>자료 이동</strong>로 다른 부서에 넘기면 이 목록이 비고, 그때 지울 수
                  있습니다.
                </p>
              </>
            )}
            <p className="text-muted-foreground">
              삭제하는 대신 <strong>보관</strong>으로 두면 자료는 남고 새 활동만 막힙니다.
            </p>
          </div>
        }
        confirmLabel="삭제"
        destructive
        onConfirm={async () => {
          if (!deleting) return
          await workspaceApi.remove(deleting.slug)
          list.reload()
        }}
        onClose={() => setDeleting(null)}
      />
      {importing && (
        <WorkspaceImportDialog
          onClose={() => setImporting(false)}
          onApplied={() => list.reload()}
        />
      )}
      {editing && (
        <WorkspaceEditDialog
          workspace={editing}
          all={all}
          onClose={() => setEditing(null)}
          onSaved={() => list.reload()}
        />
      )}
      {reassigning && (
        <WorkspaceReassignDialog
          source={reassigning}
          all={all}
          onClose={() => setReassigning(null)}
          onDone={() => list.reload()}
        />
      )}
    </div>
  )
}
