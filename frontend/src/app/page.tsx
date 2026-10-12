"use client"

import Link from "next/link"
import { useRouter } from "next/navigation"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useState } from "react"
import { toast } from "sonner"
import { api, subscribe } from "@/lib/api"
import { percent, seconds, shortSha } from "@/lib/format"
import type { CommandCenter, Severity } from "@/lib/types"

export default function CommandPage() {
  const router = useRouter()
  const [origin, setOrigin] = useState("all")
  const client = useQueryClient()
  const query = useQuery({
    queryKey: ["command-center", origin],
    queryFn: () => api<CommandCenter>(`/command-center?origin=${origin}`),
    refetchInterval: 4000,
  })

  useEffect(
    () => subscribe("/stream", () => client.invalidateQueries({ queryKey: ["command-center"] })),
    [client],
  )

  if (query.isLoading) return <Status title="Loading the review queue" />
  if (query.isError) return <Status title="The command center could not be loaded" detail={(query.error as Error).message} />
  const data = query.data
  if (!data) return null
  const maxSeverity = Math.max(1, ...Object.values(data.severity))

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden">
      <div className="flex flex-wrap items-end justify-between gap-3 border-b px-4 py-3 md:px-5">
        <div>
          <p className="text-[11px] uppercase tracking-[0.16em] text-muted-foreground">{data.workspace.name}</p>
          <h1 className="text-lg font-medium tracking-tight">Review queue</h1>
        </div>
        <p className="max-w-sm text-xs leading-relaxed text-muted-foreground">
          Catch the bug. Prove the fix. Show the evidence.
        </p>
      </div>
      <div className="flex flex-wrap items-center gap-x-5 gap-y-1 border-b px-4 py-2 text-xs md:px-5">
        <Metric label="Median" value={seconds(data.metrics.median_review_seconds)} hint={`${data.metrics.completed_reviews} completed`} />
        <Metric
          label="Confirmed"
          value={percent(data.metrics.confirmed_rate)}
          hint={data.metrics.decisions ? `${data.metrics.confirmed}/${data.metrics.decisions} decided` : "no decisions yet"}
        />
        <Metric
          label="Validation"
          value={percent(data.metrics.validation_success_rate)}
          hint={data.metrics.validation_runs ? `${data.metrics.validation_passed}/${data.metrics.validation_runs} runs passed` : "no validation runs"}
        />
        <Metric label="Active" value={String(data.metrics.running_reviews)} hint={`${data.metrics.failed_reviews} failed`} />
        <label className="ml-auto flex items-center gap-2 text-muted-foreground">
          Origin
          <select
            className="rounded-md border bg-card px-2 py-1 text-foreground"
            value={origin}
            onChange={(event) => setOrigin(event.target.value)}
          >
            <option value="all">All stored jobs</option>
            <option value="seed">Seeded history</option>
            <option value="live">This session</option>
            <option value="webhook">Webhooks</option>
          </select>
        </label>
      </div>
      <div className="grid min-h-0 flex-1 grid-cols-1 xl:grid-cols-[minmax(0,1fr)_320px]">
        <section className="min-h-0 overflow-auto">
          <table className="w-full text-left text-sm">
            <thead className="sticky top-0 bg-background text-[11px] uppercase tracking-wider text-muted-foreground">
              <tr>
                <th className="px-4 py-2 font-medium">Pull request</th>
                <th className="px-2 py-2 font-medium">Status</th>
                <th className="px-2 py-2 font-medium">Open findings</th>
                <th className="px-2 py-2 font-medium">SHA</th>
              </tr>
            </thead>
            <tbody>
              {data.queue.length === 0 ? (
                <tr>
                  <td colSpan={4} className="px-4 py-8 text-sm text-muted-foreground">
                    No pull requests in this origin filter.
                  </td>
                </tr>
              ) : (
                data.queue.map((item) => (
                  <tr key={item.pull_request_id} className="border-t hover:bg-muted/60">
                    <td className="px-4 py-3">
                      <button
                        type="button"
                        className="text-left"
                        onClick={() => {
                          if (item.job) router.push(`/reviews/${item.job.id}`)
                          else router.push(`/pulls/${item.pull_request_id}`)
                        }}
                      >
                        <div className="font-medium">
                          {item.repository.name} #{item.number}
                        </div>
                        <div className="text-xs text-muted-foreground">{item.title}</div>
                      </button>
                    </td>
                    <td className="px-2 py-3 text-xs">
                      {item.job ? (
                        <span>
                          {item.job.status}
                          <span className="mt-0.5 block text-muted-foreground">{item.job.origin}</span>
                          {item.job.status === "failed" || item.job.status === "budget_exhausted" ? (
                            <button
                              type="button"
                              className="mt-1 block underline"
                              onClick={async (event) => {
                                event.stopPropagation()
                                try {
                                  const result = await api<{ job_id: string }>(`/reviews/${item.job!.id}/retry`, { method: "POST" })
                                  router.push(`/reviews/${result.job_id}`)
                                } catch (error) {
                                  toast.error((error as Error).message)
                                }
                              }}
                            >
                              Retry
                            </button>
                          ) : null}
                        </span>
                      ) : (
                        <span className="text-muted-foreground">not reviewed</span>
                      )}
                    </td>
                    <td className="px-2 py-3">
                      <SeverityDots counts={item.open_findings} />
                    </td>
                    <td className="mono px-2 py-3 text-xs text-muted-foreground">{shortSha(item.head_sha)}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
          <div className="border-t px-4 py-3">
            <h2 className="text-[11px] uppercase tracking-[0.16em] text-muted-foreground">Repositories</h2>
            <ul className="mt-2 space-y-1 text-sm">
              {data.repositories.map((repo) => (
                <li key={repo.id}>
                  <Link href={`/repositories/${repo.id}`} className="hover:underline">
                    {repo.owner}/{repo.name}
                  </Link>
                  <span className="ml-2 text-xs text-muted-foreground">
                    {repo.connection_status} · {repo.trust_tier}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        </section>
        <aside className="min-h-0 overflow-auto border-t xl:border-t-0 xl:border-l">
          <div className="border-b px-4 py-3">
            <h2 className="text-[11px] uppercase tracking-[0.16em] text-muted-foreground">Unresolved by severity</h2>
            <ul className="mt-3 space-y-2">
              {(["critical", "high", "medium", "low"] as Severity[]).map((level) => (
                <li key={level} className="grid grid-cols-[72px_1fr_24px] items-center gap-2 text-xs">
                  <span className={`sev-${level}`}>{level}</span>
                  <span className="h-1.5 overflow-hidden rounded-full bg-muted">
                    <span
                      className="block h-full bg-current"
                      style={{ width: `${((data.severity[level] || 0) / maxSeverity) * 100}%`, color: "inherit" }}
                    />
                  </span>
                  <span className="mono text-right">{data.severity[level] || 0}</span>
                </li>
              ))}
            </ul>
            <p className="mt-3 text-[11px] leading-relaxed text-muted-foreground">{data.metrics.note}</p>
          </div>
          <div className="px-4 py-3">
            <h2 className="text-[11px] uppercase tracking-[0.16em] text-muted-foreground">Activity</h2>
            <ol className="mt-3 space-y-3">
              {data.activity.length === 0 ? (
                <li className="text-xs text-muted-foreground">No review events yet.</li>
              ) : (
                data.activity.map((event) => (
                  <li key={event.id} className="text-xs leading-relaxed">
                    <div className="text-muted-foreground">
                      {event.stage}
                      {event.agent ? ` · ${event.agent}` : ""}
                    </div>
                    <Link href={`/reviews/${event.job_id}`} className="hover:underline">
                      {event.message}
                    </Link>
                  </li>
                ))
              )}
            </ol>
          </div>
        </aside>
      </div>
    </div>
  )
}

function Metric({ label, value, hint }: { label: string; value: string; hint: string }) {
  return (
    <div title={hint}>
      <span className="text-muted-foreground">{label}</span> <span className="mono">{value}</span>
      <span className="ml-1 text-muted-foreground">{hint}</span>
    </div>
  )
}

function SeverityDots({ counts }: { counts: Record<string, number> }) {
  const levels: Severity[] = ["critical", "high", "medium", "low"]
  const any = levels.some((level) => counts[level])
  if (!any) return <span className="text-xs text-muted-foreground">none open</span>
  return (
    <span className="flex gap-2 text-[11px]">
      {levels.filter((level) => counts[level]).map((level) => (
        <span key={level} className={`mono sev-${level}`}>
          {counts[level]} {level}
        </span>
      ))}
    </span>
  )
}

function Status({ title, detail }: { title: string; detail?: string }) {
  return (
    <div className="p-6">
      <h1 className="text-lg font-medium">{title}</h1>
      {detail ? <p className="mt-2 text-sm text-muted-foreground">{detail}</p> : null}
    </div>
  )
}
