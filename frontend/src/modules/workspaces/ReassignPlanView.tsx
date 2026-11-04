/**
 * 자료 이동(부서 통폐합)의 계획 · 적용을 읽는 자리 — **무엇이 몇 건 옮겨지나, 무엇이 막히나.**
 *
 * 통폐합 창과 작업 화면이 같은 것을 그린다. 창을 닫고 돌아와도 작업 화면에서 같은 계획을 읽고
 * 적용한다 — 한 부서 객체 20만 개면 적용이 90초 남짓이라 창을 닫는 사람이 있다.
 */

import type { ReassignResult } from '@/modules/workspaces/api'

export function ReassignPlanView({ result }: { result: ReassignResult }) {
  const shown = result.items.filter((one) =>
    result.applied ? (one.moved ?? 0) > 0 || one.count > 0 : true,
  )
  return (
    <div className="space-y-2 text-xs">
      <p>
        <b>{result.source_name}</b> → <b>{result.target_name}</b>
        {result.applied ? ' — 이동했습니다' : ' — 계획(아직 아무것도 이동하지 않았습니다)'}
      </p>
      <table>
        <thead className="text-muted-foreground">
          <tr>
            <th className="pr-3 text-left font-normal">종류</th>
            <th className="pr-3 text-right font-normal">이동할 것</th>
            {result.applied && <th className="text-right font-normal">이동함</th>}
          </tr>
        </thead>
        <tbody className="tabular-nums">
          {shown.map((one) => (
            <tr key={one.kind}>
              <td className="pr-3">{one.label}</td>
              <td className="pr-3 text-right">{one.count.toLocaleString()}</td>
              {result.applied && (
                <td className="text-right">{(one.moved ?? 0).toLocaleString()}</td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
      {result.errors.map((one) => (
        <p key={one} className="text-destructive">
          {one}
        </p>
      ))}
    </div>
  )
}
