/**
 * 칸 대응 표 — 대상 SP 의 `타입.칸` 하나가 **어느 변환 · 어느 열 · 원천 응답의 어느 자리**에서 오나.
 *
 * 손으로 적은 표가 아니라 정의(레시피)에서 나온다. 대상 SP 의 정의에 없는 칸은 빨갛게 — 보내면
 * 대상이 거절하거나 조용히 버린다.
 */

import { Fragment } from 'react'

import type { LineageStep } from '@/modules/relay/api'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/shared/components/ui/table'
import { cn } from '@/shared/lib/utils'

/** 식별자 · 이름 · 관계 — 속성이 아니라 객체의 뼈대라 표에서 따로 보인다. */
function fixedRole(field: string): string | null {
  if (field === 'key') return '식별자'
  if (field === 'label') return '이름'
  if (field.startsWith('→')) return '관계'
  return null
}

function groupByType(steps: LineageStep[]): [string, LineageStep[]][] {
  const groups = new Map<string, LineageStep[]>()
  for (const one of steps) groups.set(one.target_type, [...(groups.get(one.target_type) ?? []), one])
  return [...groups]
}

const list = (values: string[]) =>
  values.length === 0 ? (
    <span className="text-muted-foreground">—</span>
  ) : (
    values.map((one, index) => (
      <div key={`${one}:${index}`} className="font-mono text-xs">
        {one || <span className="text-muted-foreground">(어댑터에 없음)</span>}
      </div>
    ))
  )

export function LineageTable({ steps }: { steps: LineageStep[] }) {
  if (steps.length === 0) {
    return (
      <p className="text-muted-foreground text-sm">
        레시피를 못 읽어 칸 대응을 그리지 못했습니다 — 위의 문제를 먼저 보세요.
      </p>
    )
  }
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>대상 칸</TableHead>
          <TableHead>← 변환</TableHead>
          <TableHead>← 열</TableHead>
          <TableHead>← 원천 자리</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {groupByType(steps).map(([type, rows]) => (
          <Fragment key={type}>
            <TableRow className="bg-muted/40 hover:bg-muted/40">
              <TableCell colSpan={4} className="font-mono text-xs font-semibold">
                {type}
              </TableCell>
            </TableRow>
            {rows.map((one, index) => {
              const role = fixedRole(one.target_field)
              return (
                <TableRow
                  key={`${one.target_field}:${index}`}
                  data-missing={one.target_missing || undefined}
                  className={cn(
                    role && 'bg-muted/20',
                    one.target_missing && 'bg-destructive/5 hover:bg-destructive/10',
                  )}
                >
                  <TableCell className="align-top">
                    <div className="flex items-center gap-1.5">
                      <span
                        className={cn(
                          'font-mono text-sm',
                          role && 'font-semibold',
                          one.target_missing && 'text-destructive',
                        )}
                      >
                        {one.target_field}
                      </span>
                      {role && (
                        <span className="bg-muted text-muted-foreground rounded px-1 text-[10px]">
                          {role}
                        </span>
                      )}
                    </div>
                    {(one.target_label || one.target_data_type) && (
                      <p className="text-muted-foreground text-xs">
                        {one.target_label}
                        {one.target_label && one.target_data_type ? ' · ' : ''}
                        {one.target_data_type}
                      </p>
                    )}
                    {one.target_missing && (
                      <p className="text-destructive text-xs">대상 SP 정의에 없는 칸</p>
                    )}
                  </TableCell>
                  <TableCell className="align-top text-xs whitespace-normal">
                    {one.transform}
                  </TableCell>
                  <TableCell className="align-top">{list(one.columns)}</TableCell>
                  <TableCell className="align-top">{list(one.source_paths)}</TableCell>
                </TableRow>
              )
            })}
          </Fragment>
        ))}
      </TableBody>
    </Table>
  )
}
