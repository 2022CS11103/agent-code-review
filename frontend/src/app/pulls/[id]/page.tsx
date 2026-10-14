"use client"

import Link from "next/link"
import { useParams, useRouter } from "next/navigation"
import { useQuery } from "@tanstack/react-query"
import { useState } from "react"
import { GitBranch, Play } from "lucide-react"
import { toast } from "sonner"
import { Notice } from "@/components/page-frame"
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
  const latest = data.jobs[0]
  const demo = data.repository.trust_tier === "demo_fixture"

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="border-b px-4 py-3">
        <p className="text-xs text-muted-foreground">
          <Link href="/" className="hover:text-foreground">PRISM</Link>
          <span className="px-1.5">/</span>
          <Link href="/repositories" className="hover:text-foreground">
            {data.repository.owner}/{data.repository.name}
          </Link>
          <span className="px-1.5">/</span>
          PR #{data.pull_request.number}
        </p>
        <div className="mt-2 flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="text-lg font-medium tracking-tight">{data.pull_request.title}</h1>
            <p className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
              <GitBranch className="size-3.5" />
              <span>{data.pull_request.head_ref}</span>
              <span>→</span>
              <span>{data.pull_request.base_ref}</span>
              <span className="mono">{shortSha(data.pull_request.head_sha)}</span>
              <span>{data.pull_request.author}</span>
              {demo ? <span className="rounded-full border px-2 py-0.5 text-[10px] uppercase tracking-wider">Demo fixture</span> : null}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-xs text-muted-foreground">{latest ? latest.status : "Not reviewed"}</span>
            <Button size="sm" onClick={runReview} disabled={pending}>
              <Play className="size-3.5" />
              {pending ? "Queuing…" : active ? "Open running review" : "Run review"}
            </Button>
          </div>
        </div>
      </div>
      <div className="flex gap-4 border-b px-4 text-sm">
        <span className="border-b-2 border-foreground py-2">Findings 0</span>
        {latest ? (
          <Link href={`/reviews/${latest.id}/validation`} className="py-2 text-muted-foreground hover:text-foreground">
            Patch & validation
          </Link>
        ) : (
          <span className="py-2 text-muted-foreground">Patch & validation</span>
        )}
        {latest ? (
          <Link href={`/reviews/${latest.id}/trace`} className="py-2 text-muted-foreground hover:text-foreground">
            Agent trace
          </Link>
        ) : (
          <span className="py-2 text-muted-foreground">Agent trace</span>
        )}
      </div>
      <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[240px_minmax(0,1fr)_280px]">
        <aside className="min-h-0 overflow-auto border-b p-4 lg:border-b-0 lg:border-r">
          <div className="flex items-center justify-between text-sm">
            <span>Findings</span>
            <span className="text-xs text-muted-foreground">0 open</span>
          </div>
          <p className="mt-1 text-[11px] text-muted-foreground">0 shown · 0 open</p>
          <div className="mt-3 space-y-2 text-xs text-muted-foreground">
            <div className="rounded-md border px-2 py-1.5">All severity</div>
            <div className="rounded-md border px-2 py-1.5">All categories</div>
            <div className="rounded-md border px-2 py-1.5">All statuses</div>
          </div>
          <p className="mt-8 text-center text-xs text-muted-foreground">No findings match these filters.</p>
        </aside>
        <section className="flex min-h-0 flex-col items-center justify-center gap-2 p-6 text-center">
          <p className="text-sm">No finding selected</p>
          <p className="max-w-xs text-xs text-muted-foreground">
            {demo
              ? "Run review to inspect a labeled demo fixture."
              : "Run review to read this pull request. Untrusted repository code is not executed."}
          </p>
          {data.files.length > 0 ? (
            <ul className="mt-4 w-full max-w-md text-left text-xs">
              {data.files.map((file) => (
                <li key={file.id} className="border-t py-1.5">
                  <span className="mono">{file.path}</span>
                  <span className="ml-2 text-muted-foreground">
                    {file.status} +{file.additions} −{file.deletions}
                  </span>
                </li>
              ))}
            </ul>
          ) : null}
        </section>
        <aside className="min-h-0 overflow-auto border-t p-4 lg:border-t-0 lg:border-l">
          <h2 className="text-sm">Review evidence</h2>
          <p className="mt-3 text-xs leading-relaxed text-muted-foreground">
            No finding evidence is available for this pull request yet.
            {demo ? " Running this fixture creates a clearly labeled demo review result." : ""}
          </p>
        </aside>
      </div>
    </div>
  )
}
