/**
 * 아이콘 선택 — **눈으로 훑고, 말로 찾는다.**
 *
 * 이름만 적는 칸으로 두면(`Wrench` 를 치는) 무엇이 있는지 알 수 없고, 오타는 조용히
 * 기본 그림으로 떨어진다 — 고른 사람은 자기가 고른 줄 안다. 그래서 격자로 펼친다.
 *
 * 두 길을 함께 낸다: **치는 것**(이름의 일부를 알 때)과 **훑는 것**(무엇이 있는지
 * 모를 때). `SearchablePicker` 와 같은 판단인데, 여기는 고를 것이 「그림」 이라
 * 한 줄 목록이 아니라 격자여야 한다.
 */

import { useMemo, useState } from 'react'
import { Search } from 'lucide-react'

import { ICON_CATALOG, ICON_GROUPS, iconOf, matches } from '@/shared/icons'
import { Button } from '@/shared/components/ui/button'
import { Input } from '@/shared/components/ui/input'
import { Popover, PopoverContent, PopoverTrigger } from '@/shared/components/ui/popover'
import { cn } from '@/shared/lib/utils'

interface Props {
  value: string
  onChange: (name: string) => void
  /** 고르개를 열지 않고 지금 것만 보여 줄 때(읽기 전용 화면). */
  disabled?: boolean
  id?: string
}

export function IconPicker({ value, onChange, disabled = false, id }: Props) {
  const [query, setQuery] = useState('')
  const Current = iconOf(value)

  const groups = useMemo(
    () =>
      ICON_GROUPS.map((group) => ({
        group,
        items: ICON_CATALOG[group].filter((one) => matches(one, query)),
      })).filter((one) => one.items.length > 0),
    [query],
  )

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        {/* 고른 것을 **크게** 보여 준다 — 격자 안의 테두리만으로는 어느 것이 골라졌는지
            멀리서 안 보인다. */}
        <span className="bg-muted flex size-9 shrink-0 items-center justify-center rounded-md">
          <Current className="size-5" />
        </span>
        <div className="relative flex-1">
          <Search className="text-muted-foreground absolute top-1/2 left-2 size-4 -translate-y-1/2" />
          <Input
            id={id}
            className="pl-8"
            value={query}
            disabled={disabled}
            placeholder="공구·시험·부서처럼 뜻으로 검색"
            onChange={(event) => setQuery(event.target.value)}
          />
        </div>
      </div>

      <div className="max-h-56 overflow-y-auto rounded-md border p-2">
        {groups.length === 0 ? (
          <p className="text-muted-foreground p-2 text-sm">
            맞는 그림이 없습니다. 다른 말로 찾아 보세요 — 없으면 「격자」 로 두면 됩니다.
          </p>
        ) : (
          groups.map(({ group, items }) => (
            <div key={group} className="mb-2 last:mb-0">
              <p className="text-muted-foreground px-1 py-1 text-xs">{group}</p>
              <div className="grid grid-cols-8 gap-1">
                {items.map((one) => (
                  <button
                    key={one.name}
                    type="button"
                    disabled={disabled}
                    aria-label={one.label}
                    aria-pressed={value === one.name}
                    title={`${one.label} (${one.name})`}
                    className={cn(
                      'flex items-center justify-center rounded-md border p-2',
                      value === one.name
                        ? 'border-primary bg-primary/10'
                        : 'hover:bg-muted border-transparent',
                    )}
                    onClick={() => onChange(one.name)}
                  >
                    <one.Icon className="size-4" />
                  </button>
                ))}
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  )
}

/**
 * 좁은 자리(생성 줄)를 위한 단추 하나 — 눌러야 격자가 뜬다.
 *
 * 만들 때부터 고르게 두는 이유: **나중에 고치는 것은 안 한다.** 만들고 나면 그
 * 타입은 이미 쓰이기 시작하고, 그 뒤에 사이드바를 다듬으러 다시 오는 사람은 없다.
 */
export function IconPickerButton({
  value,
  onChange,
  id,
}: {
  value: string
  onChange: (name: string) => void
  id?: string
}) {
  const [open, setOpen] = useState(false)
  const Current = iconOf(value)
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button id={id} variant="outline" aria-label="아이콘 선택" className="w-20">
          <Current className="size-4" />
          <span className="text-muted-foreground text-xs">선택</span>
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-96 p-3">
        <IconPicker
          value={value}
          onChange={(next) => {
            onChange(next)
            setOpen(false)
          }}
        />
      </PopoverContent>
    </Popover>
  )
}
