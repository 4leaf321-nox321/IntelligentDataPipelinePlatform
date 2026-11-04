/**
 * 사진 크게 보기 — 원본을 받아 띄우고, 같은 칸의 사진을 앞뒤로 넘긴다.
 *
 * 원본은 이 창을 열 때만 받는다 — 격자는 미리보기(320px)만 받는다. 휴대폰 사진은 픽셀을 눕혀
 * 두고 EXIF 로 「세워 보라」 고 적는데, 브라우저가 그것을 읽어 스스로 세운다.
 */

import { useEffect } from 'react'
import { ChevronLeft, ChevronRight, Download } from 'lucide-react'

import { attachmentApi, attachmentPaths } from '@/modules/files/api'
import type { Attachment } from '@/modules/files/api'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { Button } from '@/shared/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/shared/components/ui/dialog'
import { useBlobUrl } from '@/shared/hooks/useBlobUrl'

interface ImageViewerProps {
  images: Attachment[]
  index: number
  onIndex: (index: number) => void
  onClose: () => void
}

/** 1.4MB 처럼. */
export function shownSize(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)}KB`
  return `${(bytes / 1024 / 1024).toFixed(1)}MB`
}

export function ImageViewer({ images, index, onIndex, onClose }: ImageViewerProps) {
  const one = images[index]
  const original = useBlobUrl(one ? attachmentPaths.content(one.id) : null)
  const many = images.length > 1

  useEffect(() => {
    if (!many) return
    function onKey(event: KeyboardEvent) {
      if (event.key === 'ArrowLeft') onIndex((index - 1 + images.length) % images.length)
      if (event.key === 'ArrowRight') onIndex((index + 1) % images.length)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [many, index, images.length, onIndex])

  if (!one) return null
  const size = one.width && one.height ? `${one.width} × ${one.height}px · ` : ''

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-5xl">
        <DialogHeader>
          <DialogTitle className="truncate pr-8">{one.original_name}</DialogTitle>
          <DialogDescription>
            {size}
            {shownSize(one.size_bytes)}
            {many && ` · ${index + 1} / ${images.length}`}
          </DialogDescription>
        </DialogHeader>

        <div className="bg-muted/40 relative flex min-h-[40vh] items-center justify-center rounded-md">
          {original.url ? (
            <img
              src={original.url}
              alt={one.original_name}
              className="max-h-[70vh] max-w-full object-contain"
            />
          ) : original.error ? (
            <ErrorNotice error={original.error} />
          ) : (
            <p className="text-muted-foreground text-sm">원본을 불러오는 중…</p>
          )}
          {many && (
            <>
              <Button
                variant="secondary"
                size="icon"
                className="absolute left-2"
                aria-label="이전 사진"
                onClick={() => onIndex((index - 1 + images.length) % images.length)}
              >
                <ChevronLeft className="size-5" />
              </Button>
              <Button
                variant="secondary"
                size="icon"
                className="absolute right-2"
                aria-label="다음 사진"
                onClick={() => onIndex((index + 1) % images.length)}
              >
                <ChevronRight className="size-5" />
              </Button>
            </>
          )}
        </div>

        <div className="flex justify-end">
          <Button
            variant="outline"
            size="sm"
            onClick={() => attachmentApi.download(one.id, one.original_name)}
          >
            <Download className="size-4" />
            원본 다운로드
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
