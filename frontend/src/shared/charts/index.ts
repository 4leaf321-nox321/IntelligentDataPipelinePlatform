/**
 * 차트 — **각 플랫폼이 여기서 가져다 쓴다.**
 *
 *     Chart      막대형·꺾은선형·영역형·원형. 흔한 넷은 여기서 끝난다(recharts)
 *     LazyPlot   히트맵·박스·사케이·등고선·3차원 — 무거워서 쓸 때만 받는다(plotly)
 *     palette    색·눈금·실측에서 나온 상한들
 *
 * recharts 나 plotly 를 화면에서 **직접 import 하지 않는다.** 그러면 축 색·범례
 * 높이·빈 데이터 처리를 화면마다 다시 정하게 되고, 그 결정들은 서로 조금씩
 * 어긋난다 — 같은 부서가 이 그림에서는 파랑, 저 그림에서는 빨강이 된다.
 */

export { Chart } from '@/shared/charts/Chart'
export type { ChartKind, ChartProps, ChartSeries } from '@/shared/charts/Chart'
export { LazyPlot } from '@/shared/charts/LazyPlot'
export type { PlotLayout, PlotTrace } from '@/shared/charts/LazyPlot'
export {
  AXIS_COLOR,
  DOT_MAX_POINTS,
  GRID_COLOR,
  LEVEL_COLORS_DARK,
  LEVEL_COLORS_LIGHT,
  LEVEL_NONE_DARK,
  LEVEL_NONE_LIGHT,
  SERIES_COLORS,
  SERIES_CROWDED,
  TILT_LABELS_OVER,
  colorAt,
  colorFor,
  dotConfig,
  legendHeightFor,
  levelColor,
  shownNumber,
} from '@/shared/charts/palette'
