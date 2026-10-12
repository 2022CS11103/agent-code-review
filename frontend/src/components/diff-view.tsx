import type { Finding, SplitRow } from "@/lib/types"
import { cn } from "@/lib/utils"

const KEYWORDS = new Set([
  "def", "return", "import", "from", "if", "else", "elif", "for", "in", "None", "True", "False",
  "class", "and", "or", "not", "with", "as", "try", "except", "raise", "pass", "lambda", "await", "async",
])

export function highlight(text: string) {
  const parts = text.split(/(\s+|[{}()[\].,:=+\-*/<>'"]+)/)
  return parts.map((part, index) => {
    if (KEYWORDS.has(part)) return <span key={index} className="text-[#7ec8c3]">{part}</span>
    if (/^['"]/.test(part)) return <span key={index} className="text-[#d7ba7d]">{part}</span>
    if (part.startsWith("#")) return <span key={index} className="text-muted-foreground">{part}</span>
    return <span key={index}>{part}</span>
  })
}

export function DiffView({
  rows,
  findings,
  selectedId,
  onSelect,
  mode,
}: {
  rows: SplitRow[]
  findings: Finding[]
  selectedId: string | null
  onSelect: (id: string) => void
  mode: "split" | "unified"
}) {
  if (mode === "unified") {
    return (
      <div className="mono text-[12.5px] leading-5">
        {rows.map((row, index) => {
          if (row.left_kind === "ctx" && row.right_kind === "ctx") {
            return <Line key={index} no={row.right_no} text={row.right_text} kind="ctx" findings={findings} selectedId={selectedId} onSelect={onSelect} />
          }
          return (
            <div key={index}>
              {row.left_kind === "del" ? (
                <Line no={row.left_no} text={row.left_text} kind="del" findings={[]} selectedId={selectedId} onSelect={onSelect} prefix="-" />
              ) : null}
              {row.right_kind === "add" ? (
                <Line no={row.right_no} text={row.right_text} kind="add" findings={findings} selectedId={selectedId} onSelect={onSelect} prefix="+" />
              ) : null}
            </div>
          )
        })}
      </div>
    )
  }
  return (
    <div className="mono grid grid-cols-1 text-[12.5px] leading-5 xl:grid-cols-2">
      <div className="min-w-0 border-b xl:border-b-0 xl:border-r">
        {rows.map((row, index) => (
          <Line key={`l${index}`} no={row.left_no} text={row.left_text} kind={row.left_kind} findings={[]} selectedId={null} onSelect={() => undefined} />
        ))}
      </div>
      <div className="min-w-0">
        {rows.map((row, index) => (
          <Line
            key={`r${index}`}
            no={row.right_no}
            text={row.right_text}
            kind={row.right_kind}
            findings={findings}
            selectedId={selectedId}
            onSelect={onSelect}
          />
        ))}
      </div>
    </div>
  )
}

function Line({
  no,
  text,
  kind,
  findings,
  selectedId,
  onSelect,
  prefix,
}: {
  no: number | null
  text: string
  kind: string
  findings: Finding[]
  selectedId: string | null
  onSelect: (id: string) => void
  prefix?: string
}) {
  const hit = findings.find((finding) => no != null && no >= finding.start_line && no <= finding.end_line)
  const selected = hit && hit.id === selectedId
  return (
    <div
      id={hit ? `line-${hit.id}-${no}` : undefined}
      className={cn(
        "grid grid-cols-[3rem_1fr] px-2",
        kind === "add" && "diff-add",
        kind === "del" && "diff-del",
        selected && "diff-mark",
        hit && "cursor-pointer",
      )}
      onClick={() => hit && onSelect(hit.id)}
    >
      <span className="select-none pr-2 text-right text-muted-foreground">{no ?? ""}</span>
      <pre className="overflow-x-auto whitespace-pre">
        {prefix ? <span className="text-muted-foreground">{prefix}</span> : null}
        {highlight(text || " ")}
        {hit ? <span className={`ml-2 text-[10px] uppercase sev-${hit.severity}`}>{hit.rule_id}</span> : null}
      </pre>
    </div>
  )
}
