// @vitest-environment jsdom
import type { ThreadMessage } from '@assistant-ui/react'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it } from 'vitest'

import { $toolDisclosureStates, setToolViewMode } from '@/store/tool-view'

import { assistantMessage, stubThreadEnvironment, stubThreadViewportSize, ThreadRuntime } from '../test-utils'
import { Thread } from '../thread'

stubThreadEnvironment()
stubThreadViewportSize()

// `.md` is a real TLD, so the bare-domain branch of URL_RE treats these paths as
// links — and `pretty` then swaps the visible text for the fetched page title.
const PATH = 'C:\\Users\\me\\projects\\myrepo\\README.md'
const REMOTE = 'https://example.com/traces/run-42'

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
        result: { success: false, error: `Refusing to overwrite unread file ${PATH}. See ${REMOTE}.` }
      }
    ]
  } as ThreadMessage
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
  const row = container.querySelector('[data-tool-row]')!
  act(() => {
    fireEvent.click(row.querySelector('button[aria-expanded]')!)
  })

  // `getByText` with a predicate also matches every ancestor of the match, so
  // walk down to the innermost element carrying the text.
  function innermost(container: HTMLElement, needle: string): HTMLElement {
    const hits = Array.from(container.querySelectorAll<HTMLElement>('*')).filter(
      element => element.textContent?.includes(needle) && !Array.from(element.children).some(child => child.textContent?.includes(needle))
    )
    expect(hits).toHaveLength(1)
    return hits[0]
  }

  const error = await waitFor(() => {
    const node = innermost(document.body, 'Refusing to overwrite')
    expect(node).toBeTruthy()
    return node
  })

  // The path is rendered verbatim, twice over (linkified labels appeared twice
  // in the original report), and never swapped for a page title.
  expect(error.textContent).toContain(PATH)
  expect(error.textContent).not.toMatch(/dealsbe|AI Tools Directory/)

  const links = Array.from(error.querySelectorAll('a')).map(anchor => anchor.getAttribute('href'))
  expect(links.filter(Boolean)).toEqual([REMOTE])
  // The bare filename is not a link, so it contributes no href to copy or open.
  expect(Array.from(error.querySelectorAll('a')).some(anchor => /readme\.md/i.test(anchor.getAttribute('href') ?? ''))).toBe(
    false
  )
})
