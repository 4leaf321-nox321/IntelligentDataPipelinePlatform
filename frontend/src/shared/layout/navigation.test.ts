/**
 * 사이드바가 지키는 규칙.
 *
 * 여기가 화면 목록의 정본이라, 이 파일이 틀리면 라우터와 권한이 함께 틀린다.
 */

import { describe, expect, it } from 'vitest'

import { DEFAULT_ICON, iconOf } from '@/shared/icons'

import type { DynamicGroup } from '@/shared/layout/navigation'
import {
  NAV_GROUPS,
  canSee,
  itemHref,
  pendingItems,
  visibleGroups,
} from '@/shared/layout/navigation'

const ADMIN = { isSystemAdmin: true, isAnyManager: true }
const MANAGER = { isSystemAdmin: false, isAnyManager: true }
const MEMBER = { isSystemAdmin: false, isAnyManager: false }

describe('사이드바', () => {
  it('멤버에게는 관리 그룹이 통째로 안 보인다', () => {
    const titles = visibleGroups(MEMBER).map((group) => group.title)
    expect(titles).not.toContain('관리')
    expect(titles).not.toContain('내 부서')
  })

  it('부서 관리자는 내 부서를 보되 전사 관리는 못 본다', () => {
    const titles = visibleGroups(MANAGER).map((group) => group.title)
    expect(titles).toContain('내 부서')
    expect(titles).not.toContain('관리')
  })

  it('시스템 관리자는 전부 본다', () => {
    expect(visibleGroups(ADMIN).length).toBe(NAV_GROUPS.length)
  })

  it('빈 그룹은 제목까지 사라진다', () => {
    // **제목만 남으면 「뭔가 안 나온다」 로 읽힌다.** 항목이 하나도 없는 그룹은
    // 있으나 마나가 아니라 오해를 만든다.
    for (const group of visibleGroups(MEMBER)) {
      expect(group.items.length).toBeGreaterThan(0)
    }
  })

  it('부서 스코프 항목은 슬러그를 받아 주소를 만든다', () => {
    const home = NAV_GROUPS[0].items[0]
    expect(itemHref(home, 'material-lab')).toBe('/w/material-lab')
  })

  it('미구현 항목은 단계와 설명을 함께 든다', () => {
    // 라우터가 이 값으로 stub 을 그린다 — 비어 있으면 stub 이 「—」 만 보여 준다.
    for (const item of pendingItems()) {
      expect(item.phase, `${item.label} 에 단계가 없습니다`).toBeTruthy()
      expect(item.summary, `${item.label} 에 설명이 없습니다`).toBeTruthy()
    }
  })

  it('everyone 은 누구에게나 보인다', () => {
    expect(canSee(undefined, MEMBER)).toBe(true)
    expect(canSee('everyone', MEMBER)).toBe(true)
  })

  it('정의가 만든 묶음이 홈 바로 아래 선다', () => {
    // **동선이 곧 순서여야 한다.** 사람이 밟는 차례대로 놓으면 「다음에 어디로」
    // 를 안 묻는다.
    const groups = visibleGroups(ADMIN, [
      {
        slug: 'domain',
        label: '도메인',
        icon: '',
        audience: 'everyone',
        items: [{ label: '부품', icon: '', to: '/o/part', slug: 'part' }],
      },
    ])
    expect(groups[1].title).toBe('도메인')
    expect(groups[1].items[0].to).toBe('/o/part')
  })

  it('타입이 고른 그림이 사이드바에 선다', () => {
    // 전부 같은 네모면 타입이 열둘쯤 될 때 사이드바가 **이름을 한 자씩 읽어야 하는
    // 목록**이 된다. 눈은 모양을 먼저 잡는데, 모양이 하나뿐이면 그 능력이 안 쓰인다.
    const groups = visibleGroups(ADMIN, [
      {
        slug: 'domain',
        label: '도메인',
        icon: '',
        audience: 'everyone',
        items: [
          { label: '공구', icon: 'Wrench', to: '/o/tool', slug: 'tool' },
          { label: '시험', icon: 'FlaskConical', to: '/o/test', slug: 'test' },
          // **모르는 이름이면 기본으로 떨어진다** — 메뉴가 통째로 안 뜨는 것보다 낫다.
          { label: '옛것', icon: '없는이름', to: '/o/old', slug: 'old' },
        ],
      },
    ])
    const [tool, test, old] = groups[1].items
    expect(tool.icon).toBe(iconOf('Wrench'))
    expect(test.icon).not.toBe(tool.icon)
    expect(old.icon).toBe(DEFAULT_ICON)
  })

  it('기록 타입은 「기록」 표를 단다 — 이름만으로는 찾는 것인지 쌓이는 건인지 안 갈린다', () => {
    const groups = visibleGroups(ADMIN, [
      {
        slug: 'quality',
        label: '품질',
        icon: '',
        audience: 'everyone',
        items: [
          { label: '개발모델', icon: '', to: '/o/model', slug: 'model', usage: 'axis' },
          { label: '시장 서비스', icon: '', to: '/o/case', slug: 'case', usage: 'log' },
        ],
      },
    ])
    expect(groups[1].items.map((item) => item.badge)).toEqual([undefined, '기록'])
  })

  it('정의가 없으면 정적 메뉴 그대로다', () => {
    // **못 불러와도 사이드바는 선다.** 통째로 비면 나갈 길까지 사라진다.
    expect(visibleGroups(ADMIN, [])).toEqual(visibleGroups(ADMIN))
  })

  it('확장은 제 이름의 그룹으로 홈 바로 아래에 선다', () => {
    // 확장을 켜는 일은 **기능 한 덩어리가 열리는 일**이다 — 「확장」 이라는 바구니 안에
    // 숨기면 사용자는 그 말로 자기 일을 찾지 않는다.
    const groups = visibleGroups(
      ADMIN,
      [],
      [{ title: '설비 관리', items: [{ label: '설비', icon: DEFAULT_ICON, to: '/ext/equip' }] }],
    )
    expect(groups[1].title).toBe('설비 관리')
    expect(visibleGroups(ADMIN, [], []).map((one) => one.title)).not.toContain('설비 관리')
  })

  it('제목이 같으면 기존 그룹에 합친다', () => {
    // 같은 이름의 그룹이 둘 서면 사람은 어느 쪽에 무엇이 있는지 매번 다시 찾는다.
    const groups = visibleGroups(
      ADMIN,
      [],
      [
        {
          title: '관리',
          items: [{ label: '설비 설정', icon: DEFAULT_ICON, to: '/ext/equip/설정' }],
        },
      ],
    )
    const admin = groups.filter((one) => one.title === '관리')
    expect(admin).toHaveLength(1)
    expect(admin[0].items.map((one) => one.label)).toContain('설비 설정')
  })
})

describe('두 단계 묶음', () => {
  const dynamic = (
    slug: string,
    label: string,
    parent: string | null,
    items: string[],
  ): DynamicGroup => ({
    slug,
    label,
    icon: '',
    audience: 'everyone',
    parent,
    items: items.map((one) => ({ label: one, icon: '', to: `/o/${one}`, slug: one })),
  })

  it('상위 묶음 아래에 자식이 들어간다 — 제 항목도 함께 선다', () => {
    const groups = visibleGroups({ isSystemAdmin: false, isAnyManager: false }, [
      dynamic('baseinfo', '설계 기준정보', null, ['spec']),
      dynamic('machine', '기계 부품', 'baseinfo', ['part', 'material']),
      dynamic('sim', '시뮬레이션', null, ['run']),
    ])
    const top = groups.find((one) => one.title === '설계 기준정보')
    expect(top?.items.map((one) => one.label)).toEqual(['spec'])
    expect(top?.children?.map((one) => one.title)).toEqual(['기계 부품'])
    expect(top?.children?.[0].items.map((one) => one.label)).toEqual(['part', 'material'])
    // 자식은 맨 위 목록에 또 서지 않는다 — 두 번 보이면 어느 쪽을 눌러야 하는지 묻게 된다.
    expect(groups.some((one) => one.title === '기계 부품')).toBe(false)
    expect(groups.some((one) => one.title === '시뮬레이션')).toBe(true)
  })

  it('상위가 목록에서 **뒤에 와도** 그 아래로 들어간다', () => {
    // 순서는 sort_order · 이름이 정한다 — 상위 묶음을 나중에 만들면 자식이 먼저 온다.
    // 한 바퀴로 붙이면 그때 자식이 맨 위에 그대로 서서 「정했는데 안 바뀐다」 가 된다.
    const groups = visibleGroups({ isSystemAdmin: false, isAnyManager: false }, [
      dynamic('machine', '기계 부품', 'baseinfo', ['part']),
      dynamic('baseinfo', '설계 기준정보', null, []),
    ])
    expect(groups.some((one) => one.title === '기계 부품')).toBe(false)
    const top = groups.find((one) => one.title === '설계 기준정보')
    expect(top?.children?.map((one) => one.title)).toEqual(['기계 부품'])
  })

  it('상위가 목록에 없으면 맨 위로 세운다 — 기다리다 사라지는 것이 가장 나쁘다', () => {
    const groups = visibleGroups({ isSystemAdmin: false, isAnyManager: false }, [
      dynamic('machine', '기계 부품', 'gone', ['part']),
    ])
    expect(groups.some((one) => one.title === '기계 부품')).toBe(true)
  })

  it('접힘 열쇠는 slug 다 — 이름이 겹쳐도 한쪽만 접힌다', () => {
    const groups = visibleGroups({ isSystemAdmin: false, isAnyManager: false }, [
      dynamic('a', '부품', null, ['x']),
      dynamic('b', '부품', null, ['y']),
    ])
    const keys = groups.filter((one) => one.title === '부품').map((one) => one.foldKey)
    expect(keys).toEqual(['a', 'b'])
  })
})
