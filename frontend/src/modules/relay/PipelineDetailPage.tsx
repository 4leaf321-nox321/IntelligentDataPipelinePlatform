/**
 * 파이프라인 상세 — 원천 + 레시피 + 대상. **칸 대응 표**(대상의 칸이 원천의 어디서 오나) · 지금
 * 상태 · 실행 이력 · 최근 작업, 그리고 시스템 관리자의 「지금 실행」 · 「적용」 · 레시피 판.
 *
 * 비밀은 안 나온다 — 대상 토큰은 **환경 변수 이름**만 보인다(값은 서버가 풀어 쓴다).
 */

import { useParams } from 'react-router-dom'

import { relayApi } from '@/modules/relay/api'
import { LineageTable } from '@/modules/relay/LineageTable'
import { PipelineActions } from '@/modules/relay/PipelineActions'
import { Fact, PipelineHistory, PipelineJobs, PipelineState } from '@/modules/relay/PipelineRuns'
import { RecipeVersions } from '@/modules/relay/RecipeVersions'
import { useAuth } from '@/shared/auth/AuthContext'
import { isSystemAdmin } from '@/shared/auth/roles'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { PageHeader } from '@/shared/components/PageHeader'
import { StatusBadge } from '@/shared/components/StatusBadge'
import { Alert, AlertDescription, AlertTitle } from '@/shared/components/ui/alert'
import { useResource } from '@/shared/hooks/useResource'

const BACK = { to: '/relay/pipelines', label: '파이프라인' }

export default function PipelineDetailPage() {
  const { slug = '' } = useParams()
  const { user } = useAuth()
  const admin = isSystemAdmin(user)
  const detail = useResource(() => relayApi.pipeline(slug), [slug])
  const data = detail.stale ? null : detail.data

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

      {data.problems.length > 0 && (
        <Alert variant="destructive">
          <AlertTitle>정의에 문제가 있습니다 — 고치기 전에는 돌지 않습니다</AlertTitle>
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
          <Fact label="정의 파일">
            <span className="font-mono text-xs">{data.definition_path}</span>
          </Fact>
          <Fact label="레시피">
            {data.recipe_label === '파일' ? (
              <span className="font-mono text-xs">{data.recipe_path || '—'}</span>
            ) : (
              <span>{data.recipe_label}</span>
            )}
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

      {admin && (
        <RecipeVersions slug={data.slug} recipeLabel={data.recipe_label} onActivated={detail.reload} />
      )}

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
