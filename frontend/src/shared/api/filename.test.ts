/**
 * 받은 파일의 이름은 **서버가 붙인 것**을 쓴다 — 부르는 쪽의 이름은 어림이라, 큰 내보내기의
 * zip 과 작업 결과의 xlsx · json 이 늘 `.csv` 로 받아졌다(2026-10-08).
 */

import { describe, expect, it } from 'vitest'

import { filenameOf } from '@/shared/api/client'

describe('받은 파일의 이름', () => {
  it('UTF-8 이름을 먼저, 없으면 따옴표 이름, 자리표시(download)는 안 쓴다', () => {
    expect(
      filenameOf(`attachment; filename="download"; filename*=UTF-8''%EB%B6%80%ED%92%88.zip`),
    ).toBe('부품.zip')
    expect(filenameOf('attachment; filename="ontology-full-20261008.xlsx"')).toBe(
      'ontology-full-20261008.xlsx',
    )
    expect(filenameOf('attachment; filename="download"')).toBeNull()
    expect(filenameOf(null)).toBeNull()
  })
})
