/** A model change must bypass both an explicit resume and the keep-alive PTY. */
export function freshChatUrl(href: string, basePath = ''): string {
  const url = new URL(href)
  url.pathname = `${basePath}/chat`
  url.searchParams.delete('resume')
  url.searchParams.delete('learn')
  url.searchParams.set('fresh', '1')
  return url.toString()
}
