import path from 'node:path'
import { fileURLToPath } from 'node:url'

import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

const root = fileURLToPath(new URL('.', import.meta.url))

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { '@': path.resolve(root, 'src') } },
  define: { __APP_VERSION__: JSON.stringify('v0.0.0-test') },
  test: {
    environment: 'happy-dom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    /**
     * **5초는 이 시험들에 짧다.** 마흔세 파일이 한꺼번에 도는데, 기계가 다른 일(백엔드
     * 시험 · 빌드)을 함께 하고 있으면 화면 한 번 그리는 데 그만큼 걸린다 — 그때 **매번
     * 다른 파일이** 「Test timed out in 5000ms」 로 떨어졌다(실측). 시험 내용과 상관없는
     * 실패는 곧 「또 그거겠지」 가 되고, 그러면 진짜 실패도 그렇게 읽힌다.
     *
     * 빠르기를 재는 시험은 여기 없다 — 늘려도 잃는 것이 없다.
     */
    testTimeout: 20_000,
    hookTimeout: 20_000,
  },
})
