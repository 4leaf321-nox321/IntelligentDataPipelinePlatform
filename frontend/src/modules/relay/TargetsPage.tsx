/**
 * 대상 인스턴스 — 받는 Standard Platform 마다 지금 닿나 · 판 · 받는 타입 · 그 너머 누가 가져가나.
 *
 * 「그 너머」 는 대상 SP 의 코어 현황이 말하는 수신자다 — 이 플랫폼이 보내지 않지만, 대상에서
 * 무엇을 바꾸면 누가 영향을 받는지 여기서 본다.
 */

import { useMemo } from 'react'
import { Link } from 'react-router-dom'

import { relayApi } from '@/modules/relay/api'
import { buildGraph, targetRows } from '@/modules/relay/graph'
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

export default function TargetsPage() {
  const map = useResource(() => relayApi.map(), [])
  const rows = useMemo(() => (map.data ? targetRows(buildGraph(map.data)) : null), [map.data])

  return (
    <div className="space-y-4">
      <PageHeader
        title="대상 인스턴스"
        description="중계 데이터를 수신하는 Standard Platform입니다. 연결 상태, 버전, 수신하는 타입과 그 데이터를 가져가는 후속 수신처를 표시합니다."
      />
      <ErrorNotice error={map.error} />
      {map.loading && !map.data && (
        <p className="text-muted-foreground text-sm">
          불러오는 중… 대상마다 연결 상태를 확인하므로 몇 초 걸릴 수 있습니다.
        </p>
      )}
      {rows && rows.length === 0 && (
        <EmptyState
          title="대상 인스턴스 없음"
          hint="대상은 파이프라인 정의의 target(server, token_env)에서 가져옵니다. 정의에 대상을 입력하면 표시됩니다."
        />
      )}
      {rows && rows.length > 0 && (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>대상</TableHead>
              <TableHead>상태</TableHead>
              <TableHead>전송 파이프라인</TableHead>
              <TableHead>타입</TableHead>
              <TableHead>후속 수신처</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map(({ node, pipelines, types, downstream }) => (
              <TableRow key={node.id}>
                <TableCell className="align-top">
                  <p className="font-medium">{node.label}</p>
                  <p className="text-muted-foreground font-mono text-xs">{node.sub}</p>
                </TableCell>
                <TableCell className="align-top">
                  <StatusBadge kind="relay_target" value={node.status || 'unreachable'} />
                </TableCell>
                <TableCell className="align-top">
                  <ul className="space-y-0.5">
                    {pipelines.map((one) => (
                      <li key={one.id}>
                        <Link to={one.link || '#'} className="font-mono hover:underline">
                          {one.label}
                        </Link>
                      </li>
                    ))}
                  </ul>
                </TableCell>
                <TableCell className="align-top font-mono text-xs whitespace-normal">
                  {types.map((one) => one.label).join(', ') || '—'}
                </TableCell>
                <TableCell className="align-top whitespace-normal">
                  {downstream.length === 0 ? (
                    <span className="text-muted-foreground text-xs">
                      {node.status === 'ok' ? '없음' : '확인 불가'}
                    </span>
                  ) : (
                    <ul className="space-y-0.5">
                      {downstream.map((one) => (
                        <li key={one.id}>
                          {one.label}
                          <span className="text-muted-foreground ml-1 text-xs">{one.sub}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  )
}
