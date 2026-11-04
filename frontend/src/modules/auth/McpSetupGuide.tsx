/**
 * 발급한 토큰을 AI 도구에 넣는 법 — Claude Code · Claude Desktop · Codex CLI · Gemini CLI.
 *
 * 토큰을 발급한 자리에서 바로 보여 준다. 사람은 토큰을 받아 들고 「이걸 어디에 넣지」 에서 막히고,
 * 그때 README 를 찾아가는 사람은 드물다. 발급 전에도 예시 형식은 보여 주되 토큰 자리는 자리표시자.
 *
 * 네 도구 모두 **같은 다리(`npx mcp-remote`)** 로 붙인다(ReportArchive 와 같은 방식). 이 MCP 는
 * HTTP 서버인데 Claude Desktop · Gemini CLI 의 설정 파일은 stdio(command) 서버만 받고, http 주소
 * (`--allow-http`)와 사내 인증서(`NODE_OPTIONS=--use-system-ca`)를 한 방식으로 다루려는 뜻이다.
 * 토큰은 env 로 넣고 args 의 `${AUTH}` 는 mcp-remote 가 치환한다 — 셸이 먼저 풀지 않게 작은따옴표.
 */

import { useState } from 'react'

import { Button } from '@/shared/components/ui/button'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/shared/components/ui/tabs'
import { APP_SLUG } from '@/shared/branding'
import { PUBLIC_PATH } from '@/shared/base'
import { copyText } from '@/shared/lib/clipboard'

const TOKEN_PLACEHOLDER = '‹발급받은_토큰›'

/**
 * 이 설치의 MCP 주소 — **MCP 는 앱과 다른 프로세스 · 다른 포트다.**
 *
 * ⚠️ 접두어(`/<slug>`)가 있으면 늘 `<origin><접두어>/mcp` 를 보여 줬다. 그 길은 **메인 서버의
 *    nginx 에만** 있다(조각이 `/<slug>/mcp` 를 MCP 로 넘긴다) — 앱 포트로 직접 들어온 사람에게는
 *    앱에 없는 길을 알려 준 것이고, 앱은 404 로 답한다. 토큰을 받은 사람은 그 주소로 등록하고
 *    「MCP 서버가 없다」 를 MCP 쪽에서 찾는다(실측).
 *
 * 그래서 **어디로 들어왔는지**로 가른다:
 * - 표준 포트(80 · 443) → 앞에 프록시가 있다 → `<origin><접두어>/mcp`
 * - 앱 포트로 직접 → `<호스트>:<앱포트+2>/mcp` (접두어 없음)
 * - 그 둘로 안 되는 구성(표준이 아닌 포트의 프록시)에서는 서버가 `<meta name="app-mcp">` 로
 *   정본을 내려 준다(`.env` 의 `MCP_PUBLIC_URL`).
 */
export interface Where {
  protocol: string
  hostname: string
  origin: string
  port: string
}

/** 규칙만 — 창 없이도 부를 수 있게 갈라 뒀다(시험이 두 모양을 다 본다). */
export function mcpUrlFrom(
  where: Where,
  told?: string | null,
  prefix: string = PUBLIC_PATH,
  dev = false,
): string {
  if (told) return told
  const { protocol, hostname, origin, port } = where
  if (dev) return `${protocol}//${hostname}:8082/mcp`
  if (!port || port === '80' || port === '443') return `${origin}${prefix}/mcp`
  return `${protocol}//${hostname}:${Number(port) + 2}/mcp`
}

export function mcpUrl(): string {
  // 브라우저 밖(시험)에서는 빈 주소 — 번들 검사가 「localhost:80…」 이 박힌 것을 막는다.
  if (typeof window === 'undefined') return '/mcp'
  return mcpUrlFrom(
    window.location,
    document.querySelector('meta[name="app-mcp"]')?.getAttribute('content'),
    PUBLIC_PATH,
    import.meta.env.DEV,
  )
}

export function setupSnippets(token: string | null, url = mcpUrl()) {
  const tok = token || TOKEN_PLACEHOLDER
  const name = APP_SLUG
  const bridgeArgs = ['-y', 'mcp-remote', url, '--allow-http', '--header', 'Authorization:${AUTH}']
  const env = { AUTH: `Bearer ${tok}`, NODE_OPTIONS: '--use-system-ca' }

  const claudeCode =
    `claude mcp add ${name} \\\n` +
    `  -e AUTH='Bearer ${tok}' \\\n` +
    `  -e NODE_OPTIONS=--use-system-ca \\\n` +
    `  -- npx -y mcp-remote ${url} --allow-http --header 'Authorization:\${AUTH}'`

  const desktop = `"${name}": ${JSON.stringify({ command: 'npx', args: bridgeArgs, env }, null, 2)}`

  const codex =
    `[mcp_servers.${name}]\n` +
    `command = "npx"\n` +
    `args = ${JSON.stringify(bridgeArgs)}\n\n` +
    `[mcp_servers.${name}.env]\n` +
    `AUTH = "Bearer ${tok}"\n` +
    `NODE_OPTIONS = "--use-system-ca"`

  return { claudeCode, desktop, codex, gemini: desktop }
}

/** 복사할 글 한 덩이 — 이 화면의 안내들이 같은 모양을 쓴다(정제 도구 키트도). */
export function Snippet({ text, label }: { text: string; label: string }) {
  const [done, setDone] = useState<'ok' | 'fail' | null>(null)
  return (
    <div className="space-y-2">
      <pre className="bg-muted overflow-x-auto rounded px-2 py-2 font-mono text-[11px] whitespace-pre">
        {text}
      </pre>
      <div className="flex items-center gap-2">
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={() =>
            copyText(text)
              .then(() => setDone('ok'))
              .catch(() => setDone('fail'))
          }
        >
          {label} 복사
        </Button>
        {done === 'ok' && <span className="text-muted-foreground text-xs">복사됨</span>}
        {done === 'fail' && (
          <span className="text-destructive text-xs">복사 실패 — 직접 선택해 복사하세요</span>
        )}
      </div>
    </div>
  )
}

export function McpSetupGuide({ token }: { token: string | null }) {
  const url = mcpUrl()
  const s = setupSnippets(token, url)
  return (
    <section className="space-y-3 rounded-md border p-4">
      <div>
        <h3 className="text-sm font-semibold">AI 도구 연결</h3>
        <p className="text-muted-foreground mt-1 text-xs">
          사용하는 도구의 탭을 선택해 복사하세요. 네 도구 모두 <b>Node.js</b> 가 있어야 합니다 —{' '}
          <span className="font-mono">npx mcp-remote</span> 다리로 붙습니다. MCP 주소:{' '}
          <span className="font-mono">{url}</span>
        </p>
      </div>
      {!token && (
        <div className="bg-muted/40 text-muted-foreground rounded-md border border-dashed px-3 py-2 text-xs">
          아직 토큰을 발급하지 않았습니다. 아래는 예시 형식이고 토큰 자리에{' '}
          <span className="font-mono">{TOKEN_PLACEHOLDER}</span> 이 들어 있습니다. 위에서 발급하면
          실제 토큰이 채워집니다.
        </div>
      )}
      <Tabs defaultValue="claude-code" className="w-full">
        <TabsList className="w-full flex-wrap justify-start">
          <TabsTrigger value="claude-code">Claude Code</TabsTrigger>
          <TabsTrigger value="desktop">Claude Desktop</TabsTrigger>
          <TabsTrigger value="gemini">Gemini CLI</TabsTrigger>
          <TabsTrigger value="codex">Codex CLI</TabsTrigger>
        </TabsList>
        <TabsContent value="claude-code" className="space-y-2">
          <p className="text-muted-foreground text-xs">
            터미널에 붙여넣으면 등록됩니다. 확인: <span className="font-mono">claude mcp list</span>
          </p>
          <Snippet text={s.claudeCode} label="명령" />
        </TabsContent>
        <TabsContent value="desktop" className="space-y-2">
          <p className="text-muted-foreground text-xs">
            설정 → 개발자 → 「설정 편집」으로{' '}
            <span className="font-mono">claude_desktop_config.json</span> 을 열고, 아래 항목을{' '}
            <span className="font-mono">{'"mcpServers": { }'}</span> 중괄호 안에 붙여넣은 뒤 Claude
            Desktop 을 완전히 종료했다가 다시 켭니다. 다른 항목이 이미 있으면 사이에 쉼표.
          </p>
          <Snippet text={s.desktop} label="항목" />
        </TabsContent>
        <TabsContent value="gemini" className="space-y-2">
          <p className="text-muted-foreground text-xs">
            <span className="font-mono">~/.gemini/settings.json</span> 의{' '}
            <span className="font-mono">{'"mcpServers": { }'}</span> 안에 붙여넣습니다(Claude
            Desktop 과 같은 모양). 확인: <span className="font-mono">gemini</span> 실행 후{' '}
            <span className="font-mono">/mcp</span>
          </p>
          <Snippet text={s.gemini} label="항목" />
        </TabsContent>
        <TabsContent value="codex" className="space-y-2">
          <p className="text-muted-foreground text-xs">
            <span className="font-mono">~/.codex/config.toml</span> 끝에 붙여넣습니다.
          </p>
          <Snippet text={s.codex} label="설정" />
        </TabsContent>
      </Tabs>
    </section>
  )
}
