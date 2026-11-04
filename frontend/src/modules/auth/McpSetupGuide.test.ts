/**
 * 토큰을 넣는 법은 도구마다 모양이 다르지만 **주소 · 토큰 · 다리는 같다.**
 */
import { describe, expect, it } from 'vitest'

import { mcpUrlFrom, setupSnippets } from './McpSetupGuide'

describe('mcpUrl', () => {
  const at = (origin: string) => {
    const url = new URL(origin)
    return { protocol: url.protocol, hostname: url.hostname, origin: url.origin, port: url.port }
  }

  it('앱 포트로 직접 들어오면 **앱 포트 +2 · 접두어 없이**', () => {
    // 접두어가 있으면 늘 `<origin>/<slug>/mcp` 를 보여 줬다 — 그 길은 앱에 없다(404).
    expect(mcpUrlFrom(at('http://10.0.0.5:3030'), null, '/rootdesign')).toBe(
      'http://10.0.0.5:3032/mcp',
    )
    expect(mcpUrlFrom(at('http://10.0.0.5:8080'), null, '')).toBe('http://10.0.0.5:8082/mcp')
  })

  it('표준 포트로 들어오면 프록시 뒤다 — 접두어를 붙인다', () => {
    expect(mcpUrlFrom(at('https://portal.example'), null, '/rootdesign')).toBe(
      'https://portal.example/rootdesign/mcp',
    )
    expect(mcpUrlFrom(at('http://portal.example'), null, '')).toBe('http://portal.example/mcp')
  })

  it('서버가 알려 준 주소가 있으면 그것이 정본이다', () => {
    expect(mcpUrlFrom(at('https://portal.example:8443'), 'https://mcp.example/plm/mcp')).toBe(
      'https://mcp.example/plm/mcp',
    )
  })
})

describe('setupSnippets', () => {
  const url = 'https://portal.example/plm/mcp'

  it('토큰이 네 도구에 다 들어간다', () => {
    const s = setupSnippets('standardplatform_pat_abc', url)
    for (const text of [s.claudeCode, s.desktop, s.codex, s.gemini]) {
      expect(text).toContain('Bearer standardplatform_pat_abc')
      expect(text).toContain(url)
      expect(text).toContain('mcp-remote')
    }
  })

  it('토큰이 없으면 자리표시자', () => {
    const s = setupSnippets(null, url)
    expect(s.claudeCode).toContain('‹발급받은_토큰›')
    expect(s.claudeCode).not.toContain('undefined')
  })

  it('Claude Desktop 항목은 붙여넣을 수 있는 JSON 조각이고 Gemini 도 같다', () => {
    const s = setupSnippets('t', url)
    const parsed = JSON.parse(`{${s.desktop}}`)
    const entry = Object.values(parsed)[0] as {
      command: string
      args: string[]
      env: Record<string, string>
    }
    expect(entry.command).toBe('npx')
    expect(entry.args).toContain(url)
    expect(entry.env.AUTH).toBe('Bearer t')
    expect(s.gemini).toBe(s.desktop)
  })

  it('Codex 는 TOML 두 표', () => {
    const s = setupSnippets('t', url)
    expect(s.codex).toMatch(/^\[mcp_servers\.[a-z0-9]+\]\n/)
    expect(s.codex).toContain('.env]\nAUTH = "Bearer t"')
  })
})
