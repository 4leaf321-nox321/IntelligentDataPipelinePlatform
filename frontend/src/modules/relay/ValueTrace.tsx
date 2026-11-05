/**
 * 값의 출처(거꾸로 보기, ADR 0001 §6) — 대상 객체 하나의 값이 **어느 원천 행 · 칸 · 레시피 규칙**에서
 * 왔나. 칸 대응 표가 「이 칸은 어느 열에서」 를 말한다면, 이것은 「이 객체의 이 값은 원천 몇 행의 무엇에서」 를
 * 말한다.
 *
 * 쓰는 자리는 대조다 — 「다름」 이 나오면 기준 값 · 규칙 · 원천의 원래 값을 나란히 본다. 셋이 규칙대로
 * 맞는데 대상이 다르면 대상에서 바뀐 것이고(보정 계획), 규칙이 원천 값을 대상과 다른 모양으로 만들면
 * 레시피 탓이다. 대조 절의 식별자를 클릭하면 여기로 와서 바로 조회한다(`request`).
 *
 * 서버가 읽기만 한다(`GET …/trace`). 기준 실행의 원천 표 · 레시피 사본이 없으면 지금 미러 · 지금
 * 레시피로 찾고 그렇다고 말한다 — 그 말을 그대로 보인다(`problems`).
 */

import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { Loader2, Search } from 'lucide-react'

import { relayApi, traceStateLabel } from '@/modules/relay/api'
import type { TraceField, ValueTrace as Trace } from '@/modules/relay/api'
import { Fact } from '@/modules/relay/PipelineRuns'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { Alert, AlertDescription } from '@/shared/components/ui/alert'
import { Button } from '@/shared/components/ui/button'
import { Input } from '@/shared/components/ui/input'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/shared/components/ui/table'
import { shownDateTime } from '@/shared/lib/datetime'
import { cn } from '@/shared/lib/utils'

const SELECT = 'border-input bg-background h-8 rounded-md border px-2 text-sm'

const fail = (caught: unknown) => (caught instanceof Error ? caught : new Error('알 수 없는 오류'))

export interface TraceRequest {
  typeSlug: string
  key: string
  /** 같은 식별자를 다시 클릭해도 다시 조회하게 — 바뀔 때마다 조회한다. */
  nonce: number
}

function shown(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (Array.isArray(value)) return value.join(' · ')
  return typeof value === 'object' ? JSON.stringify(value) : String(value)
}

function Seen({ field }: { field: TraceField }) {
  if (!field.columns.length) return <span className="text-muted-foreground">—</span>
  if (!field.seen?.length) return <span className="text-muted-foreground">원천 행 없음</span>
  return (
    <ul className="space-y-0.5">
      {field.seen.map((one) => (
        <li key={one.value}>
          <span className="font-mono">{one.value === '' ? '(빈 값)' : one.value}</span>
          <span className="text-muted-foreground"> — {one.rows.join(' · ')}행</span>
        </li>
      ))}
    </ul>
  )
}

function Result({ trace }: { trace: Trace }) {
  const differs = new Set(trace.reconcile?.fields ?? [])
  const columns = Object.keys(trace.rows[0]?.values ?? {})
  return (
    <div className="space-y-3" data-testid="value-trace-result">
      <div className="space-y-1">
        <p className="text-sm">
          <span className="font-mono">{trace.type_slug}</span> ·{' '}
          <span className="font-mono">{trace.key}</span>
          {trace.label && <span className="ml-2 font-medium">{trace.label}</span>}
          {!trace.found && <span className="text-destructive ml-2">기준 출력에 없음</span>}
        </p>
        <dl className="space-y-1">
          <Fact label="기준">
            {trace.run ? `${trace.run} · 적용 ${shownDateTime(trace.applied_at)}` : '아직 적용한 것 없음'}
          </Fact>
          <Fact label="원천 행">
            {trace.basis} · {trace.rows_total}행{trace.note && ` · 변환기: ${trace.note}`}
          </Fact>
          <Fact label="레시피">{trace.recipe_basis}</Fact>
          {trace.reconcile && (
            <Fact label="마지막 대조">
              <span className={cn(trace.reconcile.state !== 'same' && 'text-destructive font-medium')}>
                {traceStateLabel(trace.reconcile.state)}
              </span>
              {differs.size > 0 && ` — 다른 칸: ${[...differs].join(' · ')}`}
              {trace.reconcile.at && (
                <span className="text-muted-foreground"> ({shownDateTime(trace.reconcile.at)})</span>
              )}
              {trace.reconcile.stale && (
                <span className="text-muted-foreground">
                  {' '}
                  — 지금 기준을 적용하기 전의 대조입니다. 다시 대조하면 지금 기준으로 견줍니다.
                </span>
              )}
            </Fact>
          )}
        </dl>
      </div>

      {trace.problems.length > 0 && (
        <Alert>
          <AlertDescription>
            <ul className="list-disc space-y-0.5 pl-4">
              {trace.problems.map((one) => (
                <li key={one}>{one}</li>
              ))}
            </ul>
          </AlertDescription>
        </Alert>
      )}

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>칸</TableHead>
            <TableHead>기준 값</TableHead>
            <TableHead>레시피 규칙</TableHead>
            <TableHead>원천의 원래 값</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {trace.fields.map((field) => (
            <TableRow key={field.name} data-testid={`trace-field-${field.name}`}>
              <TableCell className="font-mono">
                {field.name}
                {differs.has(field.name) && (
                  <span className="text-destructive ml-1 font-sans text-xs">다름</span>
                )}
              </TableCell>
              <TableCell className="whitespace-normal">{shown(field.value)}</TableCell>
              <TableCell className="text-muted-foreground whitespace-normal text-xs">
                {field.rule}
              </TableCell>
              <TableCell className="whitespace-normal text-xs">
                <Seen field={field} />
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>

      {trace.rows.length > 0 && (
        <div className="space-y-1">
          <p className="text-sm font-medium">
            원천 행{' '}
            <span className="text-muted-foreground font-normal">
              {trace.rows.length < trace.rows_total
                ? `— ${trace.rows_total}행 중 앞의 ${trace.rows.length}행`
                : `— ${trace.rows_total}행`}
            </span>
          </p>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="text-right">행</TableHead>
                {columns.map((column) => (
                  <TableHead key={column} className="font-mono">
                    {column}
                  </TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {trace.rows.map((row) => (
                <TableRow key={row.row}>
                  <TableCell className="text-right">{row.row}</TableCell>
                  {columns.map((column) => (
                    <TableCell key={column} className="font-mono text-xs">
                      {row.values[column] || '—'}
                    </TableCell>
                  ))}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      {trace.relations.length > 0 && (
        <div className="space-y-1">
          <p className="text-sm font-medium">닿는 관계</p>
          <ul className="space-y-0.5 text-sm">
            {trace.relations.map((one) => (
              <li key={`${one.direction}-${one.relation}-${one.other}`}>
                <span className="text-muted-foreground">{one.direction === 'out' ? '→' : '←'}</span>{' '}
                <span className="font-mono">{one.relation}</span>{' '}
                <span className="font-mono">{one.other}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

export function ValueTraceSection({
  slug,
  types,
  request,
}: {
  slug: string
  /** 레시피가 만드는 타입 — 고르는 목록. */
  types: string[]
  request?: TraceRequest | null
}) {
  const [typeSlug, setTypeSlug] = useState(types[0] ?? '')
  const [key, setKey] = useState('')
  const [trace, setTrace] = useState<Trace | null>(null)
  const [error, setError] = useState<Error | null>(null)
  const [busy, setBusy] = useState(false)
  const here = useRef<HTMLElement>(null)

  const lookup = async (wanted: string, which: string) => {
    if (!wanted || !which.trim()) return
    setBusy(true)
    setError(null)
    try {
      setTrace(await relayApi.trace(slug, wanted, which.trim()))
    } catch (caught) {
      setTrace(null)
      setError(fail(caught))
    } finally {
      setBusy(false)
    }
  }

  useEffect(() => {
    if (!request) return
    setTypeSlug(request.typeSlug)
    setKey(request.key)
    here.current?.scrollIntoView?.({ behavior: 'smooth', block: 'start' })
    void lookup(request.typeSlug, request.key)
    // 대조 절에서 클릭한 식별자만 본다 — 입력이 바뀔 때마다 조회하지 않는다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [request])

  const submit = (event: FormEvent) => {
    event.preventDefault()
    void lookup(typeSlug, key)
  }

  return (
    <section ref={here} className="space-y-2" aria-labelledby="value-trace-title">
      <h2 id="value-trace-title" className="text-base font-semibold">
        값의 출처
      </h2>
      <p className="text-muted-foreground text-sm">
        대상 객체 하나를 골라, 그 값이 원천 몇 행의 어느 칸에서 어떤 레시피 규칙으로 만들어졌는지
        조회합니다. 대조의 「다름」 · 「빠짐」 의 까닭을 가리는 데 씁니다. 대조 절의 식별자를 클릭해도
        됩니다. 아무것도 보내지 않습니다.
      </p>
      <form className="flex flex-wrap items-center gap-2" onSubmit={submit}>
        <select
          aria-label="타입"
          className={SELECT}
          value={typeSlug}
          onChange={(event) => setTypeSlug(event.target.value)}
        >
          {types.map((one) => (
            <option key={one} value={one}>
              {one}
            </option>
          ))}
        </select>
        <Input
          aria-label="식별자"
          className="h-8 w-56"
          placeholder="식별자(key)"
          value={key}
          onChange={(event) => setKey(event.target.value)}
        />
        <Button size="sm" type="submit" disabled={busy || !typeSlug || !key.trim()}>
          {busy ? <Loader2 className="size-4 animate-spin" /> : <Search className="size-4" />}
          출처 조회
        </Button>
      </form>
      <ErrorNotice error={error} />
      {trace && <Result trace={trace} />}
    </section>
  )
}
