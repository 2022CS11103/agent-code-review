"use client"

import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { toast } from "sonner"
import { Notice, PageFrame } from "@/components/page-frame"
import { Button } from "@/components/ui/button"
import { api } from "@/lib/api"
import { percent } from "@/lib/format"
import type { EvaluationRun } from "@/lib/types"

export default function EvaluationsPage() {
  const client = useQueryClient()
  const [pending, setPending] = useState(false)
  const [selected, setSelected] = useState<string | null>(null)
  const query = useQuery({
    queryKey: ["evaluations"],
    queryFn: () => api<{ items: EvaluationRun[] }>("/evaluations"),
  })

  async function run() {
    setPending(true)
    try {
      const created = await api<EvaluationRun>("/evaluations", { method: "POST" })
      toast.success("Fixture suite recorded")
      setSelected(created.id)
      client.invalidateQueries({ queryKey: ["evaluations"] })
    } catch (error) {
      toast.error((error as Error).message)
    } finally {
      setPending(false)
    }
  }

  if (query.isLoading) return <Notice title="Loading evaluation runs" />
  if (query.isError) return <Notice title="Evaluations could not be loaded" detail={(query.error as Error).message} />
  const items = query.data?.items ?? []
  const current = items.find((item) => item.id === selected) ?? items[0] ?? null

  return (
    <PageFrame
      eyebrow="Measured quality"
      title="Evaluation"
      note="Precision and recall describe the builtin rule suite on labeled fixtures. They are not a claim about novel bugs or a hosted model."
      actions={
        <Button size="sm" onClick={run} disabled={pending}>
          {pending ? "Running…" : "Run fixture suite"}
        </Button>
      }
    >
      {items.length === 0 ? (
        <p className="p-4 text-sm text-muted-foreground">No evaluation runs yet. Run the builtin fixture suite to record precision and recall.</p>
      ) : (
        <div className="grid lg:grid-cols-[280px_minmax(0,1fr)]">
          <ul className="border-b lg:border-r lg:border-b-0">
            {items.map((item) => (
              <li key={item.id}>
                <button
                  type="button"
                  className={`block w-full px-4 py-3 text-left text-xs hover:bg-muted ${current?.id === item.id ? "bg-muted" : ""}`}
                  onClick={() => setSelected(item.id)}
                >
                  <div className="font-medium">{item.fixture_set}</div>
                  <div className="text-muted-foreground">
                    P {percent(item.precision)} · R {percent(item.recall)} · {item.analyzer_version}
                  </div>
                </button>
              </li>
            ))}
          </ul>
          {current ? <RunDetail run={current} /> : null}
        </div>
      )}
    </PageFrame>
  )
}

function RunDetail({ run }: { run: EvaluationRun }) {
  return (
    <div className="min-w-0">
      <div className="flex flex-wrap gap-x-5 gap-y-1 border-b px-4 py-3 text-xs">
        <span>precision {percent(run.precision)}</span>
        <span>recall {percent(run.recall)}</span>
        <span>f1 {percent(run.f1)}</span>
        <span>false-positive rate {percent(run.false_positive_rate)}</span>
        <span>
          tp {run.true_positive} · fp {run.false_positive} · fn {run.false_negative}
        </span>
        <span className="text-muted-foreground">{run.duration_ms} ms</span>
      </div>
      <p className="px-4 py-3 text-xs text-muted-foreground">
        {run.details?.scope} Provider {run.model_provider}. Prompt {run.prompt_version}.
      </p>
      {run.residual_risks?.length ? (
        <div className="border-t px-4 py-3">
          <h2 className="text-[11px] uppercase tracking-[0.16em] text-muted-foreground">Residual risks</h2>
          <ul className="mt-2 list-disc space-y-1 pl-4 text-xs leading-relaxed">
            {run.residual_risks.map((risk) => (
              <li key={risk}>{risk}</li>
            ))}
          </ul>
        </div>
      ) : null}
      <table className="w-full text-left text-xs">
        <thead className="text-[11px] uppercase tracking-wider text-muted-foreground">
          <tr>
            <th className="px-4 py-2 font-medium">Fixture</th>
            <th className="px-2 py-2 font-medium">Expected</th>
            <th className="px-2 py-2 font-medium">Found</th>
          </tr>
        </thead>
        <tbody>
          {(run.details?.cases ?? []).map((item) => (
            <tr key={item.id} className="border-t">
              <td className="px-4 py-2 mono">{item.id}</td>
              <td className="px-2 py-2">{item.expected.join(", ") || "—"}</td>
              <td className="px-2 py-2">
                {item.found.join(", ") || "—"}
                {item.false_positive.length ? <span className="ml-2 text-destructive">fp {item.false_positive.join(", ")}</span> : null}
                {item.false_negative.length ? <span className="ml-2 text-destructive">fn {item.false_negative.join(", ")}</span> : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
