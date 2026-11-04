/**
 * 첨부 한 파일의 상한 — **설치마다 다르다**(`.env` 의 `ATTACHMENT_MAX_BYTES`, 기본 500MB).
 *
 * 화면은 보내기 **전에** 견준다 — 넘는 파일은 아예 보내지 않는다. 서버도 밝힌 크기로 받기 전에
 * 끊지만(413), 그때는 이미 브라우저가 보내기 시작한 뒤다. 생성 화면은 객체를 만들기 전에 본다 —
 * 만든 뒤에 거절되면 객체만 남는다.
 *
 * 한 번만 묻는다(화면마다 묻지 않는다). 못 물었으면 기본값으로 — 서버가 넘는 것을 어차피
 * 거절한다.
 */

import { useEffect, useState } from 'react'

import { api } from '@/shared/api/client'

/** 서버가 아직 안 알려 줬을 때의 상한 — 서버의 기본값(`attachment_max_bytes`). */
export const DEFAULT_ATTACHMENT_MAX_BYTES = 500 * 1024 * 1024

let asked: Promise<number> | null = null

export function attachmentLimit(): Promise<number> {
  asked ??= Promise.resolve()
    .then(() => api.get<{ max_bytes: number }>('/attachments/limits'))
    .then((got) => got.max_bytes)
    .catch(() => {
      asked = null // 다음에 다시 묻는다
      return DEFAULT_ATTACHMENT_MAX_BYTES
    })
  return asked
}

export function useAttachmentLimit(): number {
  const [limit, setLimit] = useState(DEFAULT_ATTACHMENT_MAX_BYTES)
  useEffect(() => {
    let live = true
    void attachmentLimit().then((found) => {
      if (live) setLimit(found)
    })
    return () => {
      live = false
    }
  }, [])
  return limit
}

/** 「500MB」 · 「2GB」 — 상한을 사람에게 보일 말(첨부 · 사진 zip 이 같은 말을 쓴다). */
export function shownLimit(bytes: number): string {
  if (bytes >= 1024 ** 3 && bytes % 1024 ** 3 === 0) return `${bytes / 1024 ** 3}GB`
  return `${Math.floor(bytes / 1024 ** 2).toLocaleString()}MB`
}

/** 상한을 넘는 파일의 안내 — 화면마다 같은 말. 넘는 것이 없으면 null. */
export function tooLargeMessage(files: File[], limit: number): string | null {
  const big = files.filter((one) => one.size > limit)
  if (big.length === 0) return null
  return `한 파일은 ${shownLimit(limit)} 까지입니다 — ${big.map((one) => one.name).join(', ')}`
}
