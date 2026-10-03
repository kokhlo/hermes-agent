import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { MarkdownTextContent } from './markdown-text'

// Regression for #82140: a plain filesystem href in assistant markdown
// (`[report](/home/user/report.md)`) rendered as a bare dead anchor —
// file:// is blocked in the renderer, and on a remote gateway the path
// isn't on this disk at all. Such links must route through the preview
// pipeline (PreviewAttachment → normalizeOrLocalPreviewTarget), which
// resolves the path at VIEW time against the session's backend: local
// connections read the file directly, remote connections fetch it over the
// authenticated /api/fs bridge. Media-extension paths keep their inline
// player instead.
describe('MarkdownLink filesystem hrefs', () => {
  afterEach(cleanup)

  it('routes an absolute file path link through the preview attachment', async () => {
    render(<MarkdownTextContent isRunning={false} text="Wrote it: [report](/home/user/report.md)" />)

    // PreviewAttachment paints the filename + an Open preview button —
    // that's the view-time door, not a dead <a>.
    await screen.findByText('report.md')
    expect(screen.getByRole('button', { name: 'Open preview' })).toBeTruthy()
    expect(document.querySelector('a[href="/home/user/report.md"]')).toBeNull()
  })

  it('routes file:// and ~/ links the same way', async () => {
    render(
      <MarkdownTextContent isRunning={false} text={'See [notes](file:///srv/data/notes.txt) and [todo](~/todo.md)'} />
    )

    await screen.findByText('notes.txt')
    await screen.findByText('todo.md')
    expect(screen.getAllByRole('button', { name: 'Open preview' })).toHaveLength(2)
  })

  it('routes angle-bracket file links whose path contains spaces', async () => {
    // CommonMark requires `<...>` destinations for paths with spaces; the
    // old FILE_LINK_RE charset `[^\)\s]*` couldn't match them, so the link
    // fell through to harden and rendered as "label [blocked]".
    render(<MarkdownTextContent isRunning={false} text={'See [notes](<~/My Notes/todo with spaces.md>)'} />)

    await screen.findByText('todo with spaces.md')
    expect(screen.getByRole('button', { name: 'Open preview' })).toBeTruthy()
    expect(screen.queryByText(/blocked/)).toBeNull()
  })

  it('routes percent-encoded file links through the same preview pipeline', async () => {
    // Markdown renderers emit the href percent-encoded; the renderer keeps
    // the encoded form in the card label, and the electron side retries the
    // decoded on-disk path when the file is opened.
    render(<MarkdownTextContent isRunning={false} text={'See [notes](~/My%20Notes/todo%20with%20spaces.md)'} />)

    await screen.findByText('todo%20with%20spaces.md')
    expect(screen.getByRole('button', { name: 'Open preview' })).toBeTruthy()
  })

  it('renders a media player for a media-extension path link', async () => {
    const { container } = render(<MarkdownTextContent isRunning={false} text="[clip](/tmp/demo.mp4)" />)

    await waitFor(() => expect(container.querySelector('video')).not.toBeNull())
    expect(container.querySelector('a[href="/tmp/demo.mp4"]')).toBeNull()
  })

  it('routes a workspace-relative file link but leaves fragments and web links alone', async () => {
    render(
      <MarkdownTextContent
        isRunning={false}
        text={'[frag](#section-2) and [rel](docs/guide.md) and [site](https://example.com)'}
      />
    )

    // A relative destination names a file in the session's working directory,
    // so it reaches the preview rail exactly like an absolute path does
    // (#131842) — rendered bare, Electron's window-open policy refused the
    // click and the deliverable was unclickable. A fragment is the app's own
    // HashRouter and an https href is the web; neither is a file.
    await screen.findByText('guide.md')

    const anchors = screen.getAllByRole('link').map(link => link.getAttribute('href'))

    expect(anchors).toEqual(['#section-2', 'https://example.com/'])
  })

  it('leaves a relative link with no file extension out of the preview pipeline', () => {
    // `[plans](pricing)` is a web-style relative link, not a file: routing it
    // to a preview would trade a live link for a dead one.
    render(<MarkdownTextContent isRunning={false} text={'[plans](pricing)'} />)

    expect(screen.queryByRole('button', { name: 'Open preview' })).toBeNull()
    expect(screen.queryByRole('link')).toBeNull()
  })
})
