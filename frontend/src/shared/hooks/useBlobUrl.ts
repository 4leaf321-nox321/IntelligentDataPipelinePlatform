/**
 * 인증이 필요한 파일을 `<img>` 에 띄울 주소로 — blob 을 받아 `blob:` 주소를 만들고, 화면에서
 * 사라지면 놓는다(안 놓으면 사진 수십 장이 탭의 메모리에 남는다).
 *
 * **서버가 이미지로 읽은 첨부에만 쓴다.** blob 주소는 앱과 같은 출처라, HTML · SVG 를 띄우면
 * 그 안의 스크립트가 앱의 권한으로 돈다(ADR 0012).
 */

import { useEffect, useState } from 'react'

import { fetchBlob } from '@/shared/api/client'

interface BlobUrl {
  url: string | null
  error: Error | null
}

export function useBlobUrl(path: string | null): BlobUrl {
  const [state, setState] = useState<BlobUrl & { path: string | null }>({
    path: null,
    url: null,
    error: null,
  })

  useEffect(() => {
    if (!path) return
    let alive = true
    let made: string | null = null
    fetchBlob(path)
      .then((blob) => {
        if (!alive) return
        made = URL.createObjectURL(blob)
        setState({ path, url: made, error: null })
      })
      .catch((caught: unknown) => {
        if (!alive) return
        setState({
          path,
          url: null,
          error: caught instanceof Error ? caught : new Error(String(caught)),
        })
      })
    return () => {
      alive = false
      if (made) URL.revokeObjectURL(made)
    }
  }, [path])

  // 주소가 바뀐 직후에는 지난 사진을 보이지 않는다 — 다음 장으로 넘겼는데 앞 장이 남아 있으면
  // 사람은 그것을 다음 장으로 읽는다.
  if (state.path !== path) return { url: null, error: null }
  return { url: state.url, error: state.error }
}
