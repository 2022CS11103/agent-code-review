"use client"

import Link from "next/link"
import { useParams } from "next/navigation"
import { useQuery } from "@tanstack/react-query"
import { PageFrame, Notice } from "@/components/page-frame"
import { api } from "@/lib/api"
import { money } from "@/lib/format"
import type { Activity, TraceResponse } from "@/lib/types"

export default function TracePage() {
  const params = useParams<{ id: string }>()
  const id = params.id
  const trace = useQuery({
    queryKey: ["trace", id],
    queryFn: () => api<TraceResponse>(`/reviews/${id}/trace`),
  })
  const events = useQuery({
    queryKey: ["events", id],
    queryFn: () => api<{ items: Activity[] }>(`/reviews/${id}/events`),
  })

  if (trace.isLoading) return <Notice title="Loading the execution trace" />
  if (trace.isError || !trace.data) {
    return <Notice title="This trace could not be loaded" detail={(trace.error as Error)?.message} />
  }
  const data = trace.data
  const usage = data.usage

  return (
    <PageFrame
      eyebrow="Agent observatory"
      title="Execution trace"
      note="Tool output and static findings are observed. Agent notes are labeled commentary from the deterministic mock provider. They are not billed model calls."
      actions={
        <Link href={`/reviews/${id}`} className="text-xs underline">
          Back to review
        </Link>
      }
    >
      <div className="grid gap-0 lg:grid-cols-[minmax(0,1fr)_320px]">
        <section className="border-b lg:border-r lg:border-b-0">
          <header className="border-b px-4 py-3 text-xs text-muted-foreground">
            Correlation <span className="mono text-foreground">{data.correlation_id}</span>
          </header>
          {data.traces.length === 0 ? (
            <p className="p-4 text-sm text-muted-foreground">No agent nodes have been recorded for this job yet.</p>
          ) : (
            <ol>
              {data.traces.map((node) => (
                <li key={node.id} className="border-b px-4 py-3">
                  <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                    <span className="text-sm font-medium">{node.agent}</span>
                    <span className="mono text-xs text-muted-foreground">{node.node}</span>
                    <span className="text-xs">{node.status}</span>
                    <span className="mono text-xs text-muted-foreground">{node.duration_ms} ms</span>
                    <span className="text-xs text-muted-foreground">retries {node.retry_count}</span>
                  </div>
                  {node.input_summary ? <p className="mt-2 text-xs leading-relaxed text-muted-foreground">{node.input_summary}</p> : null}
                  {node.error ? <p className="mt-2 text-xs text-destructive">{node.error}</p> : null}
                  {node.tool_calls?.length ? (
                    <ul className="mt-2 space-y-1 text-xs">
                      {node.tool_calls.map((call, index) => (
                        <li key={`${call.tool}-${index}`} className="mono">
                          {call.tool} · {call.status}
                        </li>
                      ))}
                    </ul>
                  ) : null}
                  <p className="mt-2 text-[11px] text-muted-foreground">
                    tokens {node.token_input}/{node.token_output} · estimate {money(node.estimated_cost_usd)} · billed {String(node.billed)}
                  </p>
                  {node.output ? (
                    <pre className="mono mt-2 max-h-48 overflow-auto whitespace-pre-wrap text-[11px] leading-relaxed text-muted-foreground">
                      {JSON.stringify(node.output, null, 2)}
                    </pre>
                  ) : null}
                </li>
              ))}
            </ol>
          )}
        </section>
        <aside>
          <div className="border-b px-4 py-3 text-xs leading-relaxed">
            <h2 className="text-[11px] uppercase tracking-[0.16em] text-muted-foreground">Usage</h2>
            {usage ? (
              <dl className="mt-2 space-y-1">
                <Row label="Provider" value={usage.provider} />
                <Row label="Rate card" value={usage.rate_card} />
                <Row label="Input tokens" value={String(usage.token_input)} />
                <Row label="Output tokens" value={String(usage.token_output)} />
                <Row label="Estimated cost" value={money(usage.estimated_cost_usd)} />
                <Row label="Billed" value={String(usage.billed)} />
              </dl>
            ) : (
              <p className="mt-2 text-muted-foreground">Usage is recorded when the agent stage finishes.</p>
            )}
            {usage?.note ? <p className="mt-2 text-muted-foreground">{usage.note}</p> : null}
          </div>
          <div className="px-4 py-3">
            <h2 className="text-[11px] uppercase tracking-[0.16em] text-muted-foreground">Event trail</h2>
            {events.isLoading ? <p className="mt-2 text-xs text-muted-foreground">Loading events…</p> : null}
            {events.isError ? <p className="mt-2 text-xs text-destructive">{(events.error as Error).message}</p> : null}
            <ol className="mt-3 space-y-3">
              {(events.data?.items ?? []).map((event) => (
                <li key={event.id} className="text-xs leading-relaxed">
                  <div className="mono text-[10px] text-muted-foreground">
                    #{event.sequence} · {event.stage}
                    {event.agent ? ` · ${event.agent}` : ""} · {event.level}
                  </div>
                  <p>{event.message}</p>
                </li>
              ))}
            </ol>
          </div>
        </aside>
      </div>
    </PageFrame>
  )
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-3">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="mono text-right">{value}</dd>
    </div>
  )
}
