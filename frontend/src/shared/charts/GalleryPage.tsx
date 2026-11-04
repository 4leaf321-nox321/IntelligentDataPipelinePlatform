/**
 * 차트 보기 — **이 설치가 그릴 수 있는 것 전부, 코드와 함께.**
 *
 * 도메인을 얹는 사람이 「무엇을 쓸 수 있나」 를 알 방법이 없으면 결국 직접
 * 라이브러리를 import 하고, 그 순간 축 색과 범례 규칙이 갈리기 시작한다. 여기가
 * 그 물음에 답하는 자리다 — 골라서 코드를 베껴 가면 된다.
 *
 * **살아 있는 문서다.** 그림이 실제로 그려지므로, 라이브러리가 안 올라오면 이
 * 화면이 먼저 그 사실을 말한다. 캡처해 둔 그림이나 글로 적은 목록은 그것을 못 한다.
 */

import { LazyPlot } from '@/shared/charts/LazyPlot'
import { Chart } from '@/shared/charts/Chart'
import { SERIES_COLORS } from '@/shared/charts/palette'
import { PageHeader } from '@/shared/components/PageHeader'

const BY_MONTH = [
  { name: '1월', 접수: 12, 완료: 8 },
  { name: '2월', 접수: 19, 완료: 14 },
  { name: '3월', 접수: 9, 완료: 9 },
  { name: '4월', 접수: 24, 완료: 17 },
  { name: '5월', 접수: 15, 완료: 15 },
  { name: '6월', 접수: 21, 완료: 12 },
]

const BY_GRADE = [
  { name: 'A', 건수: 24 },
  { name: 'B', 건수: 13 },
  { name: 'C', 건수: 7 },
  { name: '(비어 있음)', 건수: 3 },
]

function Card({
  title,
  code,
  children,
}: {
  title: string
  code: string
  children: React.ReactNode
}) {
  return (
    <section className="space-y-2 rounded-md border p-4">
      <h2 className="text-sm font-medium">{title}</h2>
      {children}
      <pre className="bg-muted text-muted-foreground overflow-x-auto rounded p-2 text-xs">
        {code}
      </pre>
    </section>
  )
}

export default function ChartGalleryPage() {
  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader
        title="차트 보기"
        description="각 플랫폼이 쓸 수 있는 그림들입니다. shared/charts 에서 가져다 쓰고, 화면에서 recharts·plotly 를 직접 부르지 않습니다 — 그러면 축 색과 범례 규칙이 화면마다 갈립니다."
      />

      <div className="mb-6 space-y-2 rounded-md border p-4">
        <h2 className="text-sm font-medium">계열 색</h2>
        <p className="text-muted-foreground text-xs">
          여덟 개를 돌려 쓰고, <strong>ReportArchive 와 같은 색</strong>입니다 — 두 플랫폼의 그림이
          한 보고서에 나란히 실리는 날이 옵니다. 값 이름으로 색을 고정하려면{' '}
          <code>colorFor(name)</code>.
        </p>
        <div className="flex flex-wrap gap-2">
          {SERIES_COLORS.map((color, index) => (
            <span key={color} className="flex items-center gap-1.5 text-xs">
              <span className="size-4 rounded" style={{ background: color }} />
              {index} · {color}
            </span>
          ))}
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <Card
          title="막대 — 기본"
          code={`<Chart kind="bar" data={rows} x="name"\n  series={[{ key: '건수' }]} />`}
        >
          <Chart
            kind="bar"
            data={BY_GRADE}
            x="name"
            series={[{ key: '건수' }]}
            title="등급별 건수"
          />
        </Card>

        <Card
          title="막대 — 여러 계열·쌓기"
          code={`<Chart kind="bar" stacked data={rows} x="name"\n  series={[{ key: '접수' }, { key: '완료' }]} />`}
        >
          <Chart
            kind="bar"
            stacked
            data={BY_MONTH}
            x="name"
            series={[{ key: '접수' }, { key: '완료' }]}
            title="달별 접수와 완료"
          />
        </Card>

        <Card
          title="꺾은선 — 시간에 따른 변화"
          code={`<Chart kind="line" data={rows} x="name"\n  series={[{ key: '접수' }, { key: '완료' }]} />`}
        >
          <Chart
            kind="line"
            data={BY_MONTH}
            x="name"
            series={[{ key: '접수' }, { key: '완료' }]}
            title="달별 추이"
          />
        </Card>

        <Card
          title="영역형 — 쌓아서 전체를 볼 때"
          code={`<Chart kind="area" stacked data={rows} x="name"\n  series={[{ key: '접수' }, { key: '완료' }]} />`}
        >
          <Chart
            kind="area"
            stacked
            data={BY_MONTH}
            x="name"
            series={[{ key: '접수' }, { key: '완료' }]}
            title="달별 누적"
          />
        </Card>

        <Card
          title="원 — 조각이 서넛일 때만"
          code={`<Chart kind="pie" data={rows} x="name"\n  series={[{ key: '건수' }]} />`}
        >
          {/* 사람은 각도보다 길이를 훨씬 잘 읽는다. 조각이 여덟이면 순위를 못 읽는다. */}
          <Chart kind="pie" data={BY_GRADE} x="name" series={[{ key: '건수' }]} title="등급 비율" />
        </Card>

        <Card title="히트맵 — plotly" code={`<LazyPlot data={[{ type: 'heatmap', z, x, y }]} />`}>
          {/* **여기부터는 plotly 다.** 무거워서 이 화면을 열 때 비로소 받는다. */}
          <LazyPlot
            height={240}
            title="두께·속도별 값"
            data={[
              {
                type: 'heatmap',
                z: [
                  [1, 20, 30],
                  [20, 1, 60],
                  [30, 60, 1],
                ],
                x: ['1.0t', '1.2t', '1.6t'],
                y: ['저속', '중속', '고속'],
                colorscale: 'Blues',
              },
            ]}
          />
        </Card>

        <Card title="상자 그림 — plotly" code={`<LazyPlot data={[{ type: 'box', y }]} />`}>
          <LazyPlot
            height={240}
            title="공정별 분포"
            data={[
              { type: 'box', y: [1, 2, 3, 4, 4, 5, 9], name: '공정 A' },
              { type: 'box', y: [2, 3, 3, 4, 6, 7, 8], name: '공정 B' },
            ]}
          />
        </Card>

        <Card
          title="흐름(사케이) — plotly"
          code={`<LazyPlot data={[{ type: 'sankey', node, link }]} />`}
        >
          <LazyPlot
            height={240}
            title="단계별 흐름"
            data={[
              {
                type: 'sankey',
                node: { label: ['접수', '검토', '승인', '반려'], pad: 12 },
                link: { source: [0, 1, 1], target: [1, 2, 3], value: [40, 30, 10] },
              },
            ]}
          />
        </Card>
      </div>

      <p className="text-muted-foreground mt-6 text-xs">
        여기 없는 그림이 필요하면 <code>LazyPlot</code> 에 plotly 의 trace 를 그대로 넘기면
        됩니다(등고선·3차원·트리맵…). 자주 쓰게 되면 그때 <code>Chart</code> 에 종류를 더합니다 —
        화면마다 plotly 를 직접 부르는 것이 늘어나는 것이 가장 나쁩니다.
      </p>
    </div>
  )
}
