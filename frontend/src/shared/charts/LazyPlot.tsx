/**
 * 무거운 그림 — 히트맵·박스·사케이·등고선·3차원. **plotly 가 그린다.**
 *
 * recharts 로는 못 그리는 것들이다. 대신 plotly 는 압축해도 1MB 를 훌쩍 넘으므로
 * **정적으로 import 하지 않는다** — 그러면 이 그림을 한 번도 안 여는 사람까지
 * 첫 화면에서 그만큼을 받는다. 여기서만 `import()` 로 불러 별도 덩어리에 둔다.
 *
 * ## 불러오는 동안에도 자리를 잡아 둔다
 *
 * 다 받은 뒤에 갑자기 높이가 생기면 그 아래 있던 것이 밀려 내려가고, 마침 그때
 * 누르던 사람은 엉뚱한 것을 누른다. 그래서 받는 동안에도 같은 높이를 차지한다.
 *
 * ## 못 받으면 그렇다고 말한다
 *
 * 폐쇄망에서 자산 하나가 빠지면 `import()` 가 실패한다. 그때 조용히 빈 칸으로
 * 두면 「데이터가 없다」 로 읽히는데, 실제로는 그림 도구를 못 받은 것이다.
 *
 * ```tsx
 * <LazyPlot
 *   data={[{ type: 'heatmap', z: [[1, 2], [3, 4]] }]}
 *   layout={{ title: '두께별 응력' }}
 *   height={320}
 * />
 * ```
 */

import { useEffect, useRef, useState } from 'react'
import { Loader2 } from 'lucide-react'

import { AXIS_COLOR, SERIES_COLORS } from '@/shared/charts/palette'

/** plotly 의 trace/layout 은 종류마다 모양이 달라 여기서 좁히지 않는다 — 좁히면
 *  새 종류를 그릴 때마다 이 파일을 고쳐야 한다. */
export type PlotTrace = Record<string, unknown>
export type PlotLayout = Record<string, unknown>

interface Props {
  data: PlotTrace[]
  layout?: PlotLayout
  height?: number
  /** 도구 막대를 보일지. 기본은 숨김 — 보고서에 실릴 그림에 버튼 줄이 함께 찍힌다. */
  toolbar?: boolean
  className?: string
  title?: string
}

/** 어느 그림에나 같게 적용할 것 — 색·글꼴·여백. 한 곳에서 정한다. */
function baseLayout(height: number): PlotLayout {
  return {
    height,
    colorway: [...SERIES_COLORS],
    margin: { t: 24, r: 16, b: 40, l: 48 },
    // 배경을 비운다 — 흰색으로 박으면 어두운 테마에서 그림만 하얗게 뜬다.
    paper_bgcolor: 'rgba(0,0,0,0)',
    plot_bgcolor: 'rgba(0,0,0,0)',
    font: { size: 12, color: AXIS_COLOR },
    showlegend: true,
    legend: { orientation: 'h', y: -0.2 },
  }
}

export function LazyPlot({ data, layout, height = 320, toolbar = false, className, title }: Props) {
  const box = useRef<HTMLDivElement>(null)
  const [failed, setFailed] = useState(false)
  const [ready, setReady] = useState(false)

  useEffect(() => {
    let cancelled = false
    const node = box.current
    if (!node) return

    import('plotly.js-dist-min')
      .then((plotly) => {
        if (cancelled || !box.current) return
        setReady(true)
        // plotly 의 trace·layout 은 종류마다 칸이 달라(히트맵의 z, 사케이의 link…)
        // 좁은 형을 강요하면 새 그림을 그릴 때마다 이 파일을 고쳐야 한다. 형은
        // 라이브러리가 선언한 것에서 **끌어다 쓰고**, 값은 부르는 쪽이 책임진다.
        type Args = Parameters<typeof plotly.default.react>
        const merged = { ...baseLayout(height), ...layout } as Args[2]
        return plotly.default.react(box.current, data as Args[1], merged, {
          displayModeBar: toolbar,
          responsive: true,
        })
      })
      .catch(() => {
        if (!cancelled) setFailed(true)
      })

    return () => {
      cancelled = true
      // 지울 때 plotly 가 붙여 둔 것을 거둔다. 안 하면 화면을 오갈수록 DOM 과
      // 리스너가 쌓인다 — 느려지는데 이유가 어디에도 안 뜬다.
      if (node) {
        void import('plotly.js-dist-min').then((plotly) => plotly.default.purge(node))
      }
    }
    // data·layout 은 매 렌더 새 객체일 수 있어 **내용**으로 비교한다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(data), JSON.stringify(layout), height, toolbar])

  if (failed) {
    return (
      <p className="text-muted-foreground py-8 text-center text-sm">
        그림 도구를 불러오지 못했습니다. 새로고침해 보고, 그래도 안 되면 관리자에게 알려 주세요 —
        데이터가 없는 것은 아닙니다.
      </p>
    )
  }

  return (
    <div className={className} style={{ height }} role="img" aria-label={title ?? '차트'}>
      {!ready && (
        <div className="text-muted-foreground flex h-full items-center justify-center gap-2 text-sm">
          <Loader2 className="size-4 animate-spin" />
          그림 준비 중…
        </div>
      )}
      <div ref={box} style={{ height: '100%', display: ready ? 'block' : 'none' }} />
    </div>
  )
}
