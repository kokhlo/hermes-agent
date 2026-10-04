// Regression (#91068): the backend's /api/sessions/search payload carries the
// real session title on every hit (web_routers/sessions.py add_lineage_result
// enriches via get_session_rich_row), but the sidebar's search mapper dropped
// it (title: null). sessionTitle() then fell back to the preview — the FTS
// snippet of the matched message — painting rows with raw message content
// (tool JSON, shell commands) as the session name.
import { describe, expect, it } from 'vitest'

import { searchResultToSession } from './index'

describe('searchResultToSession', () => {
  it('uses the backend-provided title for the synthesized row', () => {
    const s = searchResultToSession({
      lineage_root: 'root-1',
      model: 'deepseek-v4-flash',
      role: 'tool',
      session_id: '20260725_132',
      session_started: 1753450000,
      snippet: '{"bytes_written": 1879, "dirs_created": true}',
      source: 'agent',
      title: '  Fact-checking PR review comments  '
    })

    expect(s.title).toBe('Fact-checking PR review comments')
    expect(s.id).toBe('20260725_132')
  })

  it('keeps the FTS snippet as the preview with markers stripped', () => {
    const s = searchResultToSession({
      session_id: 'cron_f7da0e4',
      session_started: null,
      snippet: '...Report "PR #>>>67834<<< still in draft..."',
      role: 'user',
      model: null,
      source: 'cron',
      title: 'PR #67834 CI followup'
    })

    expect(s.title).toBe('PR #67834 CI followup')
    expect(s.preview).toBe('...Report "PR #67834 still in draft..."')
  })

  it('leaves title null when the backend sends none (untitled session)', () => {
    const s = searchResultToSession({
      session_id: '20260720_233',
      session_started: null,
      snippet: '[{"id": "call_d632e8dc7bf740048f242bfe", "c...',
      role: 'assistant',
      model: null,
      source: null
    })

    expect(s.title).toBeNull()
    expect(s.preview).toBe('[{"id": "call_d632e8dc7bf740048f242bfe", "c...')
  })
})

describe('searchResultToSession recency', () => {
  // A search hit is a MESSAGE that matched, so it must read with the message's
  // own date. The conversation's creation time is days earlier on a long-lived
  // session, and rendering that made every hit look stale.
  it('prefers the matched message time over the conversation start', () => {
    const s = searchResultToSession({
      session_id: '20260725_132',
      session_started: 1753450000,
      snippet: 'the message that actually matched',
      role: 'user',
      model: null,
      source: 'agent',
      timestamp: 1756100000
    })

    expect(s.last_active).toBe(1756100000)
  })

  it('prefers the matched message time over the conversation recency', () => {
    // last_active rides on the rich row the backend attaches — it is the
    // CONVERSATION's recency, which for a search hit is not what matched.
    const s = searchResultToSession({
      last_active: 1756200000,
      session_id: '20260725_132',
      session_started: 1753450000,
      snippet: 'an older message inside a busier conversation',
      role: 'assistant',
      model: null,
      source: 'agent',
      timestamp: 1756100000
    })

    expect(s.last_active).toBe(1756100000)
  })

  it('keeps the conversation start as started_at, not the message time', () => {
    // started_at is the conversation's creation date and must stay that way —
    // only the rendered recency follows the matched message.
    const s = searchResultToSession({
      session_id: '20260725_132',
      session_started: 1753450000,
      snippet: 'hit',
      role: 'user',
      model: null,
      source: 'agent',
      timestamp: 1756100000
    })

    expect(s.started_at).toBe(1753450000)
  })

  it('falls back to row recency for an id hit, which matched no message', () => {
    const s = searchResultToSession({
      last_active: 1756200000,
      session_id: '20260725_132',
      session_started: 1753450000,
      snippet: 'Session ID: 20260725_132',
      role: null,
      model: null,
      source: 'agent'
    })

    expect(s.last_active).toBe(1756200000)
  })

  it('falls back to the conversation start when the hit carries no time at all', () => {
    const s = searchResultToSession({
      last_active: null,
      session_id: '20260725_132',
      session_started: 1753450000,
      snippet: 'hit',
      role: 'user',
      model: null,
      source: 'agent',
      timestamp: null
    })

    expect(s.last_active).toBe(1753450000)
  })
})
