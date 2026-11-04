/**
 * 타입의 그림 한 개 — **이름을 그림으로 바꾸는 자리는 하나여야 한다.**
 *
 * 정의 화면·사이드바·목록 머리가 각자 바꾸면 그중 하나만 고쳐지는 날이 오고,
 * 그때 같은 타입이 화면마다 다른 그림으로 선다.
 */

import { iconOf } from '@/shared/icons'
import { cn } from '@/shared/lib/utils'

export function TypeIcon({ name, className }: { name?: string | null; className?: string }) {
  const Icon = iconOf(name)
  return <Icon className={cn('text-muted-foreground size-4', className)} />
}
