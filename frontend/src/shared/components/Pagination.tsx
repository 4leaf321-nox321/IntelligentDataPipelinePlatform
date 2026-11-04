/**
 * 쪽 넘기기.
 *
 * **서버가 상한을 강제하므로 화면에는 반드시 이것이 필요하다**(`shared/pagination.py`).
 * 없으면 50건이 넘는 순간 나머지를 볼 방법이 아예 없다 — 그리고 목록은 잘렸다는
 * 말을 하지 않으므로, 사람은 그것이 전부라고 읽는다.
 *
 * ## 몇 건 중 몇 건인지 말한다
 *
 * 「다음」 단추만 두면 지금 어디쯤인지, 끝이 있기는 한지 알 수 없다. 서버가 `total`
 * 을 주는 이유가 그것이다 — 안 주면 화면은 "다음 쪽이 있나" 를 알려고 한 건 더
 * 요청하는 편법을 쓰게 되고, 그 편법은 화면마다 달라진다.
 */

import { ChevronLeft, ChevronRight } from 'lucide-react'

import { Button } from '@/shared/components/ui/button'

interface PaginationProps {
  total: number
  limit: number
  offset: number
  onChange: (offset: number) => void
  /** 세는 단위. 「12건」 의 「건」. */
  unit?: string
}

export function Pagination({ total, limit, offset, onChange, unit = '건' }: PaginationProps) {
  // **한 쪽에 다 들어가면 안 그린다.** 누를 수 없는 단추 둘은 자리만 차지하고,
  // 그 자리를 매번 보면 사람은 곧 그 줄 전체를 안 읽는다.
  if (total <= limit) {
    return total > 0 ? (
      <p className="text-muted-foreground text-sm">
        {total.toLocaleString()}
        {unit}
      </p>
    ) : null
  }

  const page = Math.floor(offset / limit) + 1
  const pages = Math.ceil(total / limit)
  const from = offset + 1
  const to = Math.min(offset + limit, total)

  return (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <p className="text-muted-foreground text-sm tabular-nums">
        {total.toLocaleString()}
        {unit} 중 {from.toLocaleString()}–{to.toLocaleString()}
      </p>
      <div className="flex items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          disabled={offset <= 0}
          onClick={() => onChange(Math.max(0, offset - limit))}
        >
          <ChevronLeft className="size-4" />
          이전
        </Button>
        <span className="text-muted-foreground text-sm tabular-nums">
          {page} / {pages}
        </span>
        <Button
          variant="outline"
          size="sm"
          disabled={to >= total}
          onClick={() => onChange(offset + limit)}
        >
          다음
          <ChevronRight className="size-4" />
        </Button>
      </div>
    </div>
  )
}
