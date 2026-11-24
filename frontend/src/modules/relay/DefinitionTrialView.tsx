/**
 * 정의 판 시험의 결과 — **켜면 돌겠나.** 원천(받아 봤나) · 대상(닿나 · 토큰 · 타입) · 레시피(바꿔
 * 봤나)를 차례로 싣는다. 대상에는 아무것도 안 갔다.
 *
 * 막는 것은 `trialBlocks` 한 곳에서 센다 — 표의 배지 · 이 결과 · 켜기 확인 창이 같은 판정을 쓴다.
 * 레시피 쪽은 레시피 판 시험과 모양이 같아 `RecipeTrialView` 로 그린다.
 *
 * 원천이 크면 서버가 앞의 몇 줄만 받는다(`source.capped`). 그때 레시피 쪽은 「켜면 보낼 것」 을 세지
 * 않는다 — 덜 받은 채 견주면 안 받은 나머지가 다 지움(무덤)으로 보이기 때문이다. 막는 것은 아니다.
 */

import type {
  DefinitionSourceCheck,
  DefinitionTargetCheck,
  DefinitionTrial,
} from '@/modules/relay/api'
import { RecipeTrialView } from '@/modules/relay/RecipeTrialView'
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

/** 보기 표에 싣는 열 — 넓은 원천이면 표가 화면을 넘는다. 나머지는 열 목록에 다 있다. */
const SAMPLE_COLUMNS = 8
const CELL_MAX = 60

/** 레시피로 바꿔 본 것 — 레시피가 없었으면 까닭(`note`)만 와서 null. */
export function triedRecipe(trial: DefinitionTrial) {
  const recipe = trial.recipe
  return recipe && 'used' in recipe ? recipe : null
}

/** 시험용 상한에 걸려 앞부분만 받았으면 그 줄 수 — 그때 레시피 쪽에 「켜면 보낼 것」 이 없다. */
export function trialCapped(trial: DefinitionTrial): number | null {
  const source = trial.source
  // 0 은 「상한 없음」 이다(서버의 RELAY_TRIAL_ROWS=0) — 걸린 것으로 읽지 않는다.
  return source?.ok && source.capped ? source.capped : null
}

/** 켜면 다음 실행을 막을 것 — 비었으면 막는 것이 없다. */
export function trialBlocks(trial: DefinitionTrial): string[] {
  const out: string[] = []
  if ((trial.problems ?? []).length > 0) out.push(`정의의 문제 ${trial.problems.length}건`)
  if (!trial.source?.ok) out.push('원천 수신 실패')
  const target = trial.target
  if (!target?.reachable) out.push('대상 연결 불가')
  else if (target.token !== '정상') out.push('대상 토큰으로 정의 조회 실패')
  else if ((target.types_missing ?? []).length > 0)
    out.push(`대상 SP 정의에 없는 타입 ${target.types_missing!.length}개`)
  const recipe = triedRecipe(trial)
  if (recipe?.error) out.push('레시피 변환 중단')
  else if (recipe && !recipe.clean) out.push('레시피 미해결 있음')
  return out
}

/** 표의 배지 — 막힘 › 레시피 없음 › 깨끗함. */
export function trialVerdict(trial: DefinitionTrial): 'clean' | 'blocked' | 'no_recipe' {
  if (trialBlocks(trial).length > 0) return 'blocked'
  return triedRecipe(trial) ? 'clean' : 'no_recipe'
}

const shown = (value: unknown): string => {
  if (value === null || value === undefined) return ''
  const text = typeof value === 'object' ? JSON.stringify(value) : String(value)
  return text.length > CELL_MAX ? `${text.slice(0, CELL_MAX)}…` : text
}

function SampleTable({ rows }: { rows: Record<string, unknown>[] }) {
  if (rows.length === 0) return null
  const columns: string[] = []
  for (const one of rows) {
    for (const key of Object.keys(one)) if (!columns.includes(key)) columns.push(key)
  }
  const picked = columns.slice(0, SAMPLE_COLUMNS)
  return (
    <div className="space-y-1">
      <p className="text-muted-foreground text-xs">
        처음 {rows.length}행
        {columns.length > picked.length && `, 열은 처음 ${picked.length}개만 표시(전체 열은 위 목록)`}
      </p>
      <Table>
        <TableHeader>
          <TableRow>
            {picked.map((one) => (
              <TableHead key={one} className="font-mono text-xs">
                {one}
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row, index) => (
            <TableRow key={index}>
              {picked.map((one) => (
                <TableCell key={one} className="font-mono text-xs">
                  {shown(row[one])}
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}

function SourcePart({ source }: { source: DefinitionSourceCheck | undefined }) {
  if (!source) return null
  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="font-medium">원천</span>
        <StatusBadge kind="relay_source" value={source.ok ? 'ok' : 'failed'} />
        {source.ok && source.probe && (
          <span className="text-muted-foreground font-mono text-xs">{source.probe}</span>
        )}
      </div>
      {!source.ok ? (
        <Alert variant="destructive">
          <AlertTitle>원천 수신 실패</AlertTitle>
          <AlertDescription>{source.error}</AlertDescription>
        </Alert>
      ) : (
        <>
          <p className="text-sm">
            수신 행 {source.rows.toLocaleString()} · 삭제 표시 {source.deleted.toLocaleString()}
            {source.watermark && (
              <>
                {' '}
                · 워터마크 <span className="font-mono text-xs">{source.watermark}</span>
              </>
            )}
          </p>
          {source.capped ? (
            <p className="text-sm text-amber-700 dark:text-amber-400">
              원천 데이터가 많아 처음 {source.capped.toLocaleString()}행만 수신했습니다. 일부만 비교하면
              나머지가 모두 삭제로 집계되므로 '사용 시 전송 예정'은 집계하지 않았습니다.
            </p>
          ) : (
            !source.complete && (
              <p className="text-sm text-amber-700 dark:text-amber-400">
                마지막 페이지까지 수신되지 않았습니다. 실행 시에는 워터마크를 저장하지 않고,
                수신되지 않은 행을 삭제로 처리하지 않습니다.
              </p>
            )
          )}
          {source.rows === 0 ? (
            <p className="text-sm text-amber-700 dark:text-amber-400">
              수신 행이 없습니다. 원천에 데이터가 없거나 정의의 필터(조건, 경로)가 너무 좁습니다.
            </p>
          ) : (
            <>
              <p className="text-sm">
                <span className="text-muted-foreground">열 {source.columns.length}개: </span>
                <span className="font-mono text-xs">{source.columns.join(' · ')}</span>
              </p>
              {source.always_empty.length > 0 && (
                <p className="text-sm text-amber-700 dark:text-amber-400">
                  모든 수신 행에서 값이 비어 있는 열 {source.always_empty.length}개. 열 이름이 원천과
                  일치하는지 확인하세요:{' '}
                  <span className="font-mono text-xs">{source.always_empty.join(' · ')}</span>
                </p>
              )}
              <SampleTable rows={source.sample ?? []} />
            </>
          )}
        </>
      )}
    </div>
  )
}

function TargetPart({ target }: { target: DefinitionTargetCheck | undefined }) {
  if (!target) return null
  const missing = target.types_missing ?? []
  const present = target.types_present ?? []
  const managedBy = target.managed_by ?? {}
  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="font-medium">대상</span>
        <StatusBadge kind="relay_target" value={target.reachable ? 'ok' : 'unreachable'} />
        <span className="text-muted-foreground font-mono text-xs">{target.server}</span>
        {target.slug && (
          <span className="text-muted-foreground text-xs">
            {target.slug}
            {target.version ? ` · ${target.version}` : ''}
          </span>
        )}
      </div>
      {target.error && <p className="text-destructive text-sm">{target.error}</p>}
      {target.token !== undefined && (
        <p className="text-sm">
          <span className="text-muted-foreground">토큰: </span>
          {target.token === '정상' ? '정상' : <span className="text-destructive">{target.token}</span>}
        </p>
      )}
      {missing.length > 0 && (
        <p className="text-destructive text-sm">
          대상 SP 정의에 없는 타입 {missing.length}개. 대상에 타입을 등록해야 전송할 수 있습니다:{' '}
          <span className="font-mono text-xs">{missing.join(', ')}</span>
        </p>
      )}
      {present.length > 0 && (
        <p className="text-sm">
          <span className="text-muted-foreground">대상에 있는 타입: </span>
          <span className="font-mono text-xs">
            {present
              .map((one) => (managedBy[one] ? `${one}(관리: ${managedBy[one]})` : one))
              .join(', ')}
          </span>
        </p>
      )}
    </div>
  )
}

export function DefinitionTrialView({ trial }: { trial: DefinitionTrial }) {
  const problems = trial.problems ?? []
  const recipe = triedRecipe(trial)
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="font-medium">정의 버전 {trial.version} 테스트</span>
        <StatusBadge kind="relay_trial" value={trialVerdict(trial)} />
        <span className="text-muted-foreground text-xs">대상에는 아무것도 전송하지 않았습니다.</span>
      </div>

      {problems.length > 0 && (
        <Alert variant="destructive">
          <AlertTitle>정의의 문제 {problems.length}건 — 수정 전에는 실행되지 않음</AlertTitle>
          <AlertDescription>
            <ul className="list-disc pl-4">
              {problems.map((one) => (
                <li key={one}>{one}</li>
              ))}
            </ul>
          </AlertDescription>
        </Alert>
      )}

      <SourcePart source={trial.source} />
      {trial.intake?.held && (
        <Alert className="border-amber-500/50 bg-amber-500/5">
          <AlertTitle>이 정의로 수신하면 수신 검증에서 실행 중단</AlertTitle>
          <AlertDescription>
            <ul className="list-disc pl-4">
              {(trial.intake.reasons ?? []).map((one) => (
                <li key={one}>{one}</li>
              ))}
            </ul>
            <p className="text-muted-foreground mt-1 text-xs">{trial.intake.note}</p>
          </AlertDescription>
        </Alert>
      )}
      {trial.intake && !trial.intake.held && (
        <p className="text-muted-foreground text-xs">
          수신 검증: {trial.intake.checked ? '해당 항목 없음. ' : ''}
          {trial.intake.note}
        </p>
      )}
      <TargetPart target={trial.target} />

      {recipe ? (
        <div className="border-t pt-3">
          <RecipeTrialView trial={recipe} title={`레시피 — ${recipe.used}`} />
        </div>
      ) : trial.recipe ? (
        <p className="border-t pt-3 text-sm text-amber-700 dark:text-amber-400">
          레시피가 아직 없습니다. 이 정의를 사용해도 레시피 버전을 사용하기 전에는 실행되지 않습니다.
          AI(MCP)가 레시피 버전 초안을 작성하면 [테스트에 사용할 레시피]에서 그 버전을 선택해 다시
          테스트하세요.
        </p>
      ) : (
        trial.source &&
        !trial.source.ok && (
          <p className="text-muted-foreground border-t pt-3 text-sm">
            원천 수신에 실패해 레시피 변환을 테스트하지 않았습니다.
          </p>
        )
      )}
    </div>
  )
}
