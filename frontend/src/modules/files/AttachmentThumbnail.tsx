/**
 * 사진 미리보기 한 장 — 누르면 크게 본다. 첨부 패널과 바인딩 키트의 사진 칸이 함께 쓴다.
 *
 * 미리보기(긴 변 320px WebP)를 `useBlobUrl` 로 받는다 — access 토큰은 메모리에만 있어 `img src`
 * 에 주소를 그대로 넣으면 401 이 나고, 화면에는 아무 표시도 안 뜬다. **서버가 이미지로 읽은 첨부에만**
 * 쓴다(ADR 0012).
 */

import type { CSSProperties } from 'react'
import { ImageIcon } from 'lucide-react'

import { attachmentPaths } from '@/modules/files/api'
import { useBlobUrl } from '@/shared/hooks/useBlobUrl'
import { cn } from '@/shared/lib/utils'

export function AttachmentThumbnail({
  id,
  name,
  onOpen,
  className,
  style,
  fit = 'cover',
}: {
  id: string
  name: string
  onOpen: () => void
  /** 크기 — 기본은 첨부 패널의 정사각형(128px). */
  className?: string
  /** 크기를 값으로 — 사진의 비율대로 너비를 정할 때(바인딩 키트의 사진 칸). */
  style?: CSSProperties
  /** `cover` 는 칸을 채워 자르고, `contain` 은 사진 전체를 비율대로 보인다. */
  fit?: 'cover' | 'contain'
}) {
  const thumb = useBlobUrl(attachmentPaths.thumbnail(id))
  return (
    <button
      type="button"
      onClick={onOpen}
      title={name}
      aria-label={`${name} 크게 보기`}
      style={style}
      className={cn(
        'bg-muted/40 hover:ring-primary/40 flex size-32 items-center justify-center overflow-hidden rounded-md border hover:ring-2',
        className,
      )}
    >
      {thumb.url ? (
        <img
          src={thumb.url}
          alt={name}
          className={cn('size-full', fit === 'cover' ? 'object-cover' : 'object-contain')}
        />
      ) : (
        <ImageIcon className="text-muted-foreground size-6" />
      )}
    </button>
  )
}
