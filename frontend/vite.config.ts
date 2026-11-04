import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'
import type { Plugin } from 'vite'

import pkg from './package.json' with { type: 'json' }

const root = fileURLToPath(new URL('.', import.meta.url))

/**
 * 개발 서버에서도 **서버가 심는 것과 같은 meta** 를 심는다.
 *
 * 배포에서는 백엔드가 index.html 에 `<meta name="app-name">` 들을 넣어 화면이 자기 이름과
 * 켠 확장을 안다(backend/app/main.py). Vite 는 index.html 을 직접 주므로 그 자리가 비고,
 * 그러면 개발 화면은 늘 틀의 기본 이름에 확장 없음이 된다 — 그래서 같은 `backend/.env` 를
 * 읽어 같은 meta 를 넣는다. 화면 코드는 두 경우를 구별하지 않는다.
 */
function devIdentity(): Plugin {
  const envFile = path.resolve(root, '../backend/.env')
  const read = (): Record<string, string> => {
    if (!fs.existsSync(envFile)) return {}
    const out: Record<string, string> = {}
    for (const line of fs
      .readFileSync(envFile, 'utf-8')
      .replace(/^\uFEFF/, '')
      .split(/\r?\n/)) {
      const m = /^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*)\s*$/.exec(line)
      if (m) out[m[1]] = m[2].replace(/^["']|["']$/g, '')
    }
    return out
  }
  const escape = (s: string) =>
    s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
  return {
    name: 'dev-identity',
    apply: 'serve',
    transformIndexHtml(html) {
      const env = read()
      const pairs: [string, string | undefined][] = [
        ['app-name', env.APP_NAME],
        ['app-slug', env.APP_SLUG],
        ['app-tagline', env.APP_TAGLINE],
        // **씨앗일 뿐이다.** 켜짐은 DB 가 정하므로(관리 › 서버) 앱이
        // `/api/server/enabled-extensions` 에서 받는다 — 여기 값은 첫 그림에만 쓰인다.
        ['app-extensions', env.EXTENSIONS],
        // HWAX 포털로 로그인 — 서버와 같은 규칙: 받는 쪽(PORTAL_JWKS_URL)이 있을 때만.
        ['app-portal-launch', env.PORTAL_JWKS_URL ? env.PORTAL_LAUNCH_URL : ''],
      ]
      const tags = pairs
        .filter(([, v]) => v)
        .map(([k, v]) => `<meta name="${k}" content="${escape(v!)}" />`)
      let out = html.replace('<head>', ['<head>', ...tags].join('\n    '))
      if (env.APP_NAME)
        out = out.replace(/<title>.*?<\/title>/, `<title>${escape(env.APP_NAME)}</title>`)
      return out
    },
  }
}

export default defineConfig({
  plugins: [react(), tailwindcss(), devIdentity()],
  // **상대 주소로 굽는다.** 배포마다 접두어(`/plm/`)가 다른데 그것을 빌드에 굽으면 이미지가
  // 접두어마다 하나씩 필요하다. 상대 주소 + 서버가 심는 `<base href>` 로 어느 접두어에서도 뜬다.
  base: './',
  // **이 빌드가 몇 번인지 굽는다.** 서버가 다른 버전이면 화면이 그것을 말할 수
  // 있어야 한다 — 개발과 운영이 가까운 포트를 쓰는 동안, 프론트가 옛 서버에 붙어
  // 있어도 아무 데도 티가 안 난다.
  define: { __APP_VERSION__: JSON.stringify(`v${pkg.version}`) },
  resolve: {
    alias: { '@': path.resolve(root, 'src') },
  },
  server: {
    port: 5250,
    strictPort: true,
    // 개발 중에만 필요하다. 배포에서는 백엔드 한 프로세스가 SPA 까지 서빙하므로
    // 프론트는 항상 같은 출처의 /api 를 부른다 — API 주소를 빌드에 굽지 않는다.
    proxy: {
      // localhost 가 아니라 127.0.0.1 — 백엔드는 0.0.0.0(IPv4)에 바인딩하는데
      // 윈도우의 localhost 는 ::1 로 먼저 풀려 연결이 거부된다.
      //
      // **8081 이다. 운영이 8080 을 쓴다.** 둘이 같으면 개발 백엔드를 내린 순간
      // 이 프록시가 운영 설치본에 그대로 붙고, 화면은 그 사실을 말하지 않는다.
      '/api': { target: 'http://127.0.0.1:8081', changeOrigin: true },
    },
  },
  build: {
    outDir: 'dist',
    // **plotly 한 덩어리가 4MB 다**(`shared/charts/LazyPlot`). 히트맵·사케이·3차원을
    // 그리는 화면을 열 때만 받으므로 첫 화면에는 안 실린다 — 그래서 이 경고는 늘
    // 켜져 있게 되고, 늘 켜진 경고는 **진짜 커진 덩어리를 가린다.** 한도를 그 위로
    // 올리되 이유를 여기 적어 둔다.
    chunkSizeWarningLimit: 4200,
  },
})
