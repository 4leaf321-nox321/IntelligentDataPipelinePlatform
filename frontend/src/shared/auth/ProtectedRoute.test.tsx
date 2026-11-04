/**
 * 비밀번호 변경 화면은 「돌아갈 곳」 이 아니다.
 *
 * 변경 뒤 로그아웃되는 순간 가드가 그 자리를 기억해 두고, 새 비밀번호로 들어오면 또 변경 화면을
 * 띄웠다(실측 — 첫 설치마다 걸린다). 바꿀 필요가 없으면 거기 있어도 홈으로 보낸다.
 */
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

const auth = vi.hoisted(() => ({ status: 'authenticated', user: { must_change_password: false } }))
vi.mock('@/shared/auth/AuthContext', () => ({ useAuth: () => auth }))

import { ProtectedRoute } from './ProtectedRoute'

function App({ at }: { at: string }) {
  return (
    <MemoryRouter initialEntries={[at]}>
      <Routes>
        <Route element={<ProtectedRoute />}>
          <Route path="/" element={<p>홈</p>} />
          <Route path="/force-password-change" element={<p>비밀번호를 바꿔 주세요</p>} />
          <Route path="/notices" element={<p>공지</p>} />
        </Route>
      </Routes>
    </MemoryRouter>
  )
}

describe('ProtectedRoute', () => {
  it('바꿀 필요가 없으면 변경 화면에 있어도 홈으로', () => {
    auth.user.must_change_password = false
    render(<App at="/force-password-change" />)
    expect(screen.getByText('홈')).toBeInTheDocument()
  })

  it('바꿔야 하면 어디에 있든 변경 화면으로', () => {
    auth.user.must_change_password = true
    render(<App at="/notices" />)
    expect(screen.getByText('비밀번호를 바꿔 주세요')).toBeInTheDocument()
  })
})
