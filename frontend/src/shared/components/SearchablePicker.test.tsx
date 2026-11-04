/**
 * 피커가 지키는 것 — **두 길이 모두 나 있는가.**
 *
 * 검색만 있으면 뭘 쳐야 할지 모르는 사람이 막히고, 목록만 있으면 아는 사람이
 * 스크롤을 해야 한다. 하나만 있는 피커는 반쪽이다.
 */

import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { SearchablePicker } from '@/shared/components/SearchablePicker'
import { Dialog, DialogContent, DialogTitle } from '@/shared/components/ui/dialog'
import type { PickerOption } from '@/shared/components/SearchablePicker'

const OPTIONS: PickerOption[] = [
  { value: 'mat-lab', label: '재료시험팀', hint: '개발본부 / 재료시험팀', keywords: 'mat-lab' },
  { value: 'qa-1', label: '품질팀', hint: '생산본부 / 품질팀' },
  { value: 'qa-2', label: '품질팀', hint: '개발본부 / 품질팀' },
  { value: 'gone', label: '해체된 팀', hint: '보관됨', disabledReason: '보관' },
]

function open() {
  return userEvent.click(screen.getByRole('combobox'))
}

describe('SearchablePicker', () => {
  it('열면 전부 보인다 — 무엇이 있는지 모르는 사람의 길', async () => {
    render(<SearchablePicker options={OPTIONS} value={null} onChange={vi.fn()} />)
    await open()
    expect(screen.getAllByRole('button', { name: /팀/ })).toHaveLength(4)
    // 몇 개 중 몇 개를 보고 있는지 말한다.
    expect(screen.getByText('4 / 4')).toBeInTheDocument()
  })

  it('이름의 일부로 걸러진다 — 아는 사람의 길', async () => {
    render(<SearchablePicker options={OPTIONS} value={null} onChange={vi.fn()} />)
    await open()
    await userEvent.type(screen.getByPlaceholderText('이름으로 검색'), '품질')
    expect(screen.getAllByRole('button', { name: /품질팀/ })).toHaveLength(2)
    expect(screen.getByText('2 / 4')).toBeInTheDocument()
  })

  it('화면에 없는 별칭·코드로도 걸린다', async () => {
    // 사람은 부서 이름 대신 주소를 기억하기도 한다. 그때 안 걸리면
    // 「없다」 고 결론 내리고 새로 만든다 — 그러면 같은 값이 둘로 갈린다.
    render(<SearchablePicker options={OPTIONS} value={null} onChange={vi.fn()} />)
    await open()
    await userEvent.type(screen.getByPlaceholderText('이름으로 검색'), 'mat-lab')
    expect(screen.getByRole('button', { name: /재료시험팀/ })).toBeInTheDocument()
  })

  it('같은 이름이 여럿이면 경로로 구별된다', async () => {
    // 「품질팀」 이 둘인데 이름만 보이면 어느 쪽인지 고를 수 없다.
    render(<SearchablePicker options={OPTIONS} value={null} onChange={vi.fn()} />)
    await open()
    expect(screen.getByText('생산본부 / 품질팀')).toBeInTheDocument()
    expect(screen.getByText('개발본부 / 품질팀')).toBeInTheDocument()
  })

  it('선택할 수 없는 줄은 이유를 적고, 눌러도 안 골라진다', async () => {
    // **비활성만 시키고 말 안 하면 버그로 읽힌다.**
    const onChange = vi.fn()
    render(<SearchablePicker options={OPTIONS} value={null} onChange={onChange} />)
    await open()
    expect(screen.getByText('보관')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /해체된 팀/ }))
    expect(onChange).not.toHaveBeenCalled()
  })

  it('선택하면 값을 넘기고 닫힌다', async () => {
    const onChange = vi.fn()
    render(<SearchablePicker options={OPTIONS} value={null} onChange={onChange} />)
    await open()
    await userEvent.click(screen.getByRole('button', { name: /재료시험팀/ }))
    expect(onChange).toHaveBeenCalledWith('mat-lab')
    expect(screen.queryByPlaceholderText('이름으로 검색')).not.toBeInTheDocument()
  })

  it('맞는 것이 없으면 그렇다고 말한다', async () => {
    // 빈 목록을 그냥 두면 「고장났나」 로 읽힌다.
    render(<SearchablePicker options={OPTIONS} value={null} onChange={vi.fn()} />)
    await open()
    await userEvent.type(screen.getByPlaceholderText('이름으로 검색'), '없는부서')
    expect(screen.getByText('맞는 것이 없습니다')).toBeInTheDocument()
  })

  it('전각·대소문자가 달라도 같게 본다', async () => {
    // 눈에 같아 보이는 값이 안 걸리면 사람은 없다고 결론 내린다.
    render(
      <SearchablePicker
        options={[{ value: 'a', label: 'ASTM E8' }]}
        value={null}
        onChange={vi.fn()}
      />,
    )
    await open()
    await userEvent.type(screen.getByPlaceholderText('이름으로 검색'), 'ａｓｔｍ')
    expect(screen.getByRole('button', { name: /ASTM E8/ })).toBeInTheDocument()
  })
})

describe('SearchablePicker — 창 안에서', () => {
  it('창(Dialog) 안에서도 목록이 잘리지 않고 뜨고, 고르면 닫힌다', async () => {
    // **창은 넘칠 때를 대비해 안쪽을 잘라 둔다.** 목록을 그 안에 그리면 긴 목록이 창
    // 경계에서 잘렸다(실측 2026-09-24) — 그래서 목록은 포털로 띄운다. 이 시험은
    // 「창 안에서도 목록이 보이고 고를 수 있나」 를 지킨다.
    const onChange = vi.fn()
    render(
      <Dialog open>
        <DialogContent>
          <DialogTitle>부서 선택</DialogTitle>
          <SearchablePicker options={OPTIONS} value={null} onChange={onChange} />
        </DialogContent>
      </Dialog>,
    )
    await open()
    // 목록이 창 바깥(포털)에 그려지고, 검색 칸에 바로 초점이 간다.
    expect(screen.getByPlaceholderText('이름으로 검색')).toHaveFocus()
    expect(screen.getAllByRole('button', { name: /팀/ })).toHaveLength(4)

    await userEvent.click(screen.getByRole('button', { name: /재료시험팀/ }))
    expect(onChange).toHaveBeenCalledWith('mat-lab')
    expect(screen.queryByPlaceholderText('이름으로 검색')).not.toBeInTheDocument()
  })

  it('제목이 있으면 그 아래로 모으고, 제목으로도 찾는다', async () => {
    // 이어진 것 너머의 칸은 걸음마다 제목 아래로 선다 — 「국가」 가 어느 걸음의 것인지.
    render(
      <SearchablePicker
        options={[
          { value: 'label', label: '이름' },
          { value: 'ref.vendor.country', label: '개발사 › 국가', group: '개발사 (기업)' },
          {
            value: 'ref.model.ref.task.project',
            label: '모델 › 과제 › 프로젝트',
            group: '모델 › 과제 (과제)',
          },
          { value: 'ref.model.ref.task.label', label: '모델 › 과제 › 이름', group: '모델 › 과제 (과제)' },
        ]}
        value="label"
        onChange={vi.fn()}
        ariaLabel="칸"
      />,
    )
    await userEvent.click(screen.getByRole('combobox', { name: '칸' }))
    expect(screen.getAllByText('모델 › 과제 (과제)')).toHaveLength(1)
    expect(screen.getByText('개발사 (기업)')).toBeInTheDocument()
    await userEvent.type(screen.getByPlaceholderText('이름으로 검색'), '과제 (과제')
    expect(screen.getAllByRole('button', { name: /모델 › 과제/ })).toHaveLength(2)
    expect(screen.queryByText('개발사 (기업)')).not.toBeInTheDocument()
  })
})
