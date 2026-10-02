// @vitest-environment jsdom
import type { ThreadMessage } from '@assistant-ui/react'
import { act, cleanup, fireEvent, render, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it } from 'vitest'

import { $toolDisclosureStates, setToolViewMode } from '@/store/tool-view'

import { assistantMessage, stubThreadEnvironment, stubThreadViewportSize, ThreadRuntime } from '../test-utils'
import { Thread } from '../thread'

stubThreadEnvironment()
stubThreadViewportSize()

// `.md` is a real TLD, so the bare-domain branch of URL_RE reads this path as a
// link — and `pretty` then swaps the visible text for that page's title.
const PATH = 'C:\\Users\\me\\projects\\myrepo\\README.md'
const REMOTE = 'https://example.com/traces/run-42'
const REFUSAL = 'Refusing to overwrite unread file'

function errorMessage(): ThreadMessage {
  return {
    ...assistantMessage(),
    content: [
      {
        type: 'tool-call',
        toolCallId: 'call-write',
        toolName: 'write_file',
        args: { path: PATH, content: '# hi' },
        argsText: '{}',
        result: { success: false, error: `${REFUSAL} ${PATH}. See ${REMOTE}.` }
      }
    ]
  } as ThreadMessage
}

// A predicate query also matches every ancestor of the match, so descend to the
// innermost element carrying the text — here, the linkified summary itself.
function summary(root: HTMLElement): HTMLElement {
  const hits = Array.from(root.querySelectorAll<HTMLElement>('*')).filter(
    el => el.textContent?.includes(REFUSAL) && !Array.from(el.children).some(child => child.textContent?.includes(REFUSAL))
  )

  expect(hits).toHaveLength(1)

  return hits[0]
}

beforeEach(() => {
  $toolDisclosureStates.set({})
  setToolViewMode('product')
})

afterEach(() => {
  cleanup()
  setToolViewMode('product')
  $toolDisclosureStates.set({})
})

it('keeps a bare filename in an error summary as written while still linking an explicit URL', async () => {
  const { container } = render(
    <ThreadRuntime messages={[errorMessage()]}>
      <Thread />
    </ThreadRuntime>
  )

  await waitFor(() => expect(container.querySelector('[data-tool-row]')).not.toBeNull())
  act(() => {
    fireEvent.click(container.querySelector('[data-tool-row] button[aria-expanded]')!)
  })

  const error = await waitFor(() => summary(container))

  // The path survives verbatim and never takes a third party's page title, and
  // the one explicit URL in the same string is still linked.
  expect(error.textContent).toContain(PATH)
  expect(error.textContent).not.toMatch(/dealsbe|AI Tools Directory/)
  expect(Array.from(error.querySelectorAll('a')).map(anchor => anchor.getAttribute('href'))).toEqual([REMOTE])
})