/**
 * 목록의 파일 칸 — 첫 장이 사진이면 **작은 미리보기**, 아니면 아이콘 · 이름. 여럿이면 수를 곁에.
 *
 * 원본을 받지 않는다 — 목록 한 쪽에 사진 수십 장의 원본이면 수백 MB 다. 서버가 만들어 둔
 * 미리보기(긴 변 320px WebP, ADR 0012)를 `useBlobUrl` 로 받고, 그 주소는 원본과 같은 규칙으로
 * 판정한다(못 보는 객체의 것은 없는 것이다). 서버가 `Cache-Control: private` 을 주므로 쪽을
 * 넘겼다 돌아와도 다시 받지 않는다.
 *
 * **서버가 이미지로 읽은 것(`is_image`)만 그림으로 띄운다** — 올린 쪽이 붙인 종류는 믿지 않는다.
 */

import { ImageIcon, Paperclip } from 'lucide-react'

import { attachmentPaths } from '@/modules/files/api'
import { useBlobUrl } from '@/shared/hooks/useBlobUrl'

/** 칸 하나 — 서버의 `FileCellOut`(목록 응답의 `files`). */
export interface AttachmentCellValue {
  count: number
  first: { id: string; original_name: string; is_image?: boolean }
}

function Thumb({ id, name }: { id: string; name: string }) {
  const thumb = useBlobUrl(attachmentPaths.thumbnail(id))
  return (
    <span className="bg-muted/40 flex size-10 shrink-0 items-center justify-center overflow-hidden rounded border">
      {thumb.url ? (
        <img src={thumb.url} alt={name} className="size-full object-cover" />
      ) : (
        <ImageIcon className="text-muted-foreground size-4" aria-label={`${name} 미리보기`} />
      )}
    </span>
  )
}

export function AttachmentCell({ cell }: { cell?: AttachmentCellValue | null }) {
  // 빈 칸은 다른 칸처럼 「—」 — 빈 자리로 두면 아직 못 받은 것인지 없는 것인지 모른다.
  if (!cell) return <span className="text-muted-foreground">—</span>
  const { first, count } = cell
  const more =
    count > 1 ? (
      <span className="text-muted-foreground text-xs tabular-nums" title={`모두 ${count}개`}>
        +{count - 1}
      </span>
    ) : null
  return (
    <span className="inline-flex max-w-56 items-center gap-1.5" title={first.original_name}>
      {first.is_image ? (
        <Thumb id={first.id} name={first.original_name} />
      ) : (
        <>
          <Paperclip className="text-muted-foreground size-4 shrink-0" />
          <span className="truncate">{first.original_name}</span>
        </>
      )}
      {more}
    </span>
  )
}
