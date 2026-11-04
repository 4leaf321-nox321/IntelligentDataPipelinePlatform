/**
 * 첨부 올리기 — 첨부 패널(`AttachmentList`)과 온톨로지 바인딩 키트의 사진 칸(`shared/binding`)이
 * 함께 쓴다. **두 벌로 두지 않는다** — 한쪽만 상한 검사 · 실패 알림을 고치면 두 화면이 다른 규칙으로
 * 올린다.
 *
 * 한 장씩 차례로 올리고(동시에 보내면 큰 사진 여러 장이 서로의 시간을 잡아먹고, 어느 것이 실패했는지
 * 가리기 어렵다), 실패한 것은 이름을 붙여 남기고 나머지는 계속 올린다. 상한을 넘는 파일은 **보내지
 * 않는다** — 다 보낸 뒤에 거절당하면 그 시간이 통째로 헛것이다.
 */

import { useState } from 'react'

import { attachmentApi } from '@/modules/files/api'
import type { Attachment } from '@/modules/files/api'
import { shownLimit, useAttachmentLimit } from '@/modules/files/limit'

export interface AttachmentTarget {
  ownerTable: string
  ownerId: string
  ownerField?: string | null
  /** 어느 부서의 것인가. 비우면 전역 — 객체의 첨부는 서버가 **객체의 부서**를 따른다. */
  workspaceSlug?: string | null
}

export interface AttachmentUpload {
  /** 고른 · 끌어다 놓은 · 붙여 넣은 파일을 차례로 올린다. 진행 중이면 무시한다. 붙은 것을 돌려준다
   * (부르는 쪽이 그 id 로 자리를 정한다 — 자주검증 칸 글의 사진 표시). */
  uploadAll: (files: File[]) => Promise<Attachment[]>
  /** 「2개 중 1번째 업로드 중…」 — 올리는 중이 아니면 null. */
  progress: string | null
  error: Error | null
  setError: (error: Error | null) => void
}

export function useAttachmentUpload(
  target: AttachmentTarget,
  onDone?: () => void,
): AttachmentUpload {
  const [progress, setProgress] = useState<string | null>(null)
  const [error, setError] = useState<Error | null>(null)
  const limit = useAttachmentLimit()

  async function uploadAll(chosen: File[]): Promise<Attachment[]> {
    if (chosen.length === 0 || progress) return []
    setError(null)
    const made: Attachment[] = []
    const failed = chosen
      .filter((file) => file.size > limit)
      .map((file) => `${file.name}: 한 파일은 ${shownLimit(limit)} 까지입니다`)
    const files = chosen.filter((file) => file.size <= limit)
    for (const [index, file] of files.entries()) {
      setProgress(
        files.length > 1 ? `${files.length}개 중 ${index + 1}번째 업로드 중…` : '업로드 중…',
      )
      try {
        const one = await attachmentApi.upload({
          ownerTable: target.ownerTable,
          ownerId: target.ownerId,
          ownerField: target.ownerField,
          workspaceSlug: target.workspaceSlug ?? null,
          file,
        })
        made.push(one)
      } catch (caught) {
        failed.push(`${file.name}: ${caught instanceof Error ? caught.message : '알 수 없는 오류'}`)
      }
    }
    setProgress(null)
    onDone?.()
    if (failed.length > 0) setError(new Error(failed.join(' / ')))
    return made
  }

  return { uploadAll, progress, error, setError }
}
