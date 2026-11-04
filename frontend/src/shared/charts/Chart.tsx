/**
 * 흔한 차트 넷 — 막대형·꺾은선형·영역형·원형. **한 컴포넌트, 한 데이터 모양.**
 *
 * 각 플랫폼이 recharts 를 직접 쓰면 축 색·범례 높이·빈 데이터 처리·점 마커
 * 규칙을 저마다 다시 정하게 되고, 그 결정들은 서로 조금씩 어긋난다. 여기 한 번
 * 두면 도메인은 **무엇을 그릴지만** 정한다.
 *
 * ```tsx
 * <Chart
 *   kind="bar"
 *   data={[{ name: 'A', 건수: 12 }, { name: 'B', 건수: 5 }]}
 *   x="name"
 *   series={[{ key: '건수' }]}
 * />
 * ```
 *
 * ## 여기 없는 것은 `LazyPlot`
 *
 * 히트맵·박스·사케이·3차원처럼 무거운 것은 plotly 가 그린다(`LazyPlot`). 그것은
 * 번들이 크므로 **쓰는 화면에서만 불러온다.** 이 파일은 가볍게 유지한다.
 *
 * ## 빈 데이터는 빈 그림이 아니다
 *
 * 데이터가 없을 때 축만 덩그러니 그리면 사람은 그것을 「0 이 여럿」 으로 읽는다.
 * 여기서는 **이유를 적은 한 줄**로 바꾼다 — 그 자리에서 무엇을 해야 할지가 갈린다.
 */

import { useMemo } from 'react'
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import {
  AXIS_COLOR,
  GRID_COLOR,
  TILT_LABELS_OVER,
  colorAt,
  colorFor,
  dotConfig,
  legendHeightFor,
  shownNumber,
} from '@/shared/charts/palette'

export type ChartKind = 'bar' | 'line' | 'area' | 'pie'

export interface ChartSeries {
  /** `data` 행에서 값을 꺼낼 키. */
  key: string
  /** 범례에 쓸 이름. 없으면 key 그대로. */
  label?: string
  /** 이 계열만 다른 색으로. 없으면 순서대로 팔레트에서. */
  color?: string
}

export interface ChartProps {
  kind: ChartKind
  data: Record<string, unknown>[]
  /** 가로축(원그래프면 조각 이름)이 될 키. */
  x: string
  series: ChartSeries[]
  height?: number
  /** 막대를 쌓을지. 여럿을 나란히 두면 「전체가 얼마인지」 를 못 읽는 물음이 있다. */
  stacked?: boolean
  /**
   * 조각·막대를 누르면 **그 행 그대로.** 이름만 넘기면 부르는 쪽이 이름으로
   * 원래 값을 되찾아야 하는데, 이름은 겹칠 수 있다(부서 둘이 같은 이름). 누를 수
   * 없는 축이면 안 넘긴다.
   */
  onPick?: (row: Record<string, unknown>) => void
  /** 데이터가 없을 때 적을 말. **「없음」 만 적지 않는다** — 왜 없는지가 할 일을 가른다. */
  emptyText?: string
  /** 그림이 무엇을 말하는지 한 줄. 화면 낭독기가 읽는다. */
  title?: string
  className?: string
}

const MARGIN = { top: 8, right: 12, bottom: 4, left: 4 }

/** 눌린 것에서 **원래 행**을 꺼낸다 — 막대는 `payload` 안에, 조각은 그 자체가 행이다. */
function rowOf(entry: unknown): Record<string, unknown> | null {
  if (!entry || typeof entry !== 'object') return null
  const wrapped = (entry as { payload?: unknown }).payload
  if (wrapped && typeof wrapped === 'object') {
    // 조각은 한 겹 더 싸일 때가 있다(payload.payload).
    const inner = (wrapped as { payload?: unknown }).payload
    return (inner && typeof inner === 'object' ? inner : wrapped) as Record<string, unknown>
  }
  return entry as Record<string, unknown>
}

export function Chart({
  kind,
  data,
  x,
  series,
  height = 240,
  stacked = false,
  onPick,
  emptyText = '그릴 것이 없습니다.',
  title,
  className,
}: ChartProps) {
  const colors = useMemo(() => series.map((one, index) => one.color ?? colorAt(index)), [series])
  const legendHeight = legendHeightFor(series.length)
  // 계열이 하나면 범례가 같은 말을 한 번 더 하는 것이다 — 그 자리는 도표에 준다.
  const showLegend = series.length > 1
  const tilt = data.length > TILT_LABELS_OVER

  if (data.length === 0 || series.length === 0) {
    return (
      <p className={`text-muted-foreground py-8 text-center text-sm ${className ?? ''}`}>
        {emptyText}
      </p>
    )
  }

  const axis = { stroke: AXIS_COLOR, fontSize: 12 }
  const tooltip = (
    <Tooltip
      formatter={(value: unknown) =>
        typeof value === 'number' ? shownNumber(value) : String(value ?? '')
      }
      contentStyle={{
        background: 'var(--popover)',
        border: '1px solid var(--border)',
        borderRadius: 6,
        color: 'var(--popover-foreground)',
        fontSize: 12,
      }}
    />
  )

  return (
    <div
      className={className}
      style={{ height }}
      role="img"
      aria-label={title ?? `${series.map((one) => one.label ?? one.key).join(', ')} 차트`}
    >
      <ResponsiveContainer width="100%" height="100%">
        {kind === 'pie' ? (
          <PieChart margin={MARGIN}>
            <Pie
              data={data}
              dataKey={series[0].key}
              nameKey={x}
              // 가운데를 비운다(도넛). 꽉 찬 원은 조각 크기를 각도로만 비교하게 하는데,
              // 사람은 각도보다 길이를 훨씬 잘 읽는다 — 그래서 기본은 막대다.
              innerRadius="45%"
              outerRadius="80%"
              onClick={(entry: unknown) => {
                const row = rowOf(entry)
                if (onPick && row) onPick(row)
              }}
            >
              {data.map((row) => (
                <Cell key={String(row[x])} fill={colorFor(String(row[x]))} />
              ))}
            </Pie>
            {tooltip}
            <Legend height={legendHeight} wrapperStyle={{ fontSize: 12 }} />
          </PieChart>
        ) : kind === 'line' ? (
          <LineChart data={data} margin={MARGIN}>
            <CartesianGrid strokeDasharray="3 3" stroke={GRID_COLOR} />
            <XAxis
              dataKey={x}
              {...axis}
              angle={tilt ? -30 : 0}
              textAnchor={tilt ? 'end' : 'middle'}
              height={tilt ? 56 : 24}
            />
            <YAxis {...axis} tickFormatter={shownNumber} />
            {tooltip}
            {showLegend && <Legend height={legendHeight} wrapperStyle={{ fontSize: 12 }} />}
            {series.map((one, index) => (
              <Line
                key={one.key}
                type="monotone"
                dataKey={one.key}
                name={one.label ?? one.key}
                stroke={colors[index]}
                strokeWidth={2}
                // **점은 알아서 꺼진다.** 많아지면 선이 아니라 점이 그림을 뭉갠다.
                dot={dotConfig(data.length)}
              />
            ))}
          </LineChart>
        ) : kind === 'area' ? (
          <AreaChart data={data} margin={MARGIN}>
            <CartesianGrid strokeDasharray="3 3" stroke={GRID_COLOR} />
            <XAxis
              dataKey={x}
              {...axis}
              angle={tilt ? -30 : 0}
              textAnchor={tilt ? 'end' : 'middle'}
              height={tilt ? 56 : 24}
            />
            <YAxis {...axis} tickFormatter={shownNumber} />
            {tooltip}
            {showLegend && <Legend height={legendHeight} wrapperStyle={{ fontSize: 12 }} />}
            {series.map((one, index) => (
              <Area
                key={one.key}
                type="monotone"
                dataKey={one.key}
                name={one.label ?? one.key}
                stroke={colors[index]}
                fill={colors[index]}
                fillOpacity={0.2}
                stackId={stacked ? 'one' : undefined}
                dot={dotConfig(data.length)}
              />
            ))}
          </AreaChart>
        ) : (
          <BarChart data={data} margin={MARGIN}>
            <CartesianGrid strokeDasharray="3 3" stroke={GRID_COLOR} vertical={false} />
            <XAxis
              dataKey={x}
              {...axis}
              angle={tilt ? -30 : 0}
              textAnchor={tilt ? 'end' : 'middle'}
              height={tilt ? 56 : 24}
            />
            <YAxis {...axis} tickFormatter={shownNumber} />
            {tooltip}
            {showLegend && <Legend height={legendHeight} wrapperStyle={{ fontSize: 12 }} />}
            {series.map((one, index) => (
              <Bar
                key={one.key}
                dataKey={one.key}
                name={one.label ?? one.key}
                fill={colors[index]}
                stackId={stacked ? 'one' : undefined}
                radius={[2, 2, 0, 0]}
                cursor={onPick ? 'pointer' : undefined}
                onClick={(entry: unknown) => {
                  const row = rowOf(entry)
                  if (onPick && row) onPick(row)
                }}
              />
            ))}
          </BarChart>
        )}
      </ResponsiveContainer>
    </div>
  )
}
