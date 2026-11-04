/**
 * HWAX 포털 로그인(ADR 0028) — **켜졌나 · 이 서버에서 포털의 공개 키가 닿나.**
 *
 * 설정만 보고 「켜짐」 이라고 하면, 공개 키(JWKS) 주소가 이 서버에서 안 닿는 것은 첫 사람이
 * 포털로 로그인을 시도해야 드러난다 — 그 사람은 「확인할 수 없습니다」 를 보고 돌아간다. 그래서
 * 서버가 지금 받아 보고 답한다(`GET /api/auth/portal-status`). 설정끼리 안 맞는 것(접두어 없음 ·
 * audience 다름)도 함께 말한다.
 *
 * **게이트웨이 위임**(포털의 Claude 가 사람마다 이 설치의 액세스 토큰을 받는 길)도 같은 자리에서
 * 보인다 — 게이트웨이는 위임이 거절되면 그 사람의 도구 호출을 거절하므로, 「마지막으로 거절한 때 ·
 * 왜」 가 그 사람의 신고를 받고 찾아볼 자리다.
 */

import { type GatewayDelegation, portalApi } from '@/modules/auth/portal'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { StatusBadge } from '@/shared/components/StatusBadge'
import { useResource } from '@/shared/hooks/useResource'
import { shownDateTime } from '@/shared/lib/datetime'

export function PortalLogin() {
  const status = useResource(() => portalApi.status(), [])
  const one = status.data

  return (
    <section className="space-y-3">
      <h2 className="flex items-center gap-2 text-base font-semibold">
        HWAX 포털 로그인
        {one && (
          <StatusBadge
            kind="portal"
            value={!one.enabled ? 'off' : one.reachable ? 'on' : 'unreachable'}
          />
        )}
      </h2>
      <ErrorNotice error={status.error} />
      {one && !one.enabled && (
        <p className="text-muted-foreground text-sm">
          이 설치는 HWAX 포털에 연결되어 있지 않습니다. 연결하려면 <code>.env</code> 에{' '}
          <code>PORTAL_JWKS_URL</code> · <code>PORTAL_LAUNCH_URL</code> 을 적고 재시작합니다(운영
          안내서 「HWAX 포털에 붙이기」). 꺼져 있는 동안 포털 로그인 콜백은 404 로 답합니다.
        </p>
      )}
      {one?.enabled && (
        <dl className="grid gap-3 rounded-md border p-4 text-sm sm:grid-cols-2">
          <div>
            <dt className="text-muted-foreground text-xs">공개 키(JWKS)</dt>
            <dd>
              {one.reachable
                ? `받음 — 서명 키 ${one.kids.length}개 (${one.kids.join(', ')})`
                : '받지 못함 — 포털로 오는 로그인을 확인할 수 없습니다'}
            </dd>
            <dd className="text-muted-foreground font-mono text-xs break-all">{one.jwks_url}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground text-xs">마지막으로 받은 때 (이 프로세스)</dt>
            <dd>{one.fetched_at ? shownDateTime(one.fetched_at) : '—'}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground text-xs">포털 타일의 audience · 콜백</dt>
            <dd className="font-mono text-xs break-all">
              {one.audience} · {one.callback_path}
            </dd>
          </div>
          <div>
            <dt className="text-muted-foreground text-xs">발급자(iss)</dt>
            <dd className="font-mono text-xs break-all">{one.issuer}</dd>
          </div>
          <div className="sm:col-span-2">
            <dt className="text-muted-foreground text-xs">로그인 화면의 「HWAX 포털로 로그인」</dt>
            <dd className="font-mono text-xs break-all">
              {one.launch_url ?? '— (PORTAL_LAUNCH_URL 이 없어 단추를 표시하지 않습니다)'}
            </dd>
          </div>
          {one.last_error && (
            <div className="sm:col-span-2">
              <dt className="text-muted-foreground text-xs">
                마지막 오류{one.last_error_at ? ` (${shownDateTime(one.last_error_at)})` : ''}
              </dt>
              <dd className="font-mono text-xs break-all">{one.last_error}</dd>
            </div>
          )}
        </dl>
      )}
      {one && <Delegation one={one.delegation} />}
      {one && one.problems.length > 0 && (
        <ul className="list-disc space-y-1 rounded-md border border-amber-500/40 bg-amber-500/5 p-3 pl-7 text-sm">
          {one.problems.map((problem) => (
            <li key={problem}>{problem}</li>
          ))}
        </ul>
      )}
    </section>
  )
}

function Delegation({ one }: { one: GatewayDelegation }) {
  const state = !one.enabled ? 'off' : one.invalid_ips.length > 0 ? 'blocked' : 'on'
  const refusal = one.last_refusal
  return (
    <div className="space-y-2">
      <h3 className="flex items-center gap-2 text-sm font-semibold">
        게이트웨이 위임(Claude)
        <StatusBadge kind="delegation" value={state} />
      </h3>
      {!one.enabled ? (
        <p className="text-muted-foreground text-sm">
          포털의 Claude(MCP 게이트웨이)가 사람마다 이 설치의 액세스 토큰을 받는 길이 꺼져 있습니다.
          켜려면 <code>.env</code> 에 <code>HEAX_SSO_SECRET</code> 이 있어야 합니다 — 서버에서{' '}
          <code>deploy.sh hwax-request</code> 가 만들고 포털에 보낼 요청서를 씁니다(운영 안내서
          「HWAX 포털에 붙이기」).
        </p>
      ) : (
        <dl className="grid gap-3 rounded-md border p-4 text-sm sm:grid-cols-2">
          <div>
            <dt className="text-muted-foreground text-xs">게이트웨이가 부르는 주소</dt>
            <dd className="font-mono text-xs break-all">
              http://&lt;이 서버&gt;:&lt;앱 포트&gt;{one.sso_path}
            </dd>
          </div>
          <div>
            <dt className="text-muted-foreground text-xs">허용 주소</dt>
            <dd className="font-mono text-xs break-all">
              {one.allowed_ips.length > 0 ? one.allowed_ips.join(', ') : '제한 없음(비밀만 확인)'}
            </dd>
          </div>
          <div>
            <dt className="text-muted-foreground text-xs">발급하는 액세스 토큰</dt>
            <dd>
              하루 · 범위 <span className="font-mono text-xs">{one.scopes.join(', ')}</span> · 지금
              사용 중 {one.live_tokens}개
            </dd>
          </div>
          <div>
            <dt className="text-muted-foreground text-xs">마지막 발급</dt>
            <dd>{one.last_issued_at ? shownDateTime(one.last_issued_at) : '— (아직 없음)'}</dd>
          </div>
          <div className="sm:col-span-2">
            <dt className="text-muted-foreground text-xs">마지막 거절</dt>
            <dd>
              {refusal
                ? `${shownDateTime(refusal.at)} · ${refusal.status} — ${refusal.reason}`
                : '없음'}
            </dd>
          </div>
        </dl>
      )}
    </div>
  )
}
