/**
 * 렌더 중 예외를 붙잡는다 — **백지 화면을 만들지 않는다.**
 *
 * React 는 렌더 중 예외가 나면 그 트리를 통째로 언마운트한다. 경계가 없으면 앱
 * 전체가 사라져 **아무것도 없는 흰 화면**이 남는데, 거기에는 무엇이 잘못됐는지도,
 * 어디로 가야 하는지도 안 적힌다. 사람은 그것을 "서버가 죽었다" 로 읽고, 실제로는
 * 화면 한 조각의 undefined 참조인 경우가 대부분이다.
 *
 * ## 오류 응답과는 다른 길이다
 *
 * `ErrorNotice` 는 **서버가 말해 준 실패**를 보여 준다(코드·요청 ID 가 있다).
 * 여기는 **우리 코드가 터진 것**이라 서버는 모른다 — 그래서 보여 줄 수 있는 것은
 * 예외 메시지와 「다시 시도」 뿐이고, 원본은 콘솔에 남긴다.
 *
 * ## 왜 클래스인가
 *
 * `componentDidCatch`·`getDerivedStateFromError` 에 대응하는 훅이 아직 없다.
 * 이 파일이 이 저장소의 유일한 클래스 컴포넌트인 이유다.
 */

import { Component } from 'react'
import type { ErrorInfo, ReactNode } from 'react'
import { AlertTriangle } from 'lucide-react'

import { PUBLIC_PATH } from '@/shared/base'
import { isChunkLoadError, isReloading, reloadForNewBuild } from '@/shared/newBuild'

import { Button } from '@/shared/components/ui/button'

interface Props {
  children: ReactNode
  /** 경계가 다시 살아나야 하는 단위. 라우트가 바뀌면 이 값을 바꿔 준다. */
  resetKey?: string
}

interface State {
  error: Error | null
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidUpdate(previous: Props): void {
    // **다른 화면으로 옮기면 경계를 푼다.** 안 풀면 한 화면이 터진 뒤로 앱 전체가
    // 오류 화면에 갇히고, 사람은 새로고침 말고는 길이 없다고 느낀다.
    if (this.state.error && previous.resetKey !== this.props.resetKey) {
      this.setState({ error: null })
    }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // **새 판이 나와 옛 조각을 못 받은 것**이면 한 번 새로 고친다 — 화면 코드가 터진 것이
    // 아니다(`shared/newBuild`). 방금 고쳤는데 또 못 받으면 아래 안내가 남는다.
    if (isChunkLoadError(error) && reloadForNewBuild()) {
      this.forceUpdate()
      return
    }
    // **원본을 콘솔에 남긴다.** 화면에 스택을 뿌리지 않는 대신, 개발자 도구를 열면
    // 컴포넌트 사슬까지 그대로 보이게 한다.
    console.error('화면에서 처리하지 못한 오류', error, info.componentStack)
  }

  render(): ReactNode {
    const { error } = this.state
    if (!error) return this.props.children
    if (isReloading()) {
      return (
        <p className="text-muted-foreground py-16 text-center text-sm">
          새 판을 불러오는 중입니다…
        </p>
      )
    }
    if (isChunkLoadError(error)) {
      return (
        <div className="mx-auto max-w-lg py-16 text-center">
          <AlertTriangle className="mx-auto size-8 text-amber-600" />
          <h1 className="mt-4 text-lg font-semibold">이 화면의 파일을 받지 못했습니다</h1>
          <p className="text-muted-foreground mt-1 text-sm">
            새 판이 배포되었거나 서버에 잠깐 닿지 못했습니다. 새로 고치면 대개 낫습니다.
          </p>
          <p className="bg-muted text-muted-foreground mt-4 rounded-md p-3 text-left font-mono text-xs break-all">
            {error.message || String(error)}
          </p>
          <div className="mt-5 flex justify-center">
            <Button onClick={() => window.location.reload()}>새로 고침</Button>
          </div>
        </div>
      )
    }

    return (
      <div className="mx-auto max-w-lg py-16 text-center">
        <AlertTriangle className="text-destructive mx-auto size-8" />
        <h1 className="mt-4 text-lg font-semibold">이 화면을 그리지 못했습니다</h1>
        <p className="text-muted-foreground mt-1 text-sm">
          화면 쪽 문제라 서버에는 기록이 남지 않습니다. 계속 나면 아래 내용을 알려 주세요.
        </p>
        <p className="bg-muted text-muted-foreground mt-4 rounded-md p-3 text-left font-mono text-xs break-all">
          {error.message || String(error)}
        </p>
        <div className="mt-5 flex justify-center gap-2">
          <Button variant="outline" onClick={() => this.setState({ error: null })}>
            다시 시도
          </Button>
          {/* 새로고침이 아니라 홈으로 보낸다 — 같은 화면을 다시 그리면 대개 또
              터지고, 그러면 사람은 앱이 통째로 고장났다고 읽는다. */}
          <Button onClick={() => window.location.assign(`${PUBLIC_PATH}/`)}>홈으로</Button>
        </div>
      </div>
    )
  }
}
