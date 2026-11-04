/**
 * 변경 이력.
 *
 * **생성·수정·삭제가 없다.** 고칠 수 있으면 감사가 아니다 — 쓰는 길은
 * 서버의 도메인 코드 하나뿐이다.
 *
 * 여기 남는 것은 **되돌릴 수 없거나 권한이 실린 일**만이다. 값 하나 고친 것까지
 * 남기면 그 안에서 정작 찾을 것을 못 찾는다.
 */

import { useState } from 'react'

import { api } from '@/shared/api/client'
import type { Page } from '@/shared/api/paging'
import type { AuditEntry } from '@/shared/api/types'
import { EmptyState } from '@/shared/components/EmptyState'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { PageHeader } from '@/shared/components/PageHeader'
import { Pagination } from '@/shared/components/Pagination'
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

/** 값 하나를 한 줄로 — 객체 · 목록은 JSON 으로(`[object Object]` 가 되지 않게). */
function shown(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

/** 고치기 기록인가 — `{before, after}` 꼴. 감사 기록이 다 이 꼴은 아니다. */
function isDiff(value: unknown): value is { before?: unknown; after?: unknown } {
  return (
    typeof value === 'object' && value !== null && ('before' in value || 'after' in value)
  )
}

function shownChanges(changes: AuditEntry['changes']): string {
  // `_` 로 시작하는 키는 기록에 붙인 표식(묶음 번호 등)이다 — 칸이 아니다.
  // **모든 기록이 `{before, after}` 꼴은 아니다** — 수 · 목록 · null 도 온다(홈 게시의
  // `split: null` 에서 `null.before` 로 화면 전체가 죽었다, 2026-10-08).
  const parts = Object.entries(changes ?? {})
    .filter(([key]) => !key.startsWith('_'))
    .map(([key, value]) =>
      isDiff(value)
        ? `${key}: ${shown(value.before)} -> ${shown(value.after)}`
        : `${key}: ${shown(value)}`,
    )
  return parts.join(', ') || '—'
}

/** 서버가 강제하는 상한 안에서 고른 값(`shared/pagination.py` 의 MAX_LIMIT 는 200). */
const PER_PAGE = 50

export default function AuditPage() {
  const [offset, setOffset] = useState(0)
  const page = useResource(
    () => api.get<Page<AuditEntry>>(`/audit/entries?limit=${PER_PAGE}&offset=${offset}`),
    [offset],
  )

  return (
    <div className="space-y-6">
      <PageHeader
        title="변경 이력"
        description="되돌릴 수 없거나 권한이 실린 변경만 남습니다. 여기서는 수정할 수 없습니다."
      />

      <ErrorNotice error={page.error} />

      {page.data && page.data.items.length === 0 ? (
        <EmptyState title="기록이 없습니다" hint="아직 남길 만한 변경이 없었습니다." />
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>시각</TableHead>
              <TableHead>한 일</TableHead>
              <TableHead>누가</TableHead>
              <TableHead>대상</TableHead>
              <TableHead>바뀐 것</TableHead>
              <TableHead>요청 ID</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {(page.data?.items ?? []).map((one) => (
              <TableRow key={one.id}>
                <TableCell className="whitespace-nowrap">{shownDateTime(one.created_at)}</TableCell>
                <TableCell className="font-mono text-xs">{one.action}</TableCell>
                <TableCell>
                  {one.actor_label}
                  {/* **사람과 통로를 함께 보여 준다.** 「관리자가 바꿨습니다」 만
                      말하면, 그 관리자가 직접 눌렀는지 자기 토큰을 쥔 스크립트가
                      눌렀는지는 다른 이야기인데 구별할 방법이 없다. */}
                  {(one.actor_client || one.actor_token) && (
                    <p className="text-muted-foreground text-xs">
                      {[one.actor_client, one.actor_token].filter(Boolean).join(' · ')}
                    </p>
                  )}
                </TableCell>
                <TableCell>{one.target_label}</TableCell>
                <TableCell className="text-muted-foreground text-xs">
                  {shownChanges(one.changes)}
                  {one.reason && <p className="mt-1">사유: {one.reason}</p>}
                </TableCell>
                {/* **로그와 잇는 끈이다.** 이 값으로 app.log 에서 그 요청의 모든
                    줄을 찾을 수 있다. */}
                <TableCell className="font-mono text-xs">{one.request_id ?? '—'}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}

      {/* **없으면 50건이 넘는 순간 나머지를 볼 방법이 없다** — 그리고 목록은
          잘렸다는 말을 하지 않으므로 사람은 그것이 전부라고 읽는다. */}
      {page.data && (
        <Pagination
          total={page.data.total}
          limit={page.data.limit}
          offset={page.data.offset}
          onChange={setOffset}
        />
      )}
    </div>
  )
}
