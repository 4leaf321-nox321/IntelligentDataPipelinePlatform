/**
 * 부서 수정 — 이름·설명·상위·공개.
 *
 * **이름을 못 고치면 조직 개편이 「새로 만들고 이동」 가 된다.** 그러면 부서 id 가
 * 바뀌고, 그 부서를 가리키던 자료가 통째로 끊어진다. 개명은 흔한 일이라 반드시
 * 제자리에서 되어야 한다.
 *
 * 상위는 PATCH 가 아니라 `move` 로 보낸다 — PATCH 로 받으면 「안 바꿈」 과 「뿌리로
 * 올림」 이 둘 다 null 이라 구분할 수 없다.
 */

import { useState } from 'react'

import { workspaceApi } from '@/modules/workspaces/api'
import type { Workspace } from '@/modules/workspaces/api'
import { parentOptions } from '@/modules/workspaces/tree'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { SearchablePicker } from '@/shared/components/SearchablePicker'
import { Button } from '@/shared/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/shared/components/ui/dialog'
import { Input } from '@/shared/components/ui/input'
import { Label } from '@/shared/components/ui/label'

interface Props {
  workspace: Workspace
  all: Workspace[]
  onClose: () => void
  onSaved: () => void
}

const ROOT = '__root__'

export function WorkspaceEditDialog({ workspace, all, onClose, onSaved }: Props) {
  const [name, setName] = useState(workspace.name)
  const [description, setDescription] = useState(workspace.description)
  const [parent, setParent] = useState<string>(workspace.parent_slug ?? ROOT)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | null>(null)

  const options = [
    { value: ROOT, label: '최상위 (상위 없음)', hint: '본부처럼 위가 없는 부서' },
    ...parentOptions(
      all.filter((one) => one.slug !== workspace.slug),
      workspace.slug,
    ),
  ]
  const parentChanged = (parent === ROOT ? null : parent) !== workspace.parent_slug

  async function save() {
    setBusy(true)
    setError(null)
    try {
      if (name !== workspace.name || description !== workspace.description) {
        await workspaceApi.update(workspace.slug, { name, description })
      }
      // 상위를 바꾸면 **형제 끝으로 간다.** 옛 자리의 순서를 새 형제들 사이로 들고
      // 오면 아무도 시키지 않은 자리에 끼어든다.
      if (parentChanged) await workspaceApi.move(workspace.slug, parent === ROOT ? null : parent)
      onSaved()
      onClose()
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open onOpenChange={(open) => !open && !busy && onClose()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{workspace.name} 수정</DialogTitle>
          <DialogDescription>
            주소(<span className="font-mono">{workspace.slug}</span>)는 못 바꿉니다. 다른 시스템이
            이 주소로 부서를 찾고 있어서, 바꾸면 그쪽에서 조용히 안 맞습니다.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="edit-name">부서 이름</Label>
            <Input
              id="edit-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              maxLength={100}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="edit-description">하는 일</Label>
            {/* 조직도만으로는 「재료2팀」 과 「재료3팀」 이 무엇이 다른지 알 수 없다. */}
            <Input
              id="edit-description"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              placeholder="재료 시험과 성적서 발행"
              maxLength={255}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="edit-parent">상위 부서</Label>
            <SearchablePicker
              id="edit-parent"
              options={options}
              value={parent}
              onChange={setParent}
              placeholder="상위 부서를 선택하세요"
              searchPlaceholder="부서 이름이나 주소로 검색"
            />
            {parentChanged && (
              <p className="text-muted-foreground text-xs">
                옮겨도 자료는 하나도 안 움직입니다. 새 형제들의 맨 끝에 놓입니다.
              </p>
            )}
          </div>
        </div>
        <ErrorNotice error={error} />
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={busy}>
            취소
          </Button>
          <Button onClick={save} disabled={busy || name.trim() === ''}>
            {busy ? '저장 중…' : '저장'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
