"use client"

import { Suspense } from "react"
import { useSearchParams } from "next/navigation"
import { GithubAccount } from "@/components/github-account"
import { Notice, PageFrame } from "@/components/page-frame"

export default function GithubRoute() {
  return (
    <Suspense fallback={<Notice title="Loading GitHub account" />}>
      <GithubPage />
    </Suspense>
  )
}

function GithubPage() {
  const status = useSearchParams().get("github")
  const returned =
    status === "connected"
      ? "GitHub account saved on this server. Choose repositories, then sync."
      : status === "failed"
        ? "GitHub did not finish connecting the account. No credential was stored."
        : null

  return (
    <PageFrame
      eyebrow="Workspace"
      title="GitHub account"
      note="Connect the GitHub account that owns the repositories you want reviewed. A repository on another account appears after that account installs the same app."
    >
      {returned ? <p className="border-b px-4 py-3 text-sm">{returned}</p> : null}
      <GithubAccount />
      <ol className="max-w-xl space-y-3 px-4 py-4 text-sm leading-relaxed text-muted-foreground">
        <li>1. Add GitHub account. GitHub asks you to create the PRISM app on that account.</li>
        <li>2. Choose repositories. Pick yours, or an organization you administer.</li>
        <li>3. Sync repositories. Open pull requests land in the review queue.</li>
      </ol>
    </PageFrame>
  )
}
