/**
 * 차트의 색과 눈금 — **한 곳에서 정한다.**
 *
 * 화면마다 색을 고르면 같은 부서가 이 그림에서는 파랑이고 저 그림에서는 빨강이
 * 된다. 그러면 색이 아무 뜻도 못 갖고, 두 그림을 나란히 놓는 일이 불가능해진다.
 * `StatusBadge` 가 상태 색을 한 곳에 둔 것과 같은 이유다.
 *
 * ## 색은 ReportArchive 와 같은 여덟이다
 *
 * 두 플랫폼의 그림이 나란히 놓이는 날이 온다(보고서에 이 플랫폼의 표가 실린다).
 * 그때 같은 순서의 계열이 다른 색이면 사람은 그것을 다른 것으로 읽는다.
 *
 * ## 실측에서 나온 상한들
 *
 * 아래 세 숫자는 ReportArchive 가 실제로 데인 자리에서 나왔다. 17계열 × 1,144점
 * 차트가 **파란 얼룩** 한 덩어리가 되고 내보내기에 60.8초가 걸렸는데, 원인이
 * 선이 아니라 **점마다 찍힌 마커 19,448개**였다. 그리고 범례가 3줄로 접히며
 * 도표 위를 덮었다.
 *
 * 그래서 이 셋은 **그리는 사람이 몰라도 알아서 걸려야 한다.** 「점을 끄세요」 라고
 * 안내해서 될 문제가 아니다 — 안내는 읽히지 않고, 느려진 이유는 화면 어디에도
 * 안 적힌다.
 */

/** 계열 색. 순서가 곧 계열 번호다 — 골라서 셋만 그려도 색이 그대로여야 비교가 된다. */
export const SERIES_COLORS = [
  '#2563eb',
  '#dc2626',
  '#16a34a',
  '#f59e0b',
  '#7c3aed',
  '#0891b2',
  '#db2777',
  '#475569',
] as const

/** 계열 하나에 이 점수를 넘으면 마커를 안 찍는다. 그 위로는 점이 서로 붙어 분포를
 *  가리기만 하고, 표시와 내보내기를 함께 느리게 만든다. */
export const DOT_MAX_POINTS = 60

/** 이 수를 넘는 계열은 색이 겹쳐 못 읽는다. **줄이지는 않고 말만 한다** — 이미
 *  그렇게 그려 둔 것을 마음대로 바꾸지 않는다. */
export const SERIES_CROWDED = 8

/** 막대가 이보다 많으면 x축 이름을 비스듬히 눕힌다. 가로로 두면 서로 겹쳐 뭉갠다. */
export const TILT_LABELS_OVER = 8

export function colorAt(index: number): string {
  return SERIES_COLORS[
    ((index % SERIES_COLORS.length) + SERIES_COLORS.length) % SERIES_COLORS.length
  ]
}

/**
 * 이름으로 색을 고정한다 — 같은 값이 어느 그림에서나 같은 색.
 *
 * 자리(index)로만 주면 「A 등급」 이 이 차트에서는 첫째라 파랑, 거른 뒤에는 둘째라
 * 빨강이 된다. 사람은 그 변화를 데이터가 바뀐 것으로 읽는다.
 */
export function colorFor(name: string): string {
  let hash = 0
  for (let index = 0; index < name.length; index += 1) {
    hash = (hash * 31 + name.charCodeAt(index)) | 0
  }
  return colorAt(Math.abs(hash))
}

/** recharts 의 `dot` prop 에 그대로. false 면 안 찍는다. */
export function dotConfig(pointCount: number, radius = 3): false | { r: number } {
  return pointCount > DOT_MAX_POINTS ? false : { r: radius }
}

/**
 * 범례에 내줄 높이.
 *
 * 고정으로 두면 계열이 많을 때 범례가 도표 위로 흘러넘쳐 y축 눈금을 가린다.
 * 한 줄에 몇 개가 들어가는지 어림하고 필요한 줄만큼 주되, **세 줄까지만** — 그
 * 위로는 도표가 남지 않는다.
 */
export function legendHeightFor(seriesCount: number, baseHeight = 28, perRow = 6): number {
  const count = Number.isFinite(seriesCount) && seriesCount > 0 ? seriesCount : 1
  return baseHeight * Math.min(3, Math.ceil(count / Math.max(1, perRow)))
}

/**
 * 눈금·격자·글자 색은 **테마 변수에서** 가져온다.
 *
 * 고정 회색으로 박으면 어두운 테마에서 격자가 안 보이거나 배경과 같아진다. 그때
 * 그림은 「깨진 것」 이 아니라 **빈 것처럼** 보이고, 그것이 더 나쁘다.
 */
export const AXIS_COLOR = 'var(--muted-foreground)'
export const GRID_COLOR = 'var(--border)'

/** 눈금에 쓰는 숫자 — 자릿점을 찍고 소수는 둘째 자리까지. */
export function shownNumber(value: number): string {
  if (!Number.isFinite(value)) return ''
  return Number.isInteger(value) ? value.toLocaleString() : value.toFixed(2)
}

/**
 * 서열 색 — **한 색의 밝기 단계.** 순서가 있는 값(수준 · 등급 · 단계)에 쓴다.
 *
 * 계열 색(`SERIES_COLORS`)을 서열에 쓰면 안 된다. 그것은 「다른 것」 을 가리키는 색이라,
 * 「낮음 → 높음」 에 쓰면 사람이 빨강을 경고로, 초록을 좋음으로 읽는다 — 순서가 아니라
 * 뜻을 읽어 버린다.
 *
 * 다섯 단계이고 어두운 화면에서는 **높은 값이 밝다**(같은 램프를 뒤집지 않고 그 화면의
 * 표면에서 다시 고른 것이다). 단계 사이 밝기 차와 표면 대비를 검증기로 확인했다 —
 * 라이트·다크 모두 통과(2026-09-25).
 */
export const LEVEL_COLORS_LIGHT = ['#86b6ef', '#5598e7', '#2a78d6', '#1c5cab', '#104281'] as const
export const LEVEL_COLORS_DARK = ['#184f95', '#256abf', '#3987e5', '#6da7ec', '#9ec5f4'] as const

/** 미평가 — **색을 주지 않는다.** 값이 없는 것은 낮은 값이 아니다. */
export const LEVEL_NONE_LIGHT = '#eef1f5'
export const LEVEL_NONE_DARK = '#2a2a28'

/**
 * `index` 번째 수준의 색(전체 `total` 단계 중). 단계가 다섯보다 적으면 램프에서 고르게
 * 뽑고, 많으면 위쪽으로 몰아 마지막 단계가 가장 진하다.
 */
export function levelColor(index: number, total: number, dark = false): string {
  const ramp = dark ? LEVEL_COLORS_DARK : LEVEL_COLORS_LIGHT
  if (index < 0) return dark ? LEVEL_NONE_DARK : LEVEL_NONE_LIGHT
  if (total <= 1) return ramp[ramp.length - 1]
  const slot = Math.round((index / (total - 1)) * (ramp.length - 1))
  return ramp[Math.min(ramp.length - 1, Math.max(0, slot))]
}
