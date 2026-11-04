/**
 * 「메뉴가 바뀌었다」 를 알리는 신호 — **정의를 고치면 사이드바가 그 자리에서 따라온다.**
 *
 * 사이드바의 동적 묶음은 서버가 준다(`/api/ontology/nav`). 그 값은 사이드바가 **한 번** 읽고
 * 들고 있으므로, 관리 › 온톨로지에서 묶음·타입을 고쳐도 새로 고치기 전에는 옛 모양이 남는다 —
 * 상위 묶음을 정하고 사이드바를 보면 「정했는데 안 바뀐다」 로 보인다(실측).
 *
 * 두 곳을 잇는 자리라 전역 상태를 새로 만들지 않았다: 고치는 쪽이 `navChanged()` 를 부르고,
 * 사이드바가 `onNavChanged` 로 듣는다. 듣는 쪽이 없으면 아무 일도 안 일어난다.
 */

const listeners = new Set<() => void>()

/** 정의가 바뀌었다 — 듣고 있는 사이드바가 다시 읽는다. */
export function navChanged(): void {
  // 듣는 쪽이 듣기를 멈출 수 있으니 **복사한 목록**을 돈다 — 도는 중에 지우면 하나를 건너뛴다.
  const now = Array.from(listeners)
  for (const listener of now) listener()
}

/** 신호를 듣는다. 돌려주는 함수를 부르면 그만 듣는다(`useEffect` 의 청소 자리). */
export function onNavChanged(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}
