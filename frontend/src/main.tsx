import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import App from '@/App'
import '@/index.css'
import { listenForStaleChunks } from '@/shared/newBuild'

// **새 판이 나와 옛 조각을 못 받으면 한 번 새로 고친다**(`shared/newBuild`).
listenForStaleChunks()

const container = document.getElementById('root')
if (!container) throw new Error('#root 를 찾을 수 없습니다')

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
