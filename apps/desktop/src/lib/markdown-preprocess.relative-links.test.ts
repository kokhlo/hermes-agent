import { describe, expect, it } from 'vitest'

import { preprocessMarkdown } from '@/lib/markdown-preprocess'

// A relative markdown destination (`[report](docs/report.md)`) names a file in
// the session's working directory — the same machine the preview rail resolves
// against. Left alone it renders as a bare `<a>`, which Streamdown's URL
// hardening and Electron's window-open policy both refuse, so the deliverable is
// unclickable. Routing it through `#preview/…` keeps it working from every
// machine that opens the transcript: the target is resolved at VIEW time
// against that session's cwd.
describe('relative file-path markdown links', () => {
  it('routes a bare relative document link through the preview pipeline', () => {
    const output = preprocessMarkdown('Wrote it: [report](docs/report.md)')

    expect(output).toContain('[report](#preview/docs%2Freport.md)')
    expect(output).not.toContain('(docs/report.md)')
  })

  it('routes a dot-slash relative link the same way', () => {
    const output = preprocessMarkdown('[summary](./docs/summary.md)')

    expect(output).toContain('[summary](#preview/.%2Fdocs%2Fsummary.md)')
  })

  it('keeps a media-extension relative link on the inline player path', () => {
    const output = preprocessMarkdown('[clip](assets/demo.mp4)')

    expect(output).toContain('[clip](#media:assets%2Fdemo.mp4)')
  })

  it('leaves an in-document fragment anchor alone', () => {
    const output = preprocessMarkdown('[section](#section-2)')

    expect(output).toContain('(#section-2)')
    expect(output).not.toContain('#preview/')
  })

  it('leaves a bare relative web-style link (no file extension) alone', () => {
    // `pricing` names a route on some site, not a file in the workspace:
    // guessing a file for it would send a legitimate link to a dead preview.
    const output = preprocessMarkdown('[plans](pricing)')

    expect(output).toContain('[plans](pricing)')
    expect(output).not.toContain('#preview/')
  })

  it('does not treat a URL path segment as a relative file link', () => {
    const output = preprocessMarkdown('See [the docs](https://example.com/docs/guide.md)')

    expect(output).toContain('https://example.com/docs/guide.md')
    expect(output).not.toContain('#preview/')
  })

  it('leaves the image form of a relative link on the inline media pipeline', () => {
    const output = preprocessMarkdown('![shot](assets/shot.png)')

    expect(output).toContain('![shot](assets/shot.png)')
  })

  it('routes an angle-bracket relative link whose path contains spaces', () => {
    const output = preprocessMarkdown('[notes](<./docs/My Notes/todo.md>)')

    expect(output).toContain('[notes](#preview/.%2Fdocs%2FMy%20Notes%2Ftodo.md)')
  })

  it('does not rewrite a relative link inside a fenced code block', () => {
    const fence = '```'

    const output = preprocessMarkdown(
      ['Example:', '', `${fence}markdown`, '[report](docs/report.md)', fence].join('\n')
    )

    expect(output).toContain('[report](docs/report.md)')
  })

  it('leaves a relative link whose extension is a version-like token alone', () => {
    // `release.v2` is a label, not a filename — the trailing `.v2` must not
    // make it a file target.
    const output = preprocessMarkdown('[release](release.v2)')

    expect(output).toContain('[release](release.v2)')
  })
})