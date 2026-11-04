/**
 * 작업 기다리기 — **워커가 없으면 영영 기다리지 않는다.**
 *
 * 끝이 없던 때는 워커가 죽으면 데이터 소스 동기화 · 내보내기 「만드는 중…」 · 지표 다시 계산 ·
 * 객체 삭제(작업 경로)가 영영 바쁨이었다(2026-10-08).
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

const client = vi.hoisted(() => ({ get: vi.fn() }))
vi.mock('@/shared/api/client', () => ({ api: { get: client.get }, downloadFile: vi.fn() }))

function job(status: string) {
  return { id: 'j1', status, progress: { stage: '', done: 0, total: 0 } }
}

describe('jobsApi.waitFor', () => {
  beforeEach(() => client.get.mockReset())

  it('내리 대기면 그만두고 작업 화면으로 보낸다', async () => {
    client.get.mockResolvedValue(job('queued'))
    const { jobsApi, JobStalledError } = await import('@/modules/jobs/api')
    const waiting = jobsApi.waitFor('j1', undefined, 5, 30)
    await expect(waiting).rejects.toBeInstanceOf(JobStalledError)
    await expect(waiting).rejects.toThrow(/작업.*화면에서 확인/)
  })

  it('도는 중이면 오래 걸려도 기다리고, 끝나면 돌려준다', async () => {
    // 대기 → 도는 중(여러 바퀴, 그만둘 시간보다 길게) → 완료.
    client.get
      .mockResolvedValueOnce(job('queued'))
      .mockResolvedValueOnce(job('running'))
      .mockResolvedValueOnce(job('running'))
      .mockResolvedValueOnce(job('running'))
      .mockResolvedValueOnce(job('running'))
      .mockResolvedValue(job('done'))
    const { jobsApi } = await import('@/modules/jobs/api')
    const done = await jobsApi.waitFor('j1', undefined, 10, 15)
    expect(done.status).toBe('done')
  })
})
