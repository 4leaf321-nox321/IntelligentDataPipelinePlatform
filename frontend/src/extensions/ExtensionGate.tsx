/**
 * 꺼진 확장의 화면 — **꺼져 있다고 답한다.**
 *
 * 경로는 번들에 든 확장 **전부**를 등록한다. 켜짐이 런타임에 바뀌므로(관리 › 서버) 라우터를
 * 만들 때 가려 버리면 방금 켠 확장의 링크가 새로 고침 전까지 죽는다 — 서버도 같은 이유로
 * 라우터를 전부 붙이고 문으로 막는다(`require_extension`).
 */

import { PowerOff } from 'lucide-react'
import type { ReactNode } from 'react'

import { useEnabledExtensions } from '@/extensions/EnabledProvider'

export function ExtensionGate({ name, children }: { name: string; children?: ReactNode }) {
  const names = useEnabledExtensions()
  if (!names.includes(name)) {
    return (
      <div className="mx-auto max-w-lg py-16 text-center">
        <PowerOff className="text-muted-foreground mx-auto size-8" />
        <h1 className="mt-4 text-lg font-semibold">꺼진 확장입니다</h1>
        <p className="text-muted-foreground mt-1 text-sm">
          확장 <span className="font-mono">{name}</span> 이 이 설치에서 꺼져 있습니다. 관리 › 서버
          › 「확장 모듈」 에서 켤 수 있습니다.
        </p>
      </div>
    )
  }
  return <>{children}</>
}
