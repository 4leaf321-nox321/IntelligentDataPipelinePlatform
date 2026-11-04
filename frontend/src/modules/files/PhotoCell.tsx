/**
 * 사진 칸 — 어떤 자료의 한 자리(`owner_field`)에 사진을 **그 자리에서** 붙이고 떼고 크게 본다.
 *
 * 첨부 패널(`AttachmentList`)과 같은 길이다 — 올리기(`useAttachmentUpload` — 차례로 · 상한 · 실패한
 * 이름), 미리보기(`AttachmentThumbnail`), 크게 보기(`ImageViewer`). 다른 것은 모양뿐이다: 표의 칸
 * 안에 작게, 사진의 비율대로 나란히, 사진은 부르는 쪽이 한 번에 모아 준다(칸마다 목록을 부르지 않게).
 *
 * 붙이는 길은 셋 — 「사진」 단추로 고르기, 칸으로 끌어다 놓기(`useFileDrop`), 칸의 글을 누른 채
 * 붙여넣기(화면 캡처 — `AutoSaveText` 의 `onFiles`). 그 자리가 받는 것(이미지만)과 부서는 자료의
 * 도메인이 정한다(`extensions.register_attachment_owner`) — 부서를 보내지 않는다.
 *
 * `shared` 가 아니라 여기 두는 까닭: 첨부 API · 미리보기 · 크게 보기를 쓴다 — `shared` 가 모듈을
 * import 하면 방향이 거꾸로 선다(`PropertyFields` 와 같은 까닭). 다른 확장도 이 자리에서 가져다 쓴다.
 */

import { useRef, useState } from 'react'
import type { DragEvent } from 'react'
import { ImagePlus, Trash2 } from 'lucide-react'

import { attachmentApi } from '@/modules/files/api'
import type { Attachment } from '@/modules/files/api'
import { IMAGE_ACCEPT } from '@/modules/files/AttachmentList'
import { AttachmentThumbnail } from '@/modules/files/AttachmentThumbnail'
import { ImageViewer } from '@/modules/files/ImageViewer'
import type { AttachmentUpload } from '@/modules/files/useAttachmentUpload'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { cn } from '@/shared/lib/utils'

/** 칸에 붙은 사진 하나 — 부르는 쪽(그 화면의 서버)이 실어 준 것. */
export interface CellPhoto {
  id: string
  name: string
  is_image: boolean
  width: number | null
  height: number | null
  size_bytes: number
}

/** 끌어다 놓기 — 파일이 올 때만 받는다(글 · 링크를 끌면 평소처럼). `onFiles` 가 null 이면 안 받는다. */
export function useFileDrop(onFiles: ((files: File[]) => void) | null) {
  const [dragging, setDragging] = useState(false)
  return {
    dragging,
    handlers:
      onFiles === null
        ? {}
        : {
            onDragOver: (event: DragEvent) => {
              if (!event.dataTransfer.types.includes('Files')) return
              event.preventDefault()
              setDragging(true)
            },
            onDragLeave: () => setDragging(false),
            onDrop: (event: DragEvent) => {
              if (!event.dataTransfer.files?.length) return
              event.preventDefault()
              setDragging(false)
              onFiles(Array.from(event.dataTransfer.files))
            },
          },
  }
}

/** 끌고 있는 동안 칸이 받는다는 것을 보이는 테두리. */
export function dropRing(dragging: boolean): string {
  return cn(dragging && 'ring-primary/60 bg-primary/5 ring-2 ring-inset')
}

/** 미리보기의 높이(px) — 너비는 사진의 비율대로. */
const HEIGHT = 88

export function PhotoCell({
  ownerTable,
  ownerId,
  field,
  label,
  photos,
  canEdit,
  upload,
  onChanged,
}: {
  ownerTable: string
  ownerId: string
  field: string
  /** 칸 이름 — 「문제점 사진」. 단추 · 화면 읽기가 쓴다. */
  label: string
  photos: CellPhoto[]
  canEdit: boolean
  /** 이 칸의 올리기(`useAttachmentUpload`) — 칸의 글 · 끌어다 놓기와 같은 것을 쓴다. */
  upload: AttachmentUpload
  /** 뗀 뒤 — 부르는 쪽이 다시 읽는다. */
  onChanged: () => void
}) {
  const [viewing, setViewing] = useState<number | null>(null)
  const [failed, setFailed] = useState<Error | null>(null)
  const pick = useRef<HTMLInputElement>(null)
  const images = photos.filter((one) => one.is_image)
  const files = photos.filter((one) => !one.is_image)

  async function remove(one: CellPhoto) {
    setFailed(null)
    try {
      await attachmentApi.remove(one.id)
      onChanged()
    } catch (caught) {
      setFailed(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    }
  }

  if (photos.length === 0 && !canEdit) return null
  return (
    <div className="space-y-1">
      {(images.length > 0 || canEdit) && (
        <ul className="flex flex-wrap items-end gap-1.5" aria-label={label}>
          {images.map((one, index) => {
            const ratio = one.width && one.height ? one.width / one.height : 4 / 3
            return (
              <li key={one.id} className="group relative break-inside-avoid">
                {/* 비율대로 — 가로 사진이 정사각형으로 잘리면 보고서에서 무엇을 찍었는지 안 보인다. */}
                <AttachmentThumbnail
                  id={one.id}
                  name={one.name}
                  fit="contain"
                  onOpen={() => setViewing(index)}
                  style={{ width: Math.round(HEIGHT * ratio), height: HEIGHT, maxWidth: '100%' }}
                  className="bg-background rounded-sm print:border-0"
                />
                {canEdit && (
                  <button
                    type="button"
                    aria-label={`${one.name} 삭제`}
                    title="사진 삭제"
                    onClick={() => void remove(one)}
                    className="bg-background/90 text-muted-foreground hover:text-destructive absolute top-0.5 right-0.5 rounded p-0.5 opacity-0 shadow-sm group-hover:opacity-100 focus:opacity-100 print:hidden"
                  >
                    <Trash2 className="size-3.5" />
                  </button>
                )}
              </li>
            )
          })}
          {canEdit && (
            <li className="print:hidden">
              <input
                ref={pick}
                type="file"
                multiple
                hidden
                accept={IMAGE_ACCEPT}
                aria-label={`${label} 파일`}
                onChange={(event) => {
                  const chosen = Array.from(event.target.files ?? [])
                  // **값을 비운다** — 같은 파일을 다시 고르면 change 가 안 나 화면이 먹통으로 읽힌다.
                  event.target.value = ''
                  void upload.uploadAll(chosen)
                }}
              />
              <button
                type="button"
                onClick={() => pick.current?.click()}
                disabled={upload.progress !== null}
                aria-label={`${label} 업로드`}
                title={`${label} — 선택하거나, 이 칸으로 끌어다 놓거나, 글을 클릭한 뒤 붙여넣기(Ctrl+V)`}
                className="text-muted-foreground hover:text-foreground hover:border-foreground/30 inline-flex h-7 items-center gap-1 rounded-sm border border-dashed px-1.5 text-[11px]"
              >
                <ImagePlus className="size-3.5" />
                {upload.progress ?? '사진'}
              </button>
            </li>
          )}
        </ul>
      )}
      {files.length > 0 && (
        <ul className="text-muted-foreground space-y-0.5 text-[11px]">
          {files.map((one) => (
            <li key={one.id}>
              <button
                type="button"
                className="hover:text-foreground underline underline-offset-2"
                onClick={() => void attachmentApi.download(one.id, one.name)}
              >
                {one.name}
              </button>
            </li>
          ))}
        </ul>
      )}
      <ErrorNotice error={failed ?? upload.error} className="text-xs" />
      {viewing !== null && images[viewing] && (
        <ImageViewer
          images={images.map((one) => asAttachment(one, ownerTable, ownerId, field))}
          index={viewing}
          onIndex={setViewing}
          onClose={() => setViewing(null)}
        />
      )}
    </div>
  )
}

function asAttachment(
  one: CellPhoto,
  ownerTable: string,
  ownerId: string,
  field: string,
): Attachment {
  return {
    id: one.id,
    owner_table: ownerTable,
    owner_id: ownerId,
    owner_field: field,
    original_name: one.name,
    content_type: '',
    size_bytes: one.size_bytes,
    sha256: '',
    created_at: '',
    is_image: one.is_image,
    width: one.width,
    height: one.height,
  }
}
