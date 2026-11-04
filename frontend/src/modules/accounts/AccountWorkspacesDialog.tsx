/**
 * 계정의 **소속 변경** — 어느 부서에 속하고, 어디가 대표 소속인가.
 *
 * 부서 화면에도 멤버 관리가 있는데 여기에도 두는 이유: **사람을 옮기는 일은 사람에서
 * 시작한다.** 「이 사람 소속을 바꿔라」 를 부서 화면에서 하려면 옛 부서를 먼저 찾아 빼고
 * 새 부서를 찾아 넣어야 하고, 중간에 그만두면 두 부서에 걸쳐 있거나 어디에도 없는 계정이
 * 남는다. 여기서는 한 번에 보내고, 서버가 한 번에 정한다.
 *
 * **이름으로 보여 준다.** slug(hq)는 주소지 부서 이름이 아니다 — 사람은 자기 부서를
 * 「본사」 로 안다. 같은 이름의 팀이 본부마다 있을 수 있으므로 경로를 함께 적는다.
 */

import { useState } from 'react'
import { Loader2, Star, X } from 'lucide-react'

import { accountApi } from '@/modules/accounts/api'
import type { Account } from '@/modules/accounts/api'
import type { WorkspaceOption } from '@/shared/api/types'
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

interface Props {
  account: Account
  options: WorkspaceOption[]
  onClose: () => void
  onSaved: () => void
}

export function AccountWorkspacesDialog({ account, options, onClose, onSaved }: Props) {
  const [slugs, setSlugs] = useState<string[]>(account.workspaces.map((one) => one.slug))
  const [home, setHome] = useState<string | null>(account.home_workspace_slug)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | null>(null)

  const byslug = new Map(options.map((one) => [one.slug, one]))
  // 지금 소속인데 목록에 없는 부서(비활성)도 이름을 알아야 지운 줄 모르고 지우지 않는다.
  for (const one of account.workspaces) {
    if (!byslug.has(one.slug))
      byslug.set(one.slug, { slug: one.slug, name: one.name, path: one.path, depth: 0 })
  }

  function add(slug: string) {
    if (slugs.includes(slug)) return
    setSlugs([...slugs, slug])
    if (home === null) setHome(slug)
  }

  function drop(slug: string) {
    const left = slugs.filter((one) => one !== slug)
    setSlugs(left)
    // 대표 소속을 뺐으면 남은 첫 부서가 대표가 된다 — 비워 두면 서버가 되돌려준 값과
    // 화면이 어긋난다.
    if (home === slug) setHome(left[0] ?? null)
  }

  async function save() {
    setBusy(true)
    setError(null)
    try {
      await accountApi.setWorkspaces(account.id, {
        workspace_slugs: slugs,
        home_workspace_slug: home,
      })
      onSaved()
      onClose()
    } catch (caught) {
      // **창을 닫지 않는다.** 닫으면 오류가 어디에도 안 남고, 사람은 바뀐 줄 안다.
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open onOpenChange={(open) => !open && !busy && onClose()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{account.display_name} 의 소속</DialogTitle>
          <DialogDescription>
            여기 적힌 부서가 <strong>소속의 전부</strong>가 됩니다. 별표가 대표 소속 — 로그인해서
            처음 서는 부서입니다.
          </DialogDescription>
        </DialogHeader>

        <ul className="space-y-1">
          {slugs.map((slug) => {
            const found = byslug.get(slug)
            return (
              <li key={slug} className="flex items-center gap-2 rounded-md border p-2 text-sm">
                <Button
                  size="icon"
                  variant="ghost"
                  aria-label={`${found?.name ?? slug} 를 대표 소속으로`}
                  aria-pressed={home === slug}
                  onClick={() => setHome(slug)}
                >
                  <Star
                    className={home === slug ? 'size-4 fill-amber-400 text-amber-500' : 'size-4'}
                  />
                </Button>
                <span className="flex-1">
                  {found?.name ?? slug}
                  <span className="text-muted-foreground ml-2 text-xs">{found?.path ?? slug}</span>
                </span>
                <Button
                  size="icon"
                  variant="ghost"
                  aria-label={`${found?.name ?? slug} 소속 빼기`}
                  onClick={() => drop(slug)}
                >
                  <X className="size-4" />
                </Button>
              </li>
            )
          })}
          {slugs.length === 0 && (
            <li className="text-destructive text-sm">
              소속이 없습니다. 소속 없는 계정은 로그인해도 설 자리가 없습니다 — 한 곳은 골라 주세요.
            </li>
          )}
        </ul>

        <SearchablePicker
          value={null}
          onChange={add}
          placeholder="부서 추가"
          searchPlaceholder="부서 이름이나 주소로 검색"
          emptyText="그런 부서가 없습니다"
          options={options
            .filter((one) => !slugs.includes(one.slug))
            .map((one) => ({
              value: one.slug,
              label: one.name,
              hint: one.path,
              keywords: one.slug,
            }))}
        />

        <ErrorNotice error={error} />

        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={busy}>
            취소
          </Button>
          <Button onClick={() => void save()} disabled={busy || slugs.length === 0}>
            {busy && <Loader2 className="mr-1 size-4 animate-spin" />}
            저장
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
