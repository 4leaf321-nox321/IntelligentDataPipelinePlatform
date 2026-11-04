/**
 * 첨부 패널에서 지키는 것 — **서버가 이미지로 읽은 것만 그림으로**, 누르면 크게 보고 넘긴다,
 * 사진만 받는 칸은 고르는 창도 사진만, 여러 장 · 붙여넣기로 올리고 실패한 것은 이름을 붙여 알린다.
 */

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { Attachment } from '@/modules/files/api'

const attachmentApi = vi.hoisted(() => ({
  list: vi.fn(),
  upload: vi.fn(),
  download: vi.fn(),
  remove: vi.fn(),
}))
vi.mock('@/modules/files/api', () => ({
  attachmentApi,
  attachmentPaths: {
    thumbnail: (id: string) => `/attachments/${id}/thumbnail`,
    content: (id: string) => `/attachments/${id}/content`,
  },
}))
// 사진 받기는 blob 주소로 — 여기서는 주소만 돌려준다(받은 경로를 그대로 적어 무엇을 받았는지 본다).
vi.mock('@/shared/hooks/useBlobUrl', () => ({
  useBlobUrl: (path: string | null) => ({ url: path ? `blob:${path}` : null, error: null }),
}))

function row(id: string, name: string, isImage: boolean): Attachment {
  return {
    id,
    owner_table: 'objects',
    owner_id: 'o1',
    owner_field: 'photo',
    original_name: name,
    content_type: isImage ? 'image/png' : 'image/png',
    size_bytes: 2048,
    sha256: id.repeat(8),
    created_at: '2026-10-03T00:00:00Z',
    is_image: isImage,
    width: isImage ? 640 : null,
    height: isImage ? 480 : null,
  }
}

async function show(props: Record<string, unknown> = {}) {
  const { AttachmentList } = await import('@/modules/files/AttachmentList')
  render(
    <AttachmentList ownerTable="objects" ownerId="o1" ownerField="photo" title="사진" {...props} />,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('첨부 패널의 사진', () => {
  it('서버가 이미지로 읽은 것만 미리보기로 띄운다 — 이미지라고 적힌 가짜는 파일 줄로', async () => {
    attachmentApi.list.mockResolvedValue([row('a', '현장.png', true), row('b', '가짜.png', false)])
    await show()

    const real = await screen.findByRole('img', { name: '현장.png' })
    expect(real.getAttribute('src')).toBe('blob:/attachments/a/thumbnail')
    // 「image/png」 라고 적혔어도 서버가 못 읽은 것은 그림으로 안 띄운다.
    expect(screen.queryByRole('img', { name: '가짜.png' })).toBeNull()
    expect(screen.getByRole('button', { name: '가짜.png 다운로드' })).toBeTruthy()
  })

  it('누르면 원본을 크게 보고 앞뒤로 넘긴다', async () => {
    attachmentApi.list.mockResolvedValue([row('a', '앞.png', true), row('b', '뒤.png', true)])
    await show()
    await userEvent.click(await screen.findByRole('button', { name: '앞.png 크게 보기' }))

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText('앞.png')).toBeTruthy()
    expect(within(dialog).getByRole('img').getAttribute('src')).toBe('blob:/attachments/a/content')
    await userEvent.click(within(dialog).getByRole('button', { name: '다음 사진' }))
    expect(within(dialog).getByText('뒤.png')).toBeTruthy()
  })
})

describe('올리기', () => {
  it('사진만 받는 칸은 고르는 창도 사진만이고, 여러 장을 차례로 올린다 — 실패한 것은 이름과 함께', async () => {
    attachmentApi.list.mockResolvedValue([])
    attachmentApi.upload
      .mockResolvedValueOnce(row('a', '하나.png', true))
      .mockRejectedValueOnce(new Error('이 칸은 이미지만 받습니다'))
    await show({ canEdit: true, accept: 'image' })

    expect(await screen.findByRole('button', { name: /사진 업로드/ })).toBeTruthy()
    const input = document.querySelector('input[type="file"]') as HTMLInputElement
    expect(input.accept).toBe('image/png,image/jpeg,image/gif,image/webp')
    expect(input.multiple).toBe(true)

    const files = [
      new File(['a'], '하나.png', { type: 'image/png' }),
      new File(['b'], '성적서.pdf', { type: 'application/pdf' }),
    ]
    fireEvent.change(input, { target: { files } })
    await waitFor(() => expect(attachmentApi.upload).toHaveBeenCalledTimes(2))
    expect(await screen.findByText(/성적서\.pdf: 이 칸은 이미지만 받습니다/)).toBeTruthy()
  })

  it('상한을 넘는 파일은 보내지 않고 이름과 상한을 알린다 — 나머지는 올린다', async () => {
    attachmentApi.list.mockResolvedValue([])
    attachmentApi.upload.mockResolvedValue(row('a', '작은.txt', false))
    await show({ canEdit: true })
    const input = (await screen.findByRole('heading', { name: /사진/ }))
      .closest('section')!
      .querySelector('input[type="file"]') as HTMLInputElement
    const big = new File(['x'], '큰영상.mp4', { type: 'video/mp4' })
    Object.defineProperty(big, 'size', { value: 600 * 1024 * 1024 })
    const small = new File(['y'], '작은.txt', { type: 'text/plain' })
    fireEvent.change(input, { target: { files: [big, small] } })
    await waitFor(() => expect(attachmentApi.upload).toHaveBeenCalledTimes(1))
    expect(attachmentApi.upload.mock.calls[0][0]).toMatchObject({ file: small })
    // 상한은 서버에 묻는다 — 못 물으면 기본값(500MB).
    expect(await screen.findByText(/큰영상\.mp4: 한 파일은 500MB 까지입니다/)).toBeTruthy()
  })

  it('패널을 누르고 붙여넣으면 올린다 — 화면 캡처를 바로', async () => {
    attachmentApi.list.mockResolvedValue([])
    attachmentApi.upload.mockResolvedValue(row('a', 'image.png', true))
    await show({ canEdit: true })
    const panel = (await screen.findByRole('heading', { name: /사진/ })).closest('section')!
    const shot = new File(['x'], 'image.png', { type: 'image/png' })
    fireEvent.paste(panel, { clipboardData: { files: [shot] } })
    await waitFor(() => expect(attachmentApi.upload).toHaveBeenCalledTimes(1))
    expect(attachmentApi.upload.mock.calls[0][0]).toMatchObject({
      ownerTable: 'objects',
      ownerId: 'o1',
      ownerField: 'photo',
      file: shot,
    })
  })

  it('고칠 수 없으면 붙여넣어도 안 올린다', async () => {
    attachmentApi.list.mockResolvedValue([])
    await show({ canEdit: false })
    const panel = (await screen.findByRole('heading', { name: /사진/ })).closest('section')!
    fireEvent.paste(panel, {
      clipboardData: { files: [new File(['x'], 'image.png', { type: 'image/png' })] },
    })
    expect(attachmentApi.upload).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: /업로드/ })).toBeNull()
  })
})

describe('그 밖의 첨부', () => {
  it('칸별 목록이 이미 보인 자리는 빼고, 자리 없는 것 · 지운 칸의 것은 남긴다', async () => {
    // 자리를 안 주면 서버는 전부를 준다 — 안 빼던 때는 칸별 첨부가 두 번 섰다(2026-10-08).
    attachmentApi.list.mockResolvedValue([
      { ...row('a', '도면.pdf', false), owner_field: 'drawing' },
      { ...row('b', '메모.txt', false), owner_field: null },
      { ...row('c', '옛 칸.pdf', false), owner_field: 'removed' },
    ])
    await show({ ownerField: undefined, title: '그 밖의 첨부', excludeFields: ['drawing'] })
    expect(await screen.findByText('메모.txt')).toBeInTheDocument()
    expect(screen.getByText('옛 칸.pdf')).toBeInTheDocument()
    expect(screen.queryByText('도면.pdf')).not.toBeInTheDocument()
    expect(attachmentApi.list).toHaveBeenCalledWith('objects', 'o1', undefined)
  })
})
