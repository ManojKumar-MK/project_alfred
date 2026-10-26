/** Split unified diff text into files and typed lines for display. */

export interface DiffLine {
  kind: 'add' | 'remove' | 'context' | 'hunk' | 'meta'
  text: string
  oldNumber: number | null
  newNumber: number | null
}

export interface DiffFile {
  path: string
  lines: DiffLine[]
  additions: number
  deletions: number
  binary: boolean
}

export function parseDiff(text: string): DiffFile[] {
  const files: DiffFile[] = []
  let current: DiffFile | null = null
  let oldLine = 0
  let newLine = 0
  for (const raw of text.split('\n')) {
    if (raw.startsWith('diff --git ')) {
      const match = /^diff --git a\/(.+?) b\/(.+)$/.exec(raw)
      current = { path: match ? match[2] : raw.slice(11), lines: [], additions: 0, deletions: 0, binary: false }
      files.push(current)
      continue
    }
    if (!current) continue
    if (raw.startsWith('@@')) {
      const match = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(raw)
      oldLine = match ? Number(match[1]) : 0
      newLine = match ? Number(match[2]) : 0
      current.lines.push({ kind: 'hunk', text: raw, oldNumber: null, newNumber: null })
    } else if (raw.startsWith('Binary files')) {
      current.binary = true
      current.lines.push({ kind: 'meta', text: raw, oldNumber: null, newNumber: null })
    } else if (
      raw.startsWith('index ') ||
      raw.startsWith('--- ') ||
      raw.startsWith('+++ ') ||
      raw.startsWith('new file') ||
      raw.startsWith('deleted file') ||
      raw.startsWith('similarity') ||
      raw.startsWith('rename ') ||
      raw.startsWith('old mode') ||
      raw.startsWith('new mode')
    ) {
      if (!raw.startsWith('index ') && !raw.startsWith('--- ') && !raw.startsWith('+++ ')) {
        current.lines.push({ kind: 'meta', text: raw, oldNumber: null, newNumber: null })
      }
    } else if (raw.startsWith('+')) {
      current.additions += 1
      current.lines.push({ kind: 'add', text: raw.slice(1), oldNumber: null, newNumber: newLine++ })
    } else if (raw.startsWith('-')) {
      current.deletions += 1
      current.lines.push({ kind: 'remove', text: raw.slice(1), oldNumber: oldLine++, newNumber: null })
    } else if (raw.startsWith('\\')) {
      current.lines.push({ kind: 'meta', text: raw, oldNumber: null, newNumber: null })
    } else if (raw !== '' || current.lines.length) {
      current.lines.push({ kind: 'context', text: raw.slice(1), oldNumber: oldLine++, newNumber: newLine++ })
    }
  }
  for (const file of files) {
    while (file.lines.length && file.lines[file.lines.length - 1].kind === 'context' && !file.lines[file.lines.length - 1].text) {
      file.lines.pop()
    }
  }
  return files
}

export interface SplitRow {
  kind: 'pair' | 'hunk' | 'meta'
  left: DiffLine | null
  right: DiffLine | null
}

/** Pair a file's lines for side-by-side display: removals beside the additions that replace them. */
export function splitRows(lines: DiffLine[]): SplitRow[] {
  const rows: SplitRow[] = []
  let removed: DiffLine[] = []
  let added: DiffLine[] = []
  const flush = () => {
    for (let index = 0; index < Math.max(removed.length, added.length); index += 1) {
      rows.push({ kind: 'pair', left: removed[index] ?? null, right: added[index] ?? null })
    }
    removed = []
    added = []
  }
  for (const line of lines) {
    if (line.kind === 'remove') {
      if (added.length) flush()
      removed.push(line)
    } else if (line.kind === 'add') {
      added.push(line)
    } else {
      flush()
      if (line.kind === 'context') rows.push({ kind: 'pair', left: line, right: line })
      else rows.push({ kind: line.kind, left: line, right: null })
    }
  }
  flush()
  return rows
}
