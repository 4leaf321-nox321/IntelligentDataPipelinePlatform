/**
 * 목록의 파일 칸 — 사진이면 **미리보기만** 받고(원본이 아니다), 아니면 아이콘 · 이름. 빈 칸은
 * 다른 칸처럼 「—」.
 */

import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

const seen = vi.hoisted(() => ({ paths: [] as string[] }))
vi.mock('@/shared/hooks/useBlobUrl', () => ({
  useBlobUrl: (path: string | null) => {
    if (path) seen.paths.push(path)
    return { url: path ? `blob:${path}` : null, error: null }
  },
}))

describe('목록의 파일 칸', () => {
  it('사진이면 미리보기 주소를 받고, 여럿이면 나머지 수를 적는다', async () => {
    const { AttachmentCell } = await import('@/modules/files/AttachmentCell')
    render(
      <AttachmentCell
        cell={{ count: 3, first: { id: 'a1', original_name: '앞면.jpg', is_image: true } }}
      />,
    )
    expect(screen.getByRole('img', { name: '앞면.jpg' })).toHaveAttribute(
      'src',
      'blob:/attachments/a1/thumbnail',
    )
    expect(seen.paths).toEqual(['/attachments/a1/thumbnail']) // 원본(content)은 받지 않는다
    expect(screen.getByText('+2')).toBeInTheDocument()
  })

  it('이미지가 아니면 그림을 띄우지 않고 이름을 적는다', async () => {
    seen.paths = []
    const { AttachmentCell } = await import('@/modules/files/AttachmentCell')
    render(
      <AttachmentCell
        cell={{ count: 1, first: { id: 'f1', original_name: '성적서.pdf', is_image: false } }}
      />,
    )
    expect(screen.getByText('성적서.pdf')).toBeInTheDocument()
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
    expect(seen.paths).toEqual([])
  })

  it('빈 칸은 「—」', async () => {
    const { AttachmentCell } = await import('@/modules/files/AttachmentCell')
    render(<AttachmentCell cell={undefined} />)
    expect(screen.getByText('—')).toBeInTheDocument()
  })
})
