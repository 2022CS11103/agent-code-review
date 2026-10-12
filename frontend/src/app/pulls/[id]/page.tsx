"use client"

import Link from "next/link"
import { useParams, useRouter } from "next/navigation"
import { useQuery } from "@tanstack/react-query"
import { useState } from "react"
import { toast } from "sonner"
import { Notice, PageFrame } from "@/components/page-frame"
import { Button } from "@/components/ui/button"
import { api } from "@/lib/api"
import { shortSha } from "@/lib/format"
import type { PullDetail } from "@/lib/types"

export default function PullPage() {
  const params = useParams<{ id: string }>()
  const id = params.id
  const router = useRouter()
  const [pending, setPending] = useState(false)
  const pull = useQuery({
    queryKey: ["pull", id],
    queryFn: () => api<PullDetail>(`/pulls/${id}`),
  })

  async function runReview() {
    setPending(true)
    try {
      const result = await api<{ job_id: string; created: boolean; status: string }>("/reviews", {
        method: "POST",
        body: JSON.stringify({ pull_request_id: id }),
      })
      toast.success(result.created ? "Review queued" : "Existing review reused")
      router.push(`/reviews/${result.job_id}`)
    } catch (error) {
      toast.error((error as Error).message)
      setPending(false)
    }
  }

  if (pull.isLoading) return <Notice title="Loading the pull request" />
  if (pull.isError || !pull.data) return <Notice title="This pull request could not be loaded" detail={(pull.error as Error)?.message} />
  const data = pull.data
  const active = data.jobs.find((job) => job.status === "queued" || job.status === "running")

  return (
    <PageFrame
      eyebrow={`${data.repository.owner}/${data.repository.name}`}
      title={`#${data.pull_request.number} ${data.pull_request.title}`}
      note={`${data.pull_request.author} · ${data.pull_request.base_ref} ← ${data.pull_request.head_ref} · ${shortSha(data.pull_request.head_sha)} · ${data.repository.trust_tier}`}
      actions={
        <Button size="sm" onClick={runReview} disabled={pending}>
          {pending ? "Queuing…" : active ? "Open running review" : "Run review"}
        </Button>
      }
    >
      <div className="grid lg:grid-cols-[minmax(0,1fr)_280px]">
        <section>
          <h2 className="border-b px-4 py-2 text-[11px] uppercase tracking-[0.16em] text-muted-foreground">Changed files</h2>
          <ul>
            {data.files.map((file) => (
              <li key={file.id} className="border-b px-4 py-2 text-sm">
                <span className="mono text-xs">{file.path}</span>
                <span className="ml-2 text-xs text-muted-foreground">
                  {file.status} +{file.additions} −{file.deletions}
                  {file.skipped_reason ? ` · skipped ${file.skipped_reason}` : ""}
                </span>
              </li>
            ))}
          </ul>
          {data.pull_request.body ? <p className="px-4 py-4 text-sm leading-relaxed text-muted-foreground">{data.pull_request.body}</p> : null}
        </section>
        <aside className="border-t lg:border-t-0 lg:border-l">
          <h2 className="border-b px-4 py-2 text-[11px] uppercase tracking-[0.16em] text-muted-foreground">Review jobs</h2>
          {data.jobs.length === 0 ? (
            <p className="px-4 py-4 text-sm text-muted-foreground">No review has been started for this pull request.</p>
          ) : (
            <ul>
              {data.jobs.map((job) => (
                <li key={job.id} className="border-b px-4 py-3 text-xs">
                  <Link href={`/reviews/${job.id}`} className="underline">
                    {job.status} · {job.stage}
                  </Link>
                  <div className="mt-1 text-muted-foreground">
                    {job.origin} · {job.created_at}
                  </div>
                </li>
              ))}
            </ul>
          )}
          <p className="px-4 py-3 text-[11px] leading-relaxed text-muted-foreground">
            Running a review creates a job. The worker parses the diff, runs static checks, then records findings. The repository is not modified.
          </p>
        </aside>
      </div>
    </PageFrame>
  )
}
