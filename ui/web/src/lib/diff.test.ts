import { describe, expect, it } from 'vitest'
import { parseDiff, splitRows } from './diff'

describe('splitRows', () => {
  it('pairs removals beside their additions and keeps context on both sides', () => {
    const [file] = parseDiff(
      ['diff --git a/a.txt b/a.txt', '@@ -1,4 +1,4 @@', ' same', '-old one', '-old two', '+new one', ' tail', '+added'].join('\n'),
    )
    const rows = splitRows(file.lines).map((row) => [row.kind, row.left?.text ?? null, row.right?.text ?? null])
    expect(rows).toEqual([
      ['hunk', '@@ -1,4 +1,4 @@', null],
      ['pair', 'same', 'same'],
      ['pair', 'old one', 'new one'],
      ['pair', 'old two', null],
      ['pair', 'tail', 'tail'],
      ['pair', null, 'added'],
    ])
  })

  it('starts a new pairing when a removal follows additions', () => {
    const [file] = parseDiff(['diff --git a/a b/a', '@@ -1,2 +1,2 @@', '+a', '-b'].join('\n'))
    expect(splitRows(file.lines).map((row) => [row.left?.text ?? null, row.right?.text ?? null])).toEqual([
      ['@@ -1,2 +1,2 @@', null],
      [null, 'a'],
      ['b', null],
    ])
  })
})
