/**
 * **정제 도구 키트** — 원천 파일을 정제해 한꺼번에(수만 줄) 넣는 길. 사용자 PC 에 푼다.
 *
 * 서버가 같은 판의 zip 을 들고 있다가 여기서 내려준다. 따로(GitHub 릴리스에서) 받게 두었더니
 * 사내망에서는 받을 길이 없었고, 운영의 사람도 AI 도 그런 것이 있는 줄 몰랐다(실측).
 *
 * ## 받는 사람은 개발자가 아니다
 *
 * Claude Desktop 만 쓰는 데이터 담당자다 — 명령 창 · 설정 JSON · 토큰 범위를 몰라도 되게 한다.
 *
 *     받기 → 풀기 → 「이 PC 에 등록 정보 복사」 → install.cmd 더블클릭 → Claude Desktop 재시작
 *
 * 등록 정보 복사는 **이 플랫폼용 토큰을 하나 발급**해 이름 · 주소와 함께 클립보드에 넣는다.
 * `install.cmd` 가 그것을 읽어 등록하고 클립보드를 비운다 — 토큰이 대화(AI)에 나갈 일이 없다.
 * 플랫폼이 여럿이면 각 플랫폼 화면에서 같은 일을 한 번씩 — 앞에 등록한 것은 남는다.
 */

import { useState } from 'react'
import { ClipboardCopy, Download } from 'lucide-react'

import { Snippet } from '@/modules/auth/McpSetupGuide'
import { api, downloadFile } from '@/shared/api/client'
import { useAuth } from '@/shared/auth/AuthContext'
import { PUBLIC_PATH } from '@/shared/base'
import { APP_SLUG } from '@/shared/branding'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { Button } from '@/shared/components/ui/button'
import { useResource } from '@/shared/hooks/useResource'
import { copyText } from '@/shared/lib/clipboard'

const TOKEN_PLACEHOLDER = '‹발급받은_토큰›'
/** 클립보드에 넣는 등록 정보의 머리 — 키트의 `sp_setup.REGISTRATION` 과 같아야 한다. */
export const REGISTRATION = 'SP-PIPELINE-PLATFORM'

export interface PipelineKit {
  available: boolean
  filename: string
  version: string
  size_bytes: number
}

/** 정제 도구가 부를 이 설치의 주소 — 접두어(`/<slug>`)까지. 도구가 뒤에 `/api/…` 를 붙인다. */
export function appUrlFrom(origin: string, prefix: string = PUBLIC_PATH): string {
  return `${origin}${prefix}`
}

/** `install.cmd` 가 클립보드에서 찾는 한 줄 — 이름 · 주소 · 토큰. */
export function registrationLine(platform: string, server: string, token: string): string {
  return `${REGISTRATION} ${JSON.stringify({ platform, server, token })}`
}

/**
 * 명령으로 할 때의 설치 명령 — **한 줄로.** 줄 잇기 기호가 셸마다 다르다(cmd 는 `^`,
 * PowerShell 은 `` ` ``) — 하나를 고르면 다른 쪽에서 깨진 명령이 되고, 사람은 그것을 키트가
 * 고장 난 것으로 읽는다.
 */
export function setupCommand(
  token: string | null,
  server: string,
  platform: string = APP_SLUG,
): string {
  return (
    // 키트에 든 파이썬으로 — PC 에 파이썬이 없어도 된다(PATH 에도 안 올린다).
    `python\\python.exe sp_setup.py --platform ${platform} ` +
    `--server ${server} --token ${token || TOKEN_PLACEHOLDER} --write-claude`
  )
}

function megabytes(bytes: number): string {
  return `${(bytes / 1024 / 1024).toFixed(0)}MB`
}

export function PipelineKitGuide({
  token,
  onIssued,
}: {
  token: string | null
  /** 「이 PC 에 등록」 이 토큰을 하나 만들었다 — 아래 토큰 목록을 다시 읽게. */
  onIssued?: () => void
}) {
  const { user } = useAuth()
  const kit = useResource(() => api.get<PipelineKit>('/server/pipeline-kit/info'), [])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | null>(null)
  /** 정의(타입 · 속성)까지 넣을 수 있게 — **시스템 관리자만** 고를 수 있다. */
  const [definitions, setDefinitions] = useState(false)
  const [copied, setCopied] = useState<'ok' | 'fail' | null>(null)
  const server = appUrlFrom(typeof window === 'undefined' ? '' : window.location.origin)

  async function download(filename: string) {
    setBusy(true)
    setError(null)
    try {
      await downloadFile('/server/pipeline-kit', filename)
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBusy(false)
    }
  }

  /** 이 플랫폼용 토큰을 하나 발급해 등록 정보로 복사한다 — 사람이 범위를 고를 일이 없다. */
  async function register() {
    setBusy(true)
    setError(null)
    setCopied(null)
    try {
      const scopes = ['read', 'objects:write', ...(definitions ? ['ontology:write'] : [])]
      const made = await api.post<{ token: string }>('/auth/tokens', {
        name: `정제 도구 키트 · ${APP_SLUG}`,
        scopes,
        expires_in_days: null,
      })
      onIssued?.()
      await copyText(registrationLine(APP_SLUG, server, made.token))
        .then(() => setCopied('ok'))
        .catch(() => setCopied('fail'))
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="space-y-3 rounded-md border p-4">
      <div>
        <h3 className="text-sm font-semibold">정제 도구 키트 — 원천 파일을 한꺼번에 넣을 때</h3>
        <p className="text-muted-foreground mt-1 text-xs">
          엑셀 · CSV 같은 원천을 AI(Claude Desktop)와 함께 정리해 <b>수만 줄을 한 번에</b> 넣는
          도구입니다. 서버가 아니라 <b>내 PC</b> 에 풀어 씁니다 — 원천 파일이 PC 에 있고, 그 내용이
          대화를 거치지 않고 서버로 곧장 갑니다.
        </p>
      </div>

      {kit.error && <ErrorNotice error={kit.error} />}
      {error && <ErrorNotice error={error} />}

      {kit.data && !kit.data.available && (
        <p className="text-muted-foreground rounded-md border border-dashed px-3 py-2 text-xs">
          이 설치에는 키트가 들어 있지 않습니다 — 서버 번들로 깐 설치에서 받을 수 있습니다.
        </p>
      )}

      {kit.data?.available && (
        <>
          <ol className="list-decimal space-y-3 pl-5 text-xs">
            <li className="space-y-1.5">
              <p>
                <b>받아서 PC 에 풉니다</b>(처음 한 번 — 예: <span className="font-mono">D:\sp-pipeline</span>
                ). 이 서버와 같은 판입니다. <b>파이썬이 들어 있어</b> 따로 깔 것이 없습니다 —
                zip 안에서 바로 열지 말고 「압축 풀기」 로 푼 폴더에서 씁니다.
              </p>
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={busy}
                onClick={() => download(kit.data!.filename)}
              >
                <Download className="mr-1 size-3.5" />
                {kit.data.filename} ({megabytes(kit.data.size_bytes)})
              </Button>
            </li>
            <li className="space-y-1.5">
              <p>
                <b>이 플랫폼을 PC 에 등록합니다.</b> 아래를 누르면 이 플랫폼용 토큰이 하나 생기고,
                등록 정보가 복사됩니다.
              </p>
              {user?.is_system_admin && (
                <label className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    className="size-4"
                    checked={definitions}
                    onChange={(event) => setDefinitions(event.target.checked)}
                  />
                  정의(타입 · 속성)까지 넣을 수 있게 — 시스템 관리자만
                </label>
              )}
              <div className="flex items-center gap-2">
                <Button type="button" size="sm" disabled={busy} onClick={register}>
                  <ClipboardCopy className="mr-1 size-3.5" />이 PC 에 등록 정보 복사
                </Button>
                {copied === 'ok' && (
                  <span className="text-xs">복사했습니다 — 이제 3 으로.</span>
                )}
                {copied === 'fail' && (
                  <span className="text-destructive text-xs">
                    복사하지 못했습니다 — 브라우저가 클립보드를 막았습니다. 다시 누르세요.
                  </span>
                )}
              </div>
            </li>
            <li>
              푼 폴더의 <b className="font-mono">install.cmd</b> 를 <b>더블클릭</b>합니다. 처음이면
              설치까지 하고, 이미 설치했으면 이 플랫폼만 더합니다. Claude Desktop 이 켜져 있으면
              먼저 끄라고 합니다 — 켜진 채로 넣으면 안 보일 수 있습니다. 작업 폴더는{' '}
              <span className="font-mono">내 사용자 폴더\온톨로지작업</span> 에 생깁니다.
            </li>
            <li>
              <b>Claude Desktop 을 켭니다</b>(켜져 있었다면 작업 표시줄 아이콘 → 종료 후 다시). 도구
              목록에 <span className="font-mono">sp-pipeline</span> 이 보이면 대화에서 「○○ 자료
              작업을 시작하자」 고 말합니다 — AI 가 차례를 안내합니다. 안 보이면 같은 폴더의{' '}
              <b className="font-mono">check.cmd</b> 를 더블클릭 — 어디서 막혔는지 보여 줍니다.
            </li>
          </ol>

          <p className="text-muted-foreground text-xs">
            <b>플랫폼이 여럿이면</b> 각 플랫폼 화면에서 2 · 3 을 한 번씩 — 앞에 등록한 것은 남고,
            이 플랫폼은 <span className="font-mono">{APP_SLUG}</span> 라는 이름으로 더해집니다(같은
            이름이 이미 다른 주소로 있으면 — 개발판 · 운영판처럼 — 덮지 않고 주소를 붙인 이름으로 따로).
            작업을 만들 때 어느 플랫폼에 넣을지 AI 가 묻습니다. <b>적용은 그 플랫폼 화면의 「작업」 에서</b>{' '}
            사람이 「적용」 을 눌러 합니다 — AI 가 미리 보기 뒤에 그 링크를 줍니다. 「이 PC 에 등록」
            을 누를 때마다 토큰이 하나씩 생기니, 안 쓰는 것은 아래 토큰 목록에서 지웁니다.
          </p>

          <details className="text-xs">
            <summary className="cursor-pointer">명령으로 하려면(Gemini CLI · 다른 작업 폴더)</summary>
            <div className="mt-2 space-y-1.5">
              <p className="text-muted-foreground">
                푼 폴더의 명령 창에서 — 토큰은 위에서 발급한 것(macOS · Linux 는 앞을{' '}
                <span className="font-mono">python3 sp_setup.py</span> 로). Gemini CLI 면 끝을{' '}
                <span className="font-mono">--write-gemini</span> 로, 작업 폴더를 다른 곳에 두려면{' '}
                <span className="font-mono">--work-root "D:\온톨로지작업"</span> 을 더합니다.
              </p>
              <Snippet text={setupCommand(token, server)} label="명령" />
            </div>
          </details>
        </>
      )}
    </section>
  )
}
