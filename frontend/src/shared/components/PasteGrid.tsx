/**
 * 표로 붙여넣기 — **엑셀과 오가는 자리.**
 *
 * 빈 상자에 붙여넣게 하면 **무엇을 적어야 하는지 알 방법이 없다.** 열이 몇 개인지, 이름이
 * 무엇인지, 어느 것이 필수인지를 안내 문구로 설명하는 것보다 표로 보여 주는 편이 짧다. 붙인
 * 뒤에 한 칸을 고칠 수 있다는 것도 크다 — 상자에 붙이면 틀린 칸 하나 때문에 엑셀로 돌아간다.
 *
 * ## 표는 껍데기다
 *
 * 서버로 갈 때는 **헤더 한 줄 + 탭으로 갈린 줄들**이 된다. 표는 사람이 보기 좋으라고 있는
 * 것이지 새 형식이 아니다 — 형식이 둘이 되면 두 곳이 갈라지고, 그때 「표로는 되는데 파일로는
 * 안 되는」 상태가 생긴다.
 *
 * (같은 장치가 MatNexus 에도 있다. 그쪽 것을 보고 이 저장소의 규약으로 옮겼다.)
 */

import { useId, useMemo, useState } from 'react'
import type { ClipboardEvent, ReactNode } from 'react'
import { Check, Copy, ListChecks, Plus, Search, Trash2 } from 'lucide-react'

import { Badge } from '@/shared/components/ui/badge'
import { Button } from '@/shared/components/ui/button'
import { Input } from '@/shared/components/ui/input'
import { Popover, PopoverContent, PopoverTrigger } from '@/shared/components/ui/popover'
import { copyText } from '@/shared/lib/clipboard'
import { cn } from '@/shared/lib/utils'

/**
 * 표의 한 열.
 *
 * **사람이 읽는 이름(`label`)을 크게, 서버로 가는 글자(`header`)를 그 아래 회색으로** 둔다.
 * 머리에 `weight` 만 있으면 그것이 「무게」 인지 「무게중심」 인지 표가 말해 주지 못하고,
 * 반대로 「무게」 만 있으면 엑셀에서 채워 올 때 어떤 열 이름으로 적어야 할지 알 수 없다.
 */
export interface GridColumn {
  key: string
  /** 서버로 가는 글자 — 속성 키. 헤더 줄에 이것이 적힌다. */
  header: string
  /** 사람이 읽는 이름. 없으면 `header` 를 그대로 보여 준다. */
  label?: string
  /** 회색 줄에 덧붙는 설명 — 단위·고를 값 같은 것. */
  help?: string | null
  /** 비면 그 줄이 안 나간다. 머리에 「필수」 로 보인다. */
  required?: boolean
  /**
   * 고를 수 있는 값 — **등록된 것에서 고르게 한다.**
   *
   * 빈 칸에 이름을 손으로 치게 하면 「낙하시험」 과 「낙하 시험」 이 섞이고, 그 줄은 저장
   * 자리에서 「못 찾음」 이 된다. 목록을 주면 드롭다운으로 고를 수 있고(붙여넣기는 그대로
   * 되므로 엑셀 흐름도 살아 있다), **목록에 없는 값은 칸에 표시된다.**
   */
  options?: string[]
  /**
   * **줄마다 고를 것이 다른 열** — 그 줄의 값으로 목록을 낸다.
   *
   * 불량 유형이 그렇다: 유형은 시험 항목에 붙어 있어 낙하 시험과 방수 시험이 서로 다른
   * 목록을 낸다. 그런 열은 `options` 로 고정할 수 없다(고정하면 남의 유형이 목록에 뜨고,
   * 그것을 고를 수 있으면 저장 자리에서 막힌다).
   *
   * `options` 보다 이것이 우선한다. 목록이 줄마다 달라 **드롭다운(`datalist`)은 붙지
   * 않는다** — 고르기는 목록 단추가 한다.
   */
  optionsFor?: (row: string[]) => string[]
  /**
   * 목록은 **고르기 편하려고** 있을 뿐이다 — 새 이름도 맞다(검사하지 않는다).
   *
   * 불량 유형 목록을 **늘리는** 표가 그렇다. 등록된 것만 맞다고 보면 새 유형을 적는 칸이
   * 늘 붉게 뜨고, 그러면 사람은 그 표시를 아예 안 읽게 된다. 이미 쓰는 이름을 단추로 고를
   * 수 있게 해 두는 것이 목록의 일이다(철자가 갈리지 않게).
   */
  free?: boolean
  /** 한 칸에 여럿을 적는 열(`·` 로 이어 적는다). 검사도 토큰마다 한다. */
  multi?: boolean
  /** 읽기용 칸 — 고쳐도 서버로 안 간다(무엇을 고치는 줄인지 알려 주는 자리). */
  readOnly?: boolean
  /**
   * 체크 열 — **켜고 끄는 칸.** 여러 항목을 고르는 축(자동화 · 시험 대체)은 항목마다 열을
   * 두고 체크한다. 한 칸에 「전처리 자동화 · 실행 자동화」 를 적게 하면 이름을 외워야 하고,
   * 엑셀에서도 채우기 어렵다.
   *
   * 저장되는 값은 `O` 또는 빈 칸이고, 엑셀에서 붙여 넣은 `O` · `예` · `v` · `1` 도 켜진
   * 것으로 읽는다 — 사람마다 다른 글자를 쓰기 때문이다.
   */
  check?: boolean
}

/** 빈 줄 하나로 시작한다 — 표가 아예 비어 있으면 어디에 붙여야 할지 모른다. */
export function emptyRows(columns: GridColumn[], count = 1): string[][] {
  return Array.from({ length: count }, () => columns.map(() => ''))
}

/** 표를 서버가 받는 모양으로 — **헤더 한 줄 + 탭으로 갈린 줄들.** 다 빈 줄은 뺀다. */
export function toLines(columns: GridColumn[], rows: string[][]): string[] {
  return [
    columns.map((column) => column.header).join('\t'),
    ...rows
      .filter((row) => row.some((cell) => cell.trim()))
      .map((row) => columns.map((_, at) => (row[at] ?? '').trim()).join('\t')),
  ]
}

/** 채워진 줄 수 — 「n줄 보내기」 에 적는다. */
export function filledRows(rows: string[][]): number {
  return rows.filter((row) => row.some((cell) => cell.trim())).length
}

/** 이 줄에서 고를 수 있는 것 — 줄마다 다른 열(`optionsFor`)이 먼저다. */
export function choicesOf(column: GridColumn, row: string[] = []): string[] | undefined {
  return column.optionsFor ? column.optionsFor(row) : column.options
}

/** 체크 열에서 「켜짐」 으로 읽는 글자 — 사람마다 다른 것을 쓴다. */
const CHECKED = new Set(['o', 'O', '예', 'v', 'V', 'y', 'Y', '1', 'true', 'x', 'X', '✓'])

export function isChecked(raw: string): boolean {
  return CHECKED.has(raw.trim())
}

/** 이 칸의 값이 **등록된 것**인가. 목록이 없는 열은 늘 맞다고 본다. */
export function unknownParts(column: GridColumn, raw: string, row: string[] = []): string[] {
  const choices = choicesOf(column, row)
  // 체크 열은 무엇을 적어도 켜짐 · 꺼짐으로만 읽는다 — 틀릴 수가 없다.
  // 목록이 고르기용인 열(`free`)도 검사하지 않는다 — 새 이름을 적는 것이 그 표의 일이다.
  if (column.check || column.free || !choices || !raw.trim()) return []
  const allowed = new Set(choices.map((one) => one.trim()))
  const parts = column.multi ? raw.split('·') : [raw]
  return parts.map((one) => one.trim()).filter((one) => one && !allowed.has(one))
}

/** 한 칸에 적힌 이름들 — `·` 로 갈린다. */
function partsOf(raw: string): string[] {
  return raw
    .split('·')
    .map((one) => one.trim())
    .filter(Boolean)
}

/**
 * 여럿 고르는 칸의 목록 — **여러 개를 눌러 담는다.**
 *
 * `<datalist>` 만 있으면 하나를 고르는 순간 칸이 그 이름으로 바뀌어, 둘째를 담을 길이
 * 없다(실측). 여기서는 눌러서 켜고 끄고, 칸에는 정의된 순서로 `·` 로 이어 적힌다 —
 * 고른 순서대로 두면 같은 줄이 사람마다 다르게 보인다.
 *
 * **포털로 띄운다**(`Popover`). 창 안에서 `absolute` 로 띄운 목록은 창 경계에서 잘린다.
 */
function MultiPick({
  name,
  options,
  value,
  onChange,
}: {
  /** 무엇을 고르는 칸인지 — 단추의 이름표에 들어간다. */
  name: string
  options: string[]
  value: string
  onChange: (next: string) => void
}) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const picked = useMemo(() => new Set(partsOf(value)), [value])
  const needle = query.trim().toLowerCase()
  const shown = needle
    ? options.filter((one) => one.toLowerCase().includes(needle))
    : options

  function toggle(one: string) {
    const next = new Set(picked)
    if (next.has(one)) next.delete(one)
    else next.add(one)
    // 목록에 없는 이름(손으로 친 것)은 지우지 않고 뒤에 남긴다 — 고르기가 남의 입력을
    // 없애면 안 된다.
    const extra = [...next].filter((each) => !options.includes(each))
    onChange([...options.filter((each) => next.has(each)), ...extra].join(' · '))
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          type="button"
          size="icon"
          variant="outline"
          className="size-7 shrink-0"
          aria-label={`${name} 고르기`}
        >
          <ListChecks className="size-3" />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-64 p-0">
        <div className="flex items-center gap-1 border-b px-2">
          <Search className="text-muted-foreground size-3 shrink-0" />
          <Input
            autoFocus
            aria-label={`${name} 검색`}
            placeholder="이름으로 찾기"
            className="h-8 border-0 px-1 text-xs shadow-none focus-visible:ring-0"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </div>
        <div className="max-h-60 overflow-auto p-1">
          {shown.length === 0 && (
            <p className="text-muted-foreground px-2 py-3 text-xs">찾는 이름이 없습니다.</p>
          )}
          {shown.map((one) => (
            <button
              key={one}
              type="button"
              aria-pressed={picked.has(one)}
              className="hover:bg-accent flex w-full items-center gap-2 rounded px-2 py-1 text-left text-xs"
              onClick={() => toggle(one)}
            >
              <span
                className={cn(
                  'flex size-3.5 shrink-0 items-center justify-center rounded-[3px] border',
                  picked.has(one) && 'bg-primary border-primary text-primary-foreground',
                )}
              >
                {picked.has(one) && <Check className="size-2.5" />}
              </span>
              <span className="truncate">{one}</span>
            </button>
          ))}
        </div>
        <div className="flex items-center justify-between border-t px-2 py-1">
          <span className="text-muted-foreground text-xs">{picked.size}개 고름</span>
          <Button size="xs" variant="ghost" onClick={() => onChange('')}>
            비우기
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  )
}

export function PasteGrid({
  columns,
  rows,
  onRows,
  header,
}: {
  columns: GridColumn[]
  rows: string[][]
  onRows: (next: string[][]) => void
  /** 표 위에 둘 것 — 무엇을 두는지는 쓰는 쪽이 정한다. */
  header?: ReactNode
}) {
  /** 복사 결과. **눌렀는데 아무 일도 안 일어나면 됐는지 알 수 없다.** */
  const [copied, setCopied] = useState<'yes' | 'no' | null>(null)
  // 드롭다운 목록의 id — 한 화면에 표가 둘 있어도 섞이지 않게.
  const gridId = useId()
  const wrongCount = rows.reduce(
    (sum, row) =>
      sum +
      columns.reduce((each, column, at) => each + unknownParts(column, row[at] ?? '', row).length, 0),
    0,
  )

  async function copy() {
    // 헤더까지 함께 — 엑셀에 붙이면 그대로 표가 되고, 채워서 다시 붙여넣으면 열이 맞는다.
    try {
      await copyText(toLines(columns, rows).join('\n'))
      setCopied('yes')
      window.setTimeout(() => setCopied(null), 2000)
    } catch {
      setCopied('no')
    }
  }

  function edit(row: number, column: number, value: string) {
    const next = rows.map((one) => [...one])
    while (next.length <= row) next.push(columns.map(() => ''))
    next[row][column] = value
    // 마지막 줄에 뭔가 적으면 빈 줄을 하나 더 — 「줄 추가」 를 누르러 가지 않아도 계속 친다.
    if (row === next.length - 1 && value.trim()) next.push(columns.map(() => ''))
    onRows(next)
  }

  /** 엑셀에서 복사한 범위를 **붙여넣은 칸부터** 채운다. 한 칸짜리는 그냥 둔다. */
  function paste(event: ClipboardEvent<HTMLInputElement>, row: number, column: number) {
    const text = event.clipboardData.getData('text/plain')
    if (!text.includes('\t') && !text.includes('\n')) return
    event.preventDefault()

    const pasted = text
      .replace(/\r/g, '')
      .split('\n')
      .filter((line, index, all) => line.trim() || index < all.length - 1)
      .map((line) => line.split('\t'))
    const next = rows.map((one) => [...one])
    pasted.forEach((cells, atRow) => {
      const target = row + atRow
      while (next.length <= target) next.push(columns.map(() => ''))
      cells.forEach((cell, atColumn) => {
        const index = column + atColumn
        if (index < columns.length) next[target][index] = cell.trim()
      })
    })
    if (next[next.length - 1].some((cell) => cell.trim())) next.push(columns.map(() => ''))
    onRows(next)
  }

  return (
    <div className="space-y-2">
      {header}

      {/* 목록이 있는 열의 드롭다운. `<datalist>` 라 붙여넣기 · 손 입력을 막지 않는다.
          **줄마다 다른 열은 여기 없다** — 목록이 줄마다 달라 하나로 못 묶는다. */}
      {columns
        .filter((column) => !column.optionsFor && column.options?.length)
        .map((column) => (
          <datalist key={column.key} id={`${gridId}-${column.key}`}>
            {(column.options ?? []).map((one) => (
              <option key={one} value={one} />
            ))}
          </datalist>
        ))}

      {wrongCount > 0 && (
        <p className="text-destructive text-sm">
          등록된 이름이 아닌 칸이 {wrongCount}개 있습니다 — 붉은 칸을 고치세요. 그 줄은 저장되지
          않습니다.
        </p>
      )}

      {/* **접지 않고 옆으로 민다.** 열이 좁아 머리가 줄바꿈되면 표가 세로로 부풀고, 그때
          무엇이 어느 칸인지 읽기 어려워진다 — 차라리 가로로 스크롤한다. */}
      <div className="max-h-[50vh] overflow-auto rounded-md border">
        <table className="w-max min-w-full text-xs">
          {/* **머리는 불투명해야 한다.** 반투명하게 두면 스크롤할 때 아래 줄의 글자가
              머리를 통해 비쳐 겹쳐 보인다(실측). 배경을 깔고 그 위에 회색을 얹는다. */}
          <thead className="bg-background sticky top-0 z-20">
            <tr>
              <th className="text-muted-foreground bg-muted/40 w-8 border-b px-1 py-1 text-right font-normal">
                #
              </th>
              {columns.map((column) => (
                <th
                  key={column.key}
                  className={cn(
                    'bg-muted/40 border-b px-1.5 py-1 align-top font-medium',
                    // 체크 열은 좁게 — 이름이 길어도 열이 넓어질 이유가 없다.
                    column.check ? 'w-16 text-center' : 'min-w-36 text-left',
                  )}
                >
                  <span className="block whitespace-nowrap">
                    {column.label ?? column.header}
                    {column.required && (
                      <Badge variant="outline" className="ml-1 px-1 py-0 text-[10px]">
                        필수
                      </Badge>
                    )}
                  </span>
                  {/* 서버로 가는 글자 — 엑셀에서 채워 올 때 이 이름으로 적는다. */}
                  <span className="text-muted-foreground block truncate text-[10px] font-normal">
                    <span className="font-mono">{column.header}</span>
                    {column.help && ` · ${column.help}`}
                  </span>
                </th>
              ))}
              <th className="bg-muted/40 w-8 border-b" />
            </tr>
          </thead>
          <tbody>
            {rows.map((row, atRow) => (
              // 줄 번호는 자리이지 값이 아니다 — 지웠다 넣어도 그 자리의 칸은 같은 칸이다.
              // eslint-disable-next-line react/no-array-index-key
              <tr key={atRow} className="border-t">
                <td className="text-muted-foreground px-1 text-right tabular-nums">{atRow + 1}</td>
                {columns.map((column, atColumn) => {
                  const wrong = unknownParts(column, row[atColumn] ?? '', row)
                  const choices = choicesOf(column, row)
                  if (column.check) {
                    return (
                      <td key={column.key} className="w-16 p-0.5 text-center">
                        <input
                          type="checkbox"
                          aria-label={`${atRow + 1}번 줄 ${column.header}`}
                          checked={isChecked(row[atColumn] ?? '')}
                          onChange={(event) =>
                            edit(atRow, atColumn, event.target.checked ? 'O' : '')
                          }
                        />
                      </td>
                    )
                  }
                  return (
                    <td key={column.key} className="min-w-36 p-0.5">
                      <div className="flex items-center gap-1">
                        <Input
                          className={cn(
                            'h-7 text-xs',
                            // **모르는 이름은 칸에서 보인다.** 저장을 눌러 봐야 아는 것은
                            // 붙여넣기 스무 줄에서 스무 번 왕복하게 만든다.
                            wrong.length > 0 && 'border-destructive text-destructive',
                            column.readOnly && 'text-muted-foreground bg-muted/40',
                          )}
                          aria-label={`${atRow + 1}번 줄 ${column.header}`}
                          aria-invalid={wrong.length > 0}
                          title={
                            wrong.length > 0 ? `등록된 이름이 아닙니다: ${wrong.join(', ')}` : undefined
                          }
                          // 목록이 있으면 **드롭다운**으로 고른다 — 손으로 치는 것도 막지
                          // 않는다(엑셀에서 붙여넣기가 그대로 되어야 한다).
                          list={
                          column.options && !column.optionsFor
                            ? `${gridId}-${column.key}`
                            : undefined
                        }
                          readOnly={column.readOnly}
                          value={row[atColumn] ?? ''}
                          onPaste={(event) => paste(event, atRow, atColumn)}
                          onChange={(event) => edit(atRow, atColumn, event.target.value)}
                        />
                        {/* 여럿 적는 칸은 **눌러서 담는다.** 드롭다운으로는 하나를 고르는
                            순간 칸이 그 이름으로 바뀌어 둘째를 담을 길이 없다. */}
                        {column.multi && choices?.length && !column.readOnly ? (
                          <MultiPick
                            name={`${atRow + 1}번 줄 ${column.header}`}
                            options={choices}
                            value={row[atColumn] ?? ''}
                            onChange={(next) => edit(atRow, atColumn, next)}
                          />
                        ) : null}
                      </div>
                    </td>
                  )
                })}
                <td className="p-0.5">
                  {rows.length > 1 && (
                    <Button
                      size="icon"
                      variant="ghost"
                      className="size-6"
                      aria-label={`${atRow + 1}번 줄 제거`}
                      onClick={() => onRows(rows.filter((_, at) => at !== atRow))}
                    >
                      <Trash2 className="size-3" />
                    </Button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Button
          size="xs"
          variant="outline"
          onClick={() => onRows([...rows, columns.map(() => '')])}
        >
          <Plus className="mr-1 size-3" />줄 추가
        </Button>
        {/* **엑셀에서 채워 오는 길.** 빈 표라도 헤더가 복사되므로, 붙여 놓고 채운 뒤 다시
            가져오면 열 이름이 맞는다. */}
        <Button size="xs" variant="outline" onClick={() => void copy()}>
          {copied === 'yes' ? <Check className="mr-1 size-3" /> : <Copy className="mr-1 size-3" />}
          {copied === 'yes' ? '복사했습니다' : '엑셀로 복사'}
        </Button>
        <span className="text-muted-foreground text-xs">
          엑셀에서 범위를 복사해 <b>아무 칸에나 붙여넣으면</b> 그 자리부터 채워집니다.
        </span>
      </div>

      {/* **브라우저가 복사를 막는 자리가 있다.** 그때는 글자를 내어 주고 직접 복사하게 한다 —
          단추만 눌리고 아무 일도 안 일어나는 것이 가장 나쁘다. */}
      {copied === 'no' && (
        <div className="space-y-1 rounded-md border border-amber-500/40 bg-amber-500/5 p-2">
          <p className="text-xs">브라우저가 복사를 막았습니다. 아래를 골라서 직접 복사하세요.</p>
          <textarea
            readOnly
            aria-label="복사할 표"
            className="border-input bg-background h-24 w-full rounded-md border p-1.5 font-mono text-xs"
            value={toLines(columns, rows).join('\n')}
            onFocus={(event) => event.currentTarget.select()}
          />
        </div>
      )}
    </div>
  )
}
