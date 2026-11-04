/**
 * 복사 — **http 에서도 된다.** `navigator.clipboard` 는 보안 컨텍스트(https · localhost)에만 있어,
 * 메인 서버 없이 IP 로 직접 보는 동안(http)은 없다. 그때는 옛 방식(숨은 textarea + execCommand)으로.
 */
export async function copyText(text: string): Promise<void> {
  if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text)
      return
    } catch {
      // 아래 폴백으로
    }
  }
  const area = document.createElement('textarea')
  area.value = text
  area.setAttribute('readonly', '')
  area.style.position = 'fixed'
  area.style.opacity = '0'
  document.body.appendChild(area)
  area.select()
  try {
    if (!document.execCommand('copy')) throw new Error('copy failed')
  } finally {
    document.body.removeChild(area)
  }
}
