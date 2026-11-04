/**
 * **서버가 이 화면보다 새 판이면** 맨 위에 띠 하나 — 「새로 고침」.
 *
 * 열어 둔 탭은 업데이트를 모른다. 그대로 쓰다가 아직 안 가 본 메뉴를 누르면 옛 조각을
 * 찾다 멈춘다(`shared/newBuild`). 그 전에 말해 두면 사람이 쓰던 것을 저장하고 고친다.
 * 옛 판 서버가 답하면(이중화를 한 대씩 올리는 중) 띄우지 않는다.
 */

import { RefreshCw } from 'lucide-react'

import { Button } from '@/shared/components/ui/button'
import { useNewerServer } from '@/shared/newBuild'

export function NewBuildBanner() {
  const newer = useNewerServer()
  if (!newer) return null
  return (
    <div
      role="status"
      className="flex shrink-0 items-center justify-between gap-3 border-b bg-amber-50 px-4 py-2 text-sm text-amber-900 dark:bg-amber-950 dark:text-amber-100"
    >
      <span>
        새 판(<span className="font-mono">{newer}</span>)이 나왔습니다 — 새로 고치면 받습니다.
        쓰던 것이 있으면 저장한 뒤에 고치세요.
      </span>
      <Button size="sm" variant="outline" onClick={() => window.location.reload()}>
        <RefreshCw className="mr-1 size-3.5" />
        새로 고침
      </Button>
    </div>
  )
}
