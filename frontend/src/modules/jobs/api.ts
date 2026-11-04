/**
 * 작업 — **오래 걸리는 일은 요청이 아니라 표에 산다.**
 *
 * 일괄 입력는 올리는 순간 202 로 작업이 되고, 워커가 뒤에서 돈다. 화면은 그 작업을
 * 2초마다 보며 진행률을 그리고, 끝나면 결과(가져오기면 계획 표)를 꺼내 쓴다.
 * 설계는 `docs/sp/작업-워커-설계.md`.
 */

import { api, downloadFile } from '@/shared/api/client'

export type JobStatus = 'queued' | 'running' | 'done' | 'failed' | 'cancelled'

export interface JobProgress {
  stage: string
  done: number
  total: number
}

export interface Job {
  id: string
  kind: string
  kind_label: string
  status: JobStatus
  params: Record<string, unknown>
  progress: JobProgress
  /** 끝난 뒤의 결과 — 가져오기면 `ImportPlan` 모양 + `fingerprint`. */
  result: Record<string, unknown> | null
  error: string | null
  parent_id: string | null
  /** 이 계획을 적용한(또는 적용 중인) 작업 — 있으면 다시 적용하지 않는다. */
  applied_by?: string | null
  input_file_name: string | null
  has_output: boolean
  requested_by_name: string | null
  /** 시킨 사람 — 「취소」 · 「적용」 은 그 사람과 시스템 관리자만(서버도 그렇게 거절한다). */
  requested_by_id?: string | null
  workspace_slug: string | null
  cancel_requested: boolean
  attempts: number
  created_at: string
  started_at: string | null
  finished_at: string | null
}

export interface JobList {
  items: Job[]
  total: number
}

export interface WorkerInfo {
  worker_id: string
  host: string
  pid: number
  started_at: string
  last_seen: string
  current_job_id: string | null
  alive: boolean
}

export const TERMINAL: ReadonlySet<JobStatus> = new Set(['done', 'failed', 'cancelled'])

export function isTerminal(job: Pick<Job, 'status'>): boolean {
  return TERMINAL.has(job.status)
}

export const STATUS_LABEL: Record<JobStatus, string> = {
  queued: '대기',
  running: '진행 중',
  done: '완료',
  failed: '실패',
  cancelled: '취소됨',
}

/** 폴링 간격. 워커의 진행률 갱신(200행마다)보다 촘촘할 이유가 없다. */
export const POLL_MS = 1500

/**
 * 이만큼 「대기」 에 머물면 워커를 의심한다.
 *
 * **워커가 안 떠 있으면 작업은 영영 대기다.** 그때 화면이 말없이 돌기만 하면 사람은 제 파일이
 * 잘못된 줄 알고 몇 번을 다시 올린다 — 원인은 서버에 있는데.
 */
export const STUCK_MS = 15_000

/**
 * 이만큼 내리 「대기」 면 **기다림을 그만둔다** — 아무 워커도 안 집어 간 것이다.
 *
 * 끝이 없던 때는 워커가 죽으면 데이터 소스 동기화 · 내보내기 「만드는 중…」 · 지표 다시 계산 ·
 * 객체 삭제(작업 경로)가 영영 바쁨이었다(2026-10-08). 도는 중(`running`)인 것은 오래 걸려도
 * 기다린다 — 큰 파일은 분 단위가 정상이다.
 */
export const GIVE_UP_MS = 120_000

/** 기다림을 그만뒀을 때의 말 — 작업은 대기열에 남아 있고, 워커가 살아나면 마저 돈다. */
export class JobStalledError extends Error {
  readonly job: Job

  constructor(job: Job) {
    super(
      '워커가 돌지 않는 것 같습니다 — 작업이 아직 아무에게도 집히지 않았습니다. 「내 활동 › 작업」 ' +
        '화면에서 확인하세요(작업은 대기열에 남아 있어, 워커가 살아나면 마저 돕니다).',
    )
    this.name = 'JobStalledError'
    this.job = job
  }
}

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms))

export interface JobKind {
  name: string
  label: string
  needs_file: boolean
  two_step: boolean
  /** 파일을 받는 종류면 그 상한(바이트) — 종류마다 다르다(표 파일 100MB · 사진 zip 2GB). */
  max_file_bytes?: number | null
}

/** 일괄 처리의 한 줄 결과 — `ok` 면 `job_id` 가 새로 선 작업이다. */
export interface JobBulkResult {
  id: string
  status: 'ok' | 'error'
  message: string
  job_id: string | null
}

export const jobsApi = {
  get: (id: string) => api.get<Job>(`/jobs/${id}`),
  /**
   * 목록. `kind` · `mine` 은 **타이머가 넣은 것에 파묻히지 않으려고** 있다 — 데이터 소스마다
   * 5분에 한 줄이면 하루 288행이고, 사람이 올린 작업은 그 사이에 한 줄이다.
   */
  list: (opts: {
    limit: number
    offset: number
    status?: string
    kind?: string
    mine?: boolean
  }) => {
    const params = new URLSearchParams({ limit: String(opts.limit), offset: String(opts.offset) })
    if (opts.status) params.set('status', opts.status)
    if (opts.kind) params.set('kind', opts.kind)
    if (opts.mine) params.set('mine', 'true')
    return api.get<JobList>(`/jobs?${params.toString()}`)
  },
  kinds: () => api.get<JobKind[]>('/jobs/kinds'),
  /** 파일 없는 작업 하나를 넣는다 — 종류와 인자만(`POST /api/jobs`). */
  submit: (kind: string, params: Record<string, unknown> = {}) => {
    const body = new FormData()
    body.set('kind', kind)
    body.set('params', JSON.stringify(params))
    return api.postForm<Job>('/jobs', body)
  },
  workers: () => api.get<WorkerInfo[]>('/jobs/workers'),
  /** 계획을 본 뒤 **사람이 누르는 자리** — 같은 파일 · 같은 지문으로 적용 작업을 만든다. */
  apply: (id: string) => api.post<Job>(`/jobs/${id}/apply`),
  cancel: (id: string) => api.post<Job>(`/jobs/${id}/cancel`),
  /**
   * 고른 것들을 **한 번에** — 줄마다 결과가 온다(하나가 막혀도 나머지는 간다).
   *
   * 스무 건을 스무 번 펼쳐 누르게 하면 아무도 끝까지 안 한다. 검사는 한 건과 같다.
   */
  applyMany: (ids: string[]) => api.post<JobBulkResult[]>('/jobs/apply', { ids }),
  cancelMany: (ids: string[]) => api.post<JobBulkResult[]>('/jobs/cancel', { ids }),
  /**
   * 끝날 때까지 본다. 매 바퀴 `onTick` 으로 진행률을 준다.
   *
   * **끝난 작업을 돌려줄 뿐 실패를 던지지 않는다** — 실패도 결과다. 부르는 쪽이
   * `status` 를 보고 `error` 를 사람에게 보인다. 던지면 「어느 행이 왜」 가 사라진다.
   *
   * 던지는 것은 하나 — **내리 `giveUpMs` 동안 대기**면 `JobStalledError`(워커가 없다). 부르는
   * 쪽은 이미 오류를 보이는 자리가 있으므로 그 말이 그대로 선다.
   */
  waitFor: async (
    id: string,
    onTick?: (job: Job) => void,
    pollMs = POLL_MS,
    giveUpMs = GIVE_UP_MS,
  ): Promise<Job> => {
    let queuedSince: number | null = null
    for (;;) {
      const job = await api.get<Job>(`/jobs/${id}`)
      onTick?.(job)
      if (isTerminal(job)) return job
      // 집혔다가 워커가 죽어 다시 대기로 돌아온 것도 그때부터 다시 잰다.
      if (job.status === 'queued') {
        queuedSince ??= Date.now()
        if (Date.now() - queuedSince >= giveUpMs) throw new JobStalledError(job)
      } else {
        queuedSince = null
      }
      await sleep(pollMs)
    }
  },
  /**
   * 결과 파일을 받는다 — 내보내기 작업이 끝난 뒤. 파일 이름은 서버가 준 것을 쓴다.
   */
  download: (id: string, filename: string) => downloadFile(`/jobs/${id}/download`, filename),
  /** 계획의 **모든 줄** — 응답 · 화면은 앞의 500줄(오류 먼저)만 싣는다. */
  rowsCsv: (id: string) => downloadFile(`/jobs/${id}/rows.csv`, `job-${id.slice(0, 8)}-rows.csv`),
  /**
   * 내보내기 한 바퀴 — 작업을 넣고, 끝나기를 기다리고, 파일을 받는다. 실패하면 그 이유로 던진다.
   *
   * 큰 타입의 내보내기는 요청 안에서 만들면 끊긴다 — 그래서 작업이고, 화면은 잠깐 기다린다.
   */
  exportAndDownload: async (start: () => Promise<Job>, filename: string): Promise<void> => {
    const started = await start()
    const done = await jobsApi.waitFor(started.id)
    if (done.status !== 'done') {
      throw new Error(
        done.error ??
          (done.status === 'cancelled' ? '취소됐습니다.' : '내보내기가 끝나지 않았습니다.'),
      )
    }
    await jobsApi.download(done.id, filename)
  },
}
