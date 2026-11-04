/**
 * 주소 접두어 — 여러 플랫폼이 한 호스트명에 경로로 붙을 때(`/plm`). 서버가 `index.html` 에
 * `<meta name="app-base">` 로 심어 준다(backend/app/main.py). 개발 서버와 루트 배포에서는 빈 값.
 *
 * **빌드에 굽지 않는다.** 같은 이미지가 `/` 에서도 `/plm/` 에서도 떠야 한다 — 접두어는 배포
 * 설정이지 빌드가 아니다. 자산 주소는 `<base href>` 가, API · 라우터 주소는 이 값이 푼다.
 */
export const PUBLIC_PATH: string =
  typeof document === 'undefined'
    ? ''
    : (document.querySelector('meta[name="app-base"]')?.getAttribute('content') ?? '')

/** 라우터의 basename — 접두어가 없으면 `/`. */
export const ROUTER_BASENAME = PUBLIC_PATH || '/'

/**
 * **접두어 없이 들어왔으면 붙여서 옮긴다.** 메인 서버 없이 앱에 직접(`http://<서버>:8040/`)
 * 들어오면 라우터가 basename 과 안 맞는 주소를 거부해 흰 화면이 된다(실측). 서버에서 돌리면
 * 접두어를 벗겨 넘기는 프록시 뒤에서 무한 반복이 되므로, 브라우저의 주소만 보고 여기서 한다 —
 * 프록시 뒤에서는 주소에 늘 접두어가 있어 아무 일도 안 일어난다.
 */
if (
  PUBLIC_PATH &&
  typeof window !== 'undefined' &&
  window.location.pathname !== PUBLIC_PATH &&
  !window.location.pathname.startsWith(`${PUBLIC_PATH}/`)
) {
  const { pathname, search, hash } = window.location
  window.location.replace(`${PUBLIC_PATH}${pathname}${search}${hash}`)
}
