"use client"

import { Suspense } from "react"
import { ReviewWorkspace } from "@/components/review-workspace"

export default function ReviewPage() {
  return (
    <Suspense fallback={<div className="p-6 text-sm text-muted-foreground">Loading review…</div>}>
      <ReviewWorkspace />
    </Suspense>
  )
}
