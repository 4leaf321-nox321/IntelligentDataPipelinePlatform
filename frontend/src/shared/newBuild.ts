/**
 * **새 판이 나왔을 때** — 열어 둔 화면이 옛 조각을 찾다 멈추지 않게.
 *
 * 화면은 조각(JS 파일) 백여 개로 나뉘고, 처음 여는 화면 말고는 그 메뉴를 처음 누를 때
 * 받는다. 조각 이름에는 내용의 해시가 붙고(`JobsPage-B3dqRXw1.js`), 업데이트는 서버
 * 이미지를 통째로 바꾸므로 **옛 이름의 파일은 사라진다.** 업데이트 전부터 열어 둔 탭이
 * 아직 안 가 본 메뉴를 누르면 옛 이름을 찾다 404 를 받고, 화면에는 「Failed to fetch
 * dynamically imported module」 이 떴다(운영에서 가끔 — 새로 고치면 나았다).
 *
 * 두 겹으로 막는다:
 * - **못 받으면 한 번 새로 고친다**(`listenForStaleChunks`) — 새 `index.html` 이 새 이름을
 *   안다. 1분 안에 또 못 받으면 다시 고치지 않는다 — 그때는 파일이 정말 없거나 망이 끊긴
 *   것이라 고쳐도 같고, 되풀이하면 화면이 깜박이기만 한다.
 * - **새 판이 나왔다고 미리 말한다**(`useNewerServer`) — 5분마다 · 탭으로 돌아올 때 서버
 *   버전을 묻는다.
 */

import { useEffect, useState } from 'react'

import { UNKNOWN_VERSION, systemApi } from '@/shared/api/system'

const RELOADED_AT = 'new-build-reloaded-at'
/** 이 안에 한 번 고쳤으면 또 고치지 않는다. */
export const QUIET_MS = 60_000
/** 서버 버전을 다시 묻는 간격. */
export const CHECK_MS = 5 * 60_000

let reloading = false

/** 화면 조각(동적 import)을 못 받은 오류인가 — 브라우저마다 말이 다르다. */
export function isChunkLoadError(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error ?? '')
  return /Failed to fetch dynamically imported module|error loading dynamically imported module|Importing a module script failed|Unable to preload CSS|is not a valid JavaScript MIME type/i.test(
    message,
  )
}

/** 지금 새 판을 받으려고 새로 고치는 중인가 — 오류 화면 대신 「불러오는 중」 을 보인다. */
export function isReloading(): boolean {
  return reloading
}

/**
 * 새 판을 받으려고 **한 번** 새로 고친다 — 시작했으면 참.
 *
 * 기록을 못 남기면(사생활 모드 등) 고치지 않는다 — 되풀이를 막을 길이 없다.
 */
export function reloadForNewBuild(now: number = Date.now()): boolean {
  if (reloading) return true
  try {
    const last = Number(sessionStorage.getItem(RELOADED_AT) || 0)
    if (now - last < QUIET_MS) return false
    sessionStorage.setItem(RELOADED_AT, String(now))
  } catch {
    return false
  }
  reloading = true
  window.location.reload()
  return true
}

/** 앱이 뜰 때 한 번 — Vite 는 조각을 못 받으면 `vite:preloadError` 를 알린다. 막으면
 *  (`preventDefault`) 오류를 던지지 않는다 — 곧 새로 고치므로 오류 화면을 안 보인다. */
export function listenForStaleChunks(): void {
  window.addEventListener('vite:preloadError', (event) => {
    if (reloadForNewBuild()) event.preventDefault()
  })
}

/** `v0.4.47` → [0, 4, 47]. 못 읽으면 null. */
function parse(version: string): number[] | null {
  const found = /^v?(\d+)\.(\d+)\.(\d+)/.exec(version.trim())
  return found ? found.slice(1).map(Number) : null
}

/** 서버가 이 화면보다 **새 판**인가. 옛 판이면 아니다 — 이중화를 한 대씩 올리는 동안에는
 *  아직 안 올린 서버가 답할 수 있다. 못 읽는 버전(개발 · unknown)도 아니다. */
export function isNewer(server: string, mine: string): boolean {
  const a = parse(server)
  const b = parse(mine)
  if (!a || !b) return false
  for (let i = 0; i < 3; i += 1) {
    if (a[i] !== b[i]) return a[i] > b[i]
  }
  return false
}

/** 서버가 이 화면보다 새 판이면 그 버전, 아니면 null. 5분마다 · 탭으로 돌아올 때 묻는다. */
export function useNewerServer(mine: string = __APP_VERSION__): string | null {
  const [server, setServer] = useState<string | null>(null)
  useEffect(() => {
    let alive = true
    const check = () => {
      systemApi
        .health()
        .then((health) => {
          if (alive && health.version !== UNKNOWN_VERSION) setServer(health.version)
        })
        .catch(() => {
          // 못 물으면 그대로 — 띠는 「새 판이 있다」 를 알 때만 뜬다.
        })
    }
    check()
    const timer = window.setInterval(check, CHECK_MS)
    const onVisible = () => {
      if (document.visibilityState === 'visible') check()
    }
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      alive = false
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [])
  return server && isNewer(server, mine) ? server : null
}
