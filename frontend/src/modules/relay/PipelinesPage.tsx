/**
 * 파이프라인 — 지도의 가운데 열을 표로. 원천 + API + 대상 + 상태(대조의 어긋남 포함) + 마지막 적용.
 *
 * 지도와 같은 응답(`/relay/map`)을 읽는다 — 목록이 따로 계산하면 지도와 다른 말을 하는 날이 온다.
 */

import { useMemo } from 'react'
import { Link } from 'react-router-dom'

import { pipelineDetailOf, relayApi } from '@/modules/relay/api'
import { DriftBadge } from '@/modules/relay/DriftBadge'
import { buildGraph, pipelineRows } from '@/modules/relay/graph'
import { EmptyState } from '@/shared/components/EmptyState'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { PageHeader } from '@/shared/components/PageHeader'
import { StatusBadge } from '@/shared/components/StatusBadge'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/shared/components/ui/table'
import { useResource } from '@/shared/hooks/useResource'
import { shownDateTime } from '@/shared/lib/datetime'

const NO_PIPELINES_HINT =
  '정의 파일(relay-pipeline/1)은 서버의 파이프라인 폴더(RELAY_PIPELINES_DIR)에 둡니다 — 넣으면 다음 읽기부터 보입니다. AI(MCP)가 정의 판 초안을 지어도 「초안(꺼짐)」 으로 보입니다.'

export default function PipelinesPage() {
  const map = useResource(() => relayApi.map(), [])
  const rows = useMemo(() => (map.data ? pipelineRows(buildGraph(map.data)) : null), [map.data])

  return (
    <div className="space-y-4">
      <PageHeader
        title="파이프라인"
        description="원천의 API 하나를 레시피로 바꿔 대상 SP 에 보내는 단위입니다. 눌러서 칸 대응 표 · 실행 이력을 보고, 지금 실행합니다."
      />
      <ErrorNotice error={map.error} />
      {map.loading && !map.data && <p className="text-muted-foreground text-sm">읽는 중…</p>}
      {rows && rows.length === 0 && (
        <EmptyState title="파이프라인이 없습니다" hint={NO_PIPELINES_HINT} />
      )}
      {rows && rows.length > 0 && (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>파이프라인</TableHead>
              <TableHead>원천 시스템</TableHead>
              <TableHead>API</TableHead>
              <TableHead>대상</TableHead>
              <TableHead>상태</TableHead>
              <TableHead>마지막 적용</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map(({ node, systems, endpoints, targets }) => (
              <TableRow key={node.id}>
                <TableCell>
                  <Link to={node.link || '#'} className="font-mono font-medium hover:underline">
                    {node.label}
                  </Link>
                  <p className="text-muted-foreground text-xs">{node.sub}</p>
                </TableCell>
                <TableCell>{systems.map((one) => one.label).join(', ') || '—'}</TableCell>
                <TableCell>
                  {endpoints.map((one) => (
                    <div key={one.id}>
                      {one.label}
                      <p className="text-muted-foreground font-mono text-xs">{one.sub}</p>
                    </div>
                  ))}
                </TableCell>
                <TableCell>{targets.map((one) => one.label).join(', ') || '—'}</TableCell>
                <TableCell>
                  <span className="flex flex-wrap items-center gap-1.5">
                    <StatusBadge kind="relay_pipeline" value={node.status} />
                    <DriftBadge node={node} />
                  </span>
                </TableCell>
                <TableCell className="whitespace-nowrap">
                  {shownDateTime(pipelineDetailOf(node).applied_at)}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  )
}
