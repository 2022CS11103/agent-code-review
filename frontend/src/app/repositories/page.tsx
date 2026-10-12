"use client"

import Link from "next/link"
import { useQuery } from "@tanstack/react-query"
import { Notice, PageFrame } from "@/components/page-frame"
import { api } from "@/lib/api"
import type { RepositorySummary } from "@/lib/types"

export default function RepositoriesPage() {
  const query = useQuery({
    queryKey: ["repositories"],
    queryFn: () => api<{ items: RepositorySummary[] }>("/repositories"),
  })
  if (query.isLoading) return <Notice title="Loading repositories" />
  if (query.isError) return <Notice title="Repositories could not be loaded" detail={(query.error as Error).message} />
  const items = query.data?.items ?? []

  return (
    <PageFrame
      eyebrow="Policy studio"
      title="Repositories"
      note="Demo repositories are local fixtures. Connecting a GitHub App is documented and not active in this build, so no install button is shown."
    >
      {items.length === 0 ? (
        <p className="p-4 text-sm text-muted-foreground">No repositories are connected to this workspace.</p>
      ) : (
        <table className="w-full text-left text-sm">
          <thead className="text-[11px] uppercase tracking-wider text-muted-foreground">
            <tr>
              <th className="px-4 py-2 font-medium">Repository</th>
              <th className="px-2 py-2 font-medium">Connection</th>
              <th className="px-2 py-2 font-medium">Trust</th>
              <th className="px-2 py-2 font-medium">Threshold</th>
            </tr>
          </thead>
          <tbody>
            {items.map((repo) => (
              <tr key={repo.id} className="border-t">
                <td className="px-4 py-3">
                  <Link href={`/repositories/${repo.id}`} className="font-medium hover:underline">
                    {repo.owner}/{repo.name}
                  </Link>
                  <div className="text-xs text-muted-foreground">{repo.primary_language}</div>
                </td>
                <td className="px-2 py-3 text-xs">{repo.connection_status}</td>
                <td className="px-2 py-3 text-xs">{repo.trust_tier}</td>
                <td className="px-2 py-3 text-xs">{repo.policy?.severity_threshold ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </PageFrame>
  )
}
