/**
 * 어떤 자료에 붙은 첨부 — 목록 · 올리기 · 내려받기 · 떼기, 그리고 **사진은 격자로**.
 *
 * 화면이 아니라 **패널**이다. 도메인 상세 화면이 이것을 한 줄로 끼워 쓴다:
 *
 *     <AttachmentList ownerTable="parts" ownerId={part.id} workspaceSlug={part.workspace} />
 *
 * ## 내려받기 · 사진은 평범한 링크로 안 된다
 *
 * access 토큰은 메모리에만 있어서 브라우저가 스스로 여는 주소(`a href` · `img src`)에는 안
 * 실린다. 그러면 401 이 나는데 **화면에는 아무 표시도 안 뜬다.** 내려받기는 `downloadFile`
 * 이, 사진은 `useBlobUrl` 이 받아서 띄운다.
 *
 * ## 무엇을 사진으로 띄우나(ADR 0012)
 *
 * **서버가 열어 보고 이미지로 읽은 것(`is_image`)만.** 올린 쪽이 붙인 종류는 믿지 않는다 —
 * HTML 을 `image/png` 라고 보낼 수 있다. 나머지는 지금처럼 이름 · 크기 · 다운로드다.
 *
 * ## 올리기
 *
 * 단추로 여러 개를 고르거나, 끌어다 놓거나, 패널을 누른 뒤 붙여넣기(Ctrl+V)로 — 화면 캡처를
 * 바로 붙이는 일이 가장 잦다. 한 장씩 차례로 올리고, 실패한 것은 이름을 붙여 알린다.
 */

import { useRef, useState } from 'react'
import { Download, ImageIcon, Paperclip, Trash2, Upload } from 'lucide-react'

import { attachmentApi } from '@/modules/files/api'
import type { Attachment } from '@/modules/files/api'
import { AttachmentThumbnail } from '@/modules/files/AttachmentThumbnail'
import { ImageViewer, shownSize } from '@/modules/files/ImageViewer'
import { useAttachmentUpload } from '@/modules/files/useAttachmentUpload'
import { ApiError } from '@/shared/api/client'
import { EmptyState } from '@/shared/components/EmptyState'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { Button } from '@/shared/components/ui/button'
import { useResource } from '@/shared/hooks/useResource'
import { shownDate } from '@/shared/lib/datetime'

interface AttachmentListProps {
  ownerTable: string
  ownerId: string
  /**
   * 그 행의 **어느 자리**의 첨부인가. 안 주면 행 전체다.
   *
   * 온톨로지의 `file` 속성이 이것을 준다 — 속성이 둘이면 목록도 둘로 갈려야
   * 한다. 안 가르면 「도면」 칸에 「시험성적서」 가 섞여 보이고, 그 목록은
   * 무엇도 말해 주지 못한다.
   */
  ownerField?: string | null
  /** 제목. 자리별로 나눠 쓸 때 무엇의 첨부인지 적는다. */
  title?: string
  /** 어느 부서의 것인가. 비우면 전역 — **시스템 관리자만 붙일 수 있다.** */
  workspaceSlug?: string | null
  /** 고칠 수 있는 사람인가. 서버가 최종 판정을 한다 — 여기는 표시일 뿐이다. */
  canEdit?: boolean
  /**
   * 그 칸이 받는 것 — `image` 면 사진만(서버가 이미지로 못 읽은 것은 거절한다). 고르는 창도
   * 사진만 보이게 한다.
   */
  accept?: string | null
  /**
   * 행 전체를 볼 때(`ownerField` 없음) **빼고 보일 자리** — 그 자리는 화면의 다른 목록이 이미
   * 보인다. 안 빼던 때는 객체 상세의 「그 밖의 첨부」 가 칸별 첨부까지 다 보여 같은 파일이 두 번
   * 섰다(서버는 자리를 안 주면 전부를 준다, 2026-10-08). 지운 속성의 자리는 여기 안 적히므로
   * 그 첨부는 「그 밖의」 에 남는다 — 갈 곳이 없어지지 않게.
   */
  excludeFields?: string[]
}

/** 고르는 창이 사진만 보이게 — 바인딩 키트의 사진 칸도 같은 값을 쓴다. */
export const IMAGE_ACCEPT = 'image/png,image/jpeg,image/gif,image/webp'

export function AttachmentList({
  ownerTable,
  ownerId,
  ownerField,
  title = '첨부',
  workspaceSlug,
  canEdit = false,
  accept,
  excludeFields,
}: AttachmentListProps) {
  const list = useResource(
    () => attachmentApi.list(ownerTable, ownerId, ownerField),
    [ownerTable, ownerId, ownerField],
  )
  const [failure, setFailure] = useState<ApiError | Error | null>(null)
  const [dragging, setDragging] = useState(false)
  const [viewing, setViewing] = useState<number | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const imageOnly = accept === 'image'
  // 올리기는 바인딩 키트의 사진 칸과 한 벌이다(`useAttachmentUpload`) — 차례로 · 상한 · 실패한 이름.
  const {
    uploadAll,
    progress,
    error: uploadError,
    setError: setUploadError,
  } = useAttachmentUpload({ ownerTable, ownerId, ownerField, workspaceSlug }, () => list.reload())
  const error = failure ?? uploadError

  async function act(run: () => Promise<unknown>) {
    setFailure(null)
    setUploadError(null)
    try {
      await run()
      list.reload()
    } catch (caught) {
      setFailure(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    }
  }

  const rows = (list.data ?? []).filter(
    (one) => !one.owner_field || !(excludeFields ?? []).includes(one.owner_field),
  )
  const images = rows.filter((one) => one.is_image)
  const others = rows.filter((one) => !one.is_image)
  const busy = progress !== null

  return (
    <section
      className={`space-y-3 rounded-md outline-none ${dragging ? 'ring-primary/50 ring-2' : ''}`}
      // 붙여넣기는 이 패널을 누른 뒤에 — 화면 어디서나 받으면 다른 칸에 붙이려던 것이 여기로 온다.
      tabIndex={canEdit ? 0 : undefined}
      onPaste={(event) => {
        if (!canEdit) return
        const files = Array.from(event.clipboardData.files)
        if (files.length === 0) return
        event.preventDefault()
        void uploadAll(files)
      }}
      onDragOver={(event) => {
        if (!canEdit || !event.dataTransfer.types.includes('Files')) return
        event.preventDefault()
        setDragging(true)
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(event) => {
        if (!canEdit) return
        event.preventDefault()
        setDragging(false)
        void uploadAll(Array.from(event.dataTransfer.files))
      }}
    >
      <div className="flex items-center justify-between gap-3">
        <h2 className="flex items-center gap-2 text-base font-semibold">
          {imageOnly ? <ImageIcon className="size-4" /> : <Paperclip className="size-4" />}
          {title}
          {rows.length > 0 && (
            <span className="text-muted-foreground text-sm font-normal tabular-nums">
              {rows.length}
            </span>
          )}
        </h2>
        {canEdit && (
          <>
            <input
              ref={fileRef}
              id={`attach-${ownerTable}-${ownerId}-${ownerField ?? 'all'}`}
              type="file"
              multiple
              hidden
              accept={imageOnly ? IMAGE_ACCEPT : undefined}
              onChange={(event) => {
                const files = Array.from(event.target.files ?? [])
                // **값을 비운다.** 안 비우면 같은 파일을 다시 고를 때 change 가
                // 안 나고, 사람은 화면이 먹통이라고 읽는다.
                event.target.value = ''
                void uploadAll(files)
              }}
            />
            <Button
              variant="outline"
              size="sm"
              disabled={busy}
              onClick={() => fileRef.current?.click()}
            >
              <Upload className="size-4" />
              {progress ?? (imageOnly ? '사진 업로드' : '파일 업로드')}
            </Button>
          </>
        )}
      </div>

      <ErrorNotice error={error ?? list.error} />

      {rows.length === 0 ? (
        <EmptyState
          title={imageOnly ? '사진이 없습니다' : '첨부가 없습니다'}
          hint={
            canEdit
              ? `위 단추로 선택하거나, 이 자리로 끌어다 놓거나, 클릭한 뒤 붙여넣기(Ctrl+V)로 업로드할 수 있습니다.${
                  imageOnly ? ' 사진(PNG · JPEG · GIF · WebP)만 업로드할 수 있습니다.' : ''
                }`
              : '아직 올라온 파일이 없습니다.'
          }
        />
      ) : (
        <>
          {images.length > 0 && (
            <ul className="flex flex-wrap gap-2" aria-label={`${title} 사진`}>
              {images.map((one, index) => (
                <li key={one.id} className="relative">
                  <AttachmentThumbnail
                    id={one.id}
                    name={one.original_name}
                    onOpen={() => setViewing(index)}
                  />
                  {canEdit && (
                    <Button
                      variant="secondary"
                      size="icon"
                      className="absolute top-1 right-1 size-7 opacity-80 hover:opacity-100"
                      aria-label={`${one.original_name} 떼기`}
                      onClick={() => act(() => attachmentApi.remove(one.id))}
                    >
                      <Trash2 className="size-3.5" />
                    </Button>
                  )}
                </li>
              ))}
            </ul>
          )}
          {others.length > 0 && (
            <ul className="divide-y rounded-md border">
              {others.map((one: Attachment) => (
                <li key={one.id} className="flex items-center gap-3 px-3 py-2 text-sm">
                  <span className="min-w-0 flex-1 truncate">{one.original_name}</span>
                  <span className="text-muted-foreground shrink-0 text-xs tabular-nums">
                    {shownSize(one.size_bytes)}
                  </span>
                  <span className="text-muted-foreground shrink-0 text-xs">
                    {shownDate(one.created_at)}
                  </span>
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label={`${one.original_name} 다운로드`}
                    onClick={() => act(() => attachmentApi.download(one.id, one.original_name))}
                  >
                    <Download className="size-4" />
                  </Button>
                  {canEdit && (
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label={`${one.original_name} 떼기`}
                      onClick={() => act(() => attachmentApi.remove(one.id))}
                    >
                      <Trash2 className="size-4" />
                    </Button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </>
      )}

      {viewing !== null && images[viewing] && (
        <ImageViewer
          images={images}
          index={viewing}
          onIndex={setViewing}
          onClose={() => setViewing(null)}
        />
      )}
    </section>
  )
}
