/**
 * 파이프라인 상세 — 원천 + 레시피 + 대상. **칸 대응 표**(대상의 칸이 원천의 어디서 오나) · 지금
 * 상태 · 대조(대상에 지금 있는 것과 기준) · 값의 출처(거꾸로 보기) · 실행 이력 · 최근 작업, 그리고 시스템 관리자의 「지금
 * 실행」 · 「적용」 · 「대조」 · 정의 판 · 레시피 판.
 *
 * 초안(AI 가 지은 정의 판이 아직 안 켜진 새 파이프라인)은 돌지 않는다 — 맨 위에 그렇게 적고, 실행
 * 단추 대신 정의 판으로 보낸다.
 *
 * 비밀은 안 나온다 — 대상 토큰은 **환경 변수 이름**만 보인다(값은 서버가 풀어 쓴다).
 *
 * 자동 적용(타이머의 실행이 기준을 넘은 계획을 사람 없이 적용)은 사실 목록에 기준과 함께 적는다 —
 * 꺼진 것이 기본이라 「끔」 은 흐리게. 받기 점검(대량 삭제 · 빈 칸이면 멈춤)도 그 곁에 기준을 적는다.
 *
 * **늦음**(일정대로라면 끝났어야 할 실행이 끝나지 않음)은 맨 위에 까닭과 함께 — 실패도 멈춤도
 * 아니라서 이력에는 아무 줄도 생기지 않는다. 실행 추이(그림)는 그림 라이브러리를 이 화면에서만 받게
 * `lazy()` 로 매단다.
 */

import { lazy, Suspense, useState } from 'react'
import { useParams } from 'react-router-dom'

import { relayApi } from '@/modules/relay/api'
import type { PipelineDetail } from '@/modules/relay/api'
import { DefinitionVersions } from '@/modules/relay/DefinitionVersions'
import { LineageTable } from '@/modules/relay/LineageTable'
import { PipelineActions } from '@/modules/relay/PipelineActions'
import { Fact, PipelineHistory, PipelineJobs, PipelineState } from '@/modules/relay/PipelineRuns'
import { ReconcileSection } from '@/modules/relay/ReconcileSection'
import { RecipeVersions } from '@/modules/relay/RecipeVersions'
import { ValueTraceSection } from '@/modules/relay/ValueTrace'
import type { TraceRequest } from '@/modules/relay/ValueTrace'
import { useAuth } from '@/shared/auth/AuthContext'
import { isSystemAdmin } from '@/shared/auth/roles'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { PageHeader } from '@/shared/components/PageHeader'
import { StatusBadge } from '@/shared/components/StatusBadge'
import { Alert, AlertDescription, AlertTitle } from '@/shared/components/ui/alert'
import { useResource } from '@/shared/hooks/useResource'
import { cn } from '@/shared/lib/utils'

const BACK = { to: '/relay/pipelines', label: '파이프라인' }

const PipelineTrend = lazy(() => import('@/modules/relay/PipelineTrend'))

/**
 * 정의 판 · 레시피 판 — 시스템 관리자만. 정의 판의 「시험에 쓸 레시피」 가 레시피 판 목록을 쓰므로
 * 그 목록은 여기서 한 번 읽어 둘에 준다(따로 읽으면 한쪽에서 켠 판이 다른 쪽에 안 보인다).
 */
function VersionPanels({
  detail,
  onChanged,
}: {
  detail: PipelineDetail
  /** 판을 켜거나 버려 상세가 바뀌었다. */
  onChanged: () => void
}) {
  const recipes = useResource(() => relayApi.recipes(detail.slug), [detail.slug])
  return (
    <>
      <DefinitionVersions
        slug={detail.slug}
        definitionLabel={detail.definition_label}
        draft={detail.draft}
        recipes={recipes.data ?? []}
        onChanged={onChanged}
      />
      <RecipeVersions
        slug={detail.slug}
        recipeLabel={detail.recipe_label}
        versions={recipes}
        onActivated={onChanged}
      />
    </>
  )
}

export default function PipelineDetailPage() {
  const { slug = '' } = useParams()
  const { user } = useAuth()
  const admin = isSystemAdmin(user)
  const detail = useResource(() => relayApi.pipeline(slug), [slug])
  const data = detail.stale ? null : detail.data
  const [traceRequest, setTraceRequest] = useState<TraceRequest | null>(null)

  if (!data) {
    return (
      <div className="space-y-4">
        <PageHeader title={slug} back={BACK} />
        <ErrorNotice error={detail.error} />
        {detail.loading && (
          <p className="text-muted-foreground text-sm">
            읽는 중… 대상 SP 의 정의와 견주느라 몇 초 걸릴 수 있습니다.
          </p>
        )}
      </div>
    )
  }

  const missing = data.lineage.filter((one) => one.target_missing).length
  return (
    <div className="space-y-6">
      <PageHeader
        back={BACK}
        title={
          <span className="flex items-center gap-2">
            <span className="font-mono">{data.slug}</span>
            <StatusBadge kind="relay_pipeline" value={data.status} />
          </span>
        }
        description={`${data.system} ${data.endpoint_label} → ${data.target_name || data.target_server}`}
      />

      <ErrorNotice error={detail.error} />

      {data.late && (
        <Alert variant="destructive">
          <AlertTitle>실행이 늦습니다 — 일정대로라면 끝났어야 할 실행이 끝나지 않았습니다</AlertTitle>
          <AlertDescription>
            {data.late} 작업 워커 · 일정 타이머의 상태는 「서버」 화면, 걸린 작업은 아래 「최근 작업」 에서
            확인하세요.
          </AlertDescription>
        </Alert>
      )}
      {data.draft && (
        <Alert className="border-dashed">
          <AlertTitle>이 파이프라인은 아직 초안입니다 — 정의 판을 사용해야 실행됩니다</AlertTitle>
          <AlertDescription>
            {admin
              ? 'AI(MCP)가 작성한 정의 판 초안입니다. 아래 「정의 판」 에서 시험해 원천 · 대상에 ' +
                '닿는지 확인하고 「사용」 을 클릭하면 그때부터 「지금 실행」 이 표시됩니다.'
              : '시스템 관리자가 정의 판을 시험하고 사용하면 그때부터 실행됩니다.'}
          </AlertDescription>
        </Alert>
      )}
      {data.problems.length > 0 && (
        <Alert variant="destructive">
          <AlertTitle>정의에 문제가 있습니다 — 수정하기 전에는 실행되지 않습니다</AlertTitle>
          <AlertDescription>
            <ul className="list-disc pl-4">
              {data.problems.map((one) => (
                <li key={one}>{one}</li>
              ))}
            </ul>
          </AlertDescription>
        </Alert>
      )}
      {data.target_schema_note && (
        <Alert className="border-amber-500/40 bg-amber-500/5">
          <AlertTitle>대상의 정의와 견주지 못했습니다</AlertTitle>
          <AlertDescription>
            {data.target_schema_note} 칸 대응 표의 「대상 SP 정의에 없는 칸」 표시는 그동안 비어
            있습니다.
          </AlertDescription>
        </Alert>
      )}

      <section className="grid gap-6 lg:grid-cols-2">
        <dl className="space-y-1.5">
          <Fact label="원천 시스템">
            {data.system}
            <span className="text-muted-foreground ml-1 font-mono text-xs">{data.source}</span>
          </Fact>
          <Fact label="API">
            {data.endpoint_label}
            <p className="text-muted-foreground font-mono text-xs">{data.endpoint}</p>
          </Fact>
          <Fact label="어댑터">
            <span className="font-mono text-xs">{data.adapter_kind}</span>
          </Fact>
          <Fact label="대상">
            {data.target_name || '—'}
            <p className="text-muted-foreground font-mono text-xs">{data.target_server}</p>
          </Fact>
          <Fact label="대상 토큰">
            <span className="font-mono text-xs">{data.target_token_env || '—'}</span>
            <span className="text-muted-foreground ml-1 text-xs">(환경 변수 이름)</span>
          </Fact>
          <Fact label="정의">
            {data.definition_label === '파일' ? (
              <span className="font-mono text-xs">{data.definition_path}</span>
            ) : (
              <span>{data.definition_label}</span>
            )}
          </Fact>
          <Fact label="레시피">
            {data.recipe_label === '파일' ? (
              <span className="font-mono text-xs">{data.recipe_path || '—'}</span>
            ) : (
              <span>{data.recipe_label}</span>
            )}
          </Fact>
          <Fact label="자동 적용">
            <span className={cn(data.auto_apply_label === '미사용' && 'text-muted-foreground')}>
              {data.auto_apply_label}
            </span>
          </Fact>
          <Fact label="받기 점검">
            <span className={cn(data.intake_label === '미사용' && 'text-muted-foreground')}>
              {data.intake_label || '—'}
            </span>
          </Fact>
        </dl>
        <PipelineState detail={data} />
      </section>

      {admin ? (
        <PipelineActions detail={data} onQueued={detail.reload} />
      ) : (
        <p className="text-muted-foreground text-sm">
          「지금 실행」 · 「적용」 은 시스템 관리자가 합니다.
        </p>
      )}

      <section className="space-y-2">
        <h2 className="text-base font-semibold">
          칸 대응 표
          {missing > 0 && (
            <span className="text-destructive ml-2 text-sm font-normal">
              대상 SP 정의에 없는 칸 {missing}개
            </span>
          )}
        </h2>
        <p className="text-muted-foreground text-sm">
          대상의 칸마다 어느 변환 · 어느 열을 거쳐 원천 응답의 어디서 오는지 — 레시피에서 계산합니다.
        </p>
        <LineageTable steps={data.lineage} />
      </section>

      <ReconcileSection
        detail={data}
        admin={admin}
        onQueued={detail.reload}
        onTrace={(typeSlug, key) => setTraceRequest({ typeSlug, key, nonce: Date.now() })}
      />

      {data.types.length > 0 && (
        <ValueTraceSection slug={data.slug} types={data.types} request={traceRequest} />
      )}

      {admin && <VersionPanels detail={data} onChanged={detail.reload} />}

      <section className="space-y-2">
        <h2 className="text-base font-semibold">실행 추이</h2>
        <Suspense fallback={<p className="text-muted-foreground text-sm">그림을 불러오는 중…</p>}>
          <PipelineTrend slug={data.slug} />
        </Suspense>
      </section>

      <section className="space-y-2">
        <h2 className="text-base font-semibold">실행 이력</h2>
        <PipelineHistory detail={data} />
      </section>

      <section className="space-y-2">
        <h2 className="text-base font-semibold">최근 작업</h2>
        <PipelineJobs detail={data} />
      </section>
    </div>
  )
}
