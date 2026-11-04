/**
 * 원천 시스템 — PLM · MES 같은 레거시와 그 API, API 마다 어느 파이프라인이 읽나.
 *
 * 비밀(토큰 · 계정)은 여기 안 나온다 — 서버가 정의를 비밀 없이 읽어 준다(`relay_core.describe`).
 */

import { useMemo } from 'react'
import { Link } from 'react-router-dom'

import { relayApi } from '@/modules/relay/api'
import { buildGraph, systemRows } from '@/modules/relay/graph'
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

export default function SourcesPage() {
  const map = useResource(() => relayApi.map(), [])
  const rows = useMemo(() => (map.data ? systemRows(buildGraph(map.data)) : null), [map.data])

  return (
    <div className="space-y-4">
      <PageHeader
        title="원천 시스템"
        description="데이터를 내주는 레거시 시스템과 그 API 입니다. 파이프라인 정의에서 읽으며, 인증 비밀은 보이지 않습니다."
      />
      <ErrorNotice error={map.error} />
      {map.loading && !map.data && <p className="text-muted-foreground text-sm">읽는 중…</p>}
      {rows && rows.length === 0 && (
        <EmptyState
          title="원천 시스템이 없습니다"
          hint="원천은 파이프라인 정의의 system · adapter 에서 나옵니다 — 서버의 파이프라인 폴더(RELAY_PIPELINES_DIR)에 정의를 두면 보입니다."
        />
      )}
      {rows && rows.length > 0 && (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>원천 시스템</TableHead>
              <TableHead>API</TableHead>
              <TableHead>파이프라인</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.flatMap(({ node, endpoints }) =>
              (endpoints.length ? endpoints : [null]).map((endpoint, index) => (
                <TableRow key={`${node.id}:${endpoint?.node.id ?? '-'}`}>
                  {index === 0 && (
                    <TableCell rowSpan={Math.max(1, endpoints.length)} className="align-top">
                      <p className="font-medium">{node.label}</p>
                      <p className="text-muted-foreground font-mono text-xs">{node.sub}</p>
                    </TableCell>
                  )}
                  <TableCell className="align-top">
                    {endpoint ? (
                      <>
                        {endpoint.node.label}
                        <p className="text-muted-foreground font-mono text-xs">
                          {endpoint.node.sub}
                        </p>
                      </>
                    ) : (
                      '—'
                    )}
                  </TableCell>
                  <TableCell className="align-top">
                    <ul className="space-y-1">
                      {(endpoint?.pipelines ?? []).map((one) => (
                        <li key={one.id} className="flex items-center gap-2">
                          <Link to={one.link || '#'} className="font-mono hover:underline">
                            {one.label}
                          </Link>
                          <StatusBadge kind="relay_pipeline" value={one.status} />
                        </li>
                      ))}
                    </ul>
                  </TableCell>
                </TableRow>
              )),
            )}
          </TableBody>
        </Table>
      )}
    </div>
  )
}
