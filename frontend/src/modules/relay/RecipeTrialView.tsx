/**
 * 레시피 판 시험의 결과 — **켜면 무엇이 바뀌나.** 대상에는 아무것도 안 갔다.
 *
 * 보낼 것 표는 움직이는 줄(새 · 바뀜 · 무덤)만 싣는다 — 그대로인 타입까지 늘어놓으면 바뀌는 것이
 * 그 사이에 묻힌다.
 */

import type { RecipeTrial } from '@/modules/relay/api'
import { StatusBadge } from '@/shared/components/StatusBadge'
import { Alert, AlertDescription, AlertTitle } from '@/shared/components/ui/alert'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/shared/components/ui/table'

const KIND_LABEL: Record<string, string> = { objects: '객체', relations: '관계' }
const UNRESOLVED_SHOWN = 5

/** 보낼 것 중 움직이는 줄 — `[종류, 타입, 건수]`. */
export function movingRows(trial: RecipeTrial) {
  const out: { kind: string; type: string; created: number; changed: number; gone: number }[] = []
  for (const [kind, group] of Object.entries(trial.would_send ?? {})) {
    for (const [type, one] of Object.entries(group ?? {})) {
      const created = Number(one.created ?? 0)
      const changed = Number(one.changed ?? 0)
      const gone = Number(one.gone ?? 0)
      if (created + changed + gone > 0) out.push({ kind, type, created, changed, gone })
    }
  }
  return out
}

export function RecipeTrialView({ trial }: { trial: RecipeTrial }) {
  const moving = movingRows(trial)
  const rows = Object.entries(trial.rows ?? {})
  const unresolved = trial.unresolved ?? []
  const problems = trial.problems ?? []
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="font-medium">판 {trial.version} 시험</span>
        <StatusBadge kind="relay_trial" value={trial.clean ? 'clean' : 'blocked'} />
        <span className="text-muted-foreground text-xs">대상에는 아무것도 보내지 않았습니다.</span>
      </div>

      {trial.error && (
        <Alert variant="destructive">
          <AlertTitle>변환이 멈췄습니다</AlertTitle>
          <AlertDescription>{trial.error}</AlertDescription>
        </Alert>
      )}
      {problems.length > 0 && (
        <Alert className="border-amber-500/40 bg-amber-500/5">
          <AlertTitle>레시피의 문제 {problems.length}건</AlertTitle>
          <AlertDescription>
            <ul className="list-disc pl-4">
              {problems.map((one) => (
                <li key={one}>{one}</li>
              ))}
            </ul>
          </AlertDescription>
        </Alert>
      )}

      {rows.length > 0 && (
        <p className="text-sm">
          <span className="text-muted-foreground">만든 줄 — </span>
          {rows.map(([type, count]) => `${type} ${count.toLocaleString()}`).join(' · ')}
        </p>
      )}

      {trial.would_send && (
        <div className="space-y-1">
          <p className="text-sm font-medium">
            켜면 보낼 것
            {trial.against && (
              <span className="text-muted-foreground ml-1 text-xs font-normal">
                — 견준 것: {trial.against}
              </span>
            )}
          </p>
          {moving.length === 0 ? (
            <p className="text-muted-foreground text-sm">바뀌는 것이 없습니다.</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>종류</TableHead>
                  <TableHead>타입</TableHead>
                  <TableHead className="text-right">새</TableHead>
                  <TableHead className="text-right">바뀜</TableHead>
                  <TableHead className="text-right">무덤</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {moving.map((one) => (
                  <TableRow key={`${one.kind}:${one.type}`}>
                    <TableCell>{KIND_LABEL[one.kind] ?? one.kind}</TableCell>
                    <TableCell className="font-mono text-xs">{one.type}</TableCell>
                    <TableCell className="text-right">{one.created.toLocaleString()}</TableCell>
                    <TableCell className="text-right">{one.changed.toLocaleString()}</TableCell>
                    <TableCell className="text-right">{one.gone.toLocaleString()}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </div>
      )}

      {(trial.unresolved_count ?? 0) > 0 && (
        <div className="space-y-1">
          <p className="text-destructive text-sm">
            미해결 {trial.unresolved_count?.toLocaleString()}건 — 켜면 그 실행은 멈춥니다. 앞의{' '}
            {Math.min(UNRESOLVED_SHOWN, unresolved.length)}건:
          </p>
          <pre className="bg-muted/30 max-h-48 overflow-auto rounded-md border p-2 text-xs">
            {unresolved
              .slice(0, UNRESOLVED_SHOWN)
              .map((one) => JSON.stringify(one))
              .join('\n')}
          </pre>
        </div>
      )}

      {trial.report && (
        <details className="rounded-md border">
          <summary className="cursor-pointer px-2 py-1 text-sm">변환 보고 펼치기</summary>
          <pre className="bg-muted/30 max-h-80 overflow-auto border-t p-2 text-xs whitespace-pre-wrap">
            {trial.report}
          </pre>
        </details>
      )}
    </div>
  )
}
