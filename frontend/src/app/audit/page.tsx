"use client"

import { useQuery } from "@tanstack/react-query"
import { useState } from "react"
import { Notice, PageFrame } from "@/components/page-frame"
import { Button } from "@/components/ui/button"
import { api } from "@/lib/api"
import type { AuditItem } from "@/lib/types"

const PAGE = 25

export default function AuditPage() {
  const [offset, setOffset] = useState(0)
  const query = useQuery({
    queryKey: ["audit", offset],
    queryFn: () => api<{ total: number; items: AuditItem[] }>(`/audit?limit=${PAGE}&offset=${offset}`),
  })
  if (query.isLoading) return <Notice title="Loading the audit log" />
  if (query.isError) return <Notice title="Audit history could not be loaded" detail={(query.error as Error).message} />
  const total = query.data?.total ?? 0
  const items = query.data?.items ?? []

  return (
    <PageFrame
      eyebrow="Security-sensitive actions"
      title="Audit log"
      note="Decisions, policy edits, webhook rejections, and review requests are stored. Secrets in details are redacted by the logger; payloads here are the structured fields the API persisted."
      actions={
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <span>
            {total === 0 ? "0" : `${offset + 1}–${Math.min(offset + PAGE, total)}`} of {total}
          </span>
          <Button size="sm" variant="outline" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
            Newer
          </Button>
          <Button size="sm" variant="outline" disabled={offset + PAGE >= total} onClick={() => setOffset(offset + PAGE)}>
            Older
          </Button>
        </div>
      }
    >
      {items.length === 0 ? (
        <p className="p-4 text-sm text-muted-foreground">No audit events yet.</p>
      ) : (
        <table className="w-full text-left text-sm">
          <thead className="text-[11px] uppercase tracking-wider text-muted-foreground">
            <tr>
              <th className="px-4 py-2 font-medium">When</th>
              <th className="px-2 py-2 font-medium">Action</th>
              <th className="px-2 py-2 font-medium">Target</th>
              <th className="px-2 py-2 font-medium">Details</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.id} className="border-t align-top">
                <td className="px-4 py-3 text-xs text-muted-foreground">{item.created_at}</td>
                <td className="px-2 py-3 text-xs">{item.action}</td>
                <td className="px-2 py-3 text-xs">
                  {item.target_type}
                  <div className="mono text-[10px] text-muted-foreground">{item.target_id}</div>
                </td>
                <td className="px-2 py-3">
                  <pre className="mono max-w-xl whitespace-pre-wrap text-[11px] text-muted-foreground">
                    {item.details ? JSON.stringify(item.details) : "—"}
                  </pre>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </PageFrame>
  )
}
