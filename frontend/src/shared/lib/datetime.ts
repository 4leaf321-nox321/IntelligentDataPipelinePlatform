/**
 * 날짜·시각 표시 — **절대 시각으로 적는다.**
 *
 * "3일 전" 은 읽는 시점에 따라 달라져서, 화면을 캡처해 주고받는 순간 뜻을 잃는다.
 */

/** 2026-09-08. 목록의 날짜 칸. */
export function shownDate(raw: string | null | undefined): string {
  if (!raw) return '—'
  const when = new Date(raw)
  return Number.isNaN(when.getTime()) ? raw : when.toLocaleDateString('ko-KR')
}

const MOMENT =
  /^(\d{4}-\d{2}-\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(?:[.,]\d+)?)?\s*(Z|[+-]\d{2}(?::?\d{2})?)?$/i

const two = (value: number) => String(value).padStart(2, '0')

/**
 * 저장된 날짜·시각 → `<input type="datetime-local">` 의 값(`YYYY-MM-DDTHH:mm[:ss]`).
 *
 * 서버는 파이썬의 `fromisoformat` 이 읽는 것을 다 받는다 — 가져오기로 들어온 값에는 공백
 * (`2026-10-08 09:30`) · 오프셋(`…+09:00`) · 소수 초가 붙어 있다. 그 모양을 그대로 넣으면
 * 입력 칸이 **빈칸으로 보였고**, 빈 줄 알고 손대면 그 값이 지워졌다(2026-10-08). 오프셋이 있으면
 * 이 브라우저의 시각으로 옮긴다. **못 읽으면 null** — 부르는 쪽이 원값을 보이고 보존한다.
 */
export function toDatetimeLocal(raw: string | null | undefined): string | null {
  if (!raw) return ''
  const found = MOMENT.exec(raw.trim())
  if (!found) return null
  const [, day, hour, minute, second, offset] = found
  if (!offset) return `${day}T${hour}:${minute}${second ? `:${second}` : ''}`
  // `+0900` · `+09` 도 온다 — 브라우저의 Date 는 `+09:00` 만 확실히 읽는다.
  let zone = offset.toUpperCase()
  if (/^[+-]\d{4}$/.test(zone)) zone = `${zone.slice(0, 3)}:${zone.slice(3)}`
  else if (/^[+-]\d{2}$/.test(zone)) zone = `${zone}:00`
  const when = new Date(`${day}T${hour}:${minute}:${second ?? '00'}${zone}`)
  if (Number.isNaN(when.getTime())) return null
  const local = `${when.getFullYear()}-${two(when.getMonth() + 1)}-${two(when.getDate())}`
  const clock = `${two(when.getHours())}:${two(when.getMinutes())}`
  return `${local}T${clock}${second ? `:${two(when.getSeconds())}` : ''}`
}

/** 2026-09-08 14:32. 로그처럼 시각이 필요한 자리에만. */
export function shownDateTime(raw: string | null | undefined): string {
  if (!raw) return '—'
  const when = new Date(raw)
  if (Number.isNaN(when.getTime())) return raw
  return `${when.toLocaleDateString('ko-KR')} ${when.toLocaleTimeString('ko-KR', {
    hour: '2-digit',
    minute: '2-digit',
  })}`
}
