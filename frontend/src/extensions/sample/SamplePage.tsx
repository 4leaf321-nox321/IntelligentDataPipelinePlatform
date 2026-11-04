import { api } from '@/shared/api/client'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { PageHeader } from '@/shared/components/PageHeader'
import { useResource } from '@/shared/hooks/useResource'

interface Ping {
  extension: string
  user: string
}

export default function SamplePage() {
  const ping = useResource(() => api.get<Ping>('/ext/sample/ping'), [])
  if (ping.error) return <ErrorNotice error={ping.error} />
  return (
    <div className="space-y-4">
      <PageHeader title="본보기 확장" description="이 설치에서 켠 확장의 자리입니다." />
      {ping.data && (
        <p className="text-muted-foreground text-sm">
          서버의 <span className="font-mono">{ping.data.extension}</span> 확장이{' '}
          <span className="font-mono">{ping.data.user}</span> 에게 응답했습니다.
        </p>
      )}
    </div>
  )
}
