export function PageFrame({
  eyebrow,
  title,
  note,
  actions,
  children,
}: {
  eyebrow?: string
  title: string
  note?: string
  actions?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden">
      <div className="flex flex-wrap items-end justify-between gap-3 border-b px-4 py-3 md:px-5">
        <div>
          {eyebrow ? <p className="text-[11px] uppercase tracking-[0.16em] text-muted-foreground">{eyebrow}</p> : null}
          <h1 className="text-lg font-medium tracking-tight">{title}</h1>
          {note ? <p className="mt-1 max-w-2xl text-xs leading-relaxed text-muted-foreground">{note}</p> : null}
        </div>
        {actions}
      </div>
      <div className="min-h-0 flex-1 overflow-auto">{children}</div>
    </div>
  )
}

export function Notice({ title, detail }: { title: string; detail?: string }) {
  return (
    <div className="p-6">
      <h1 className="text-lg font-medium">{title}</h1>
      {detail ? <p className="mt-2 max-w-xl text-sm leading-relaxed text-muted-foreground">{detail}</p> : null}
    </div>
  )
}
