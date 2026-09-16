import { api } from '../../data/api'

export interface CommentSubmission { message: string; docPaths: string[] }
export interface CommentTarget { label: string; submit: (submission: CommentSubmission) => void | Promise<void> }
type CommentSender = (message: string, docPaths: string[]) => void | Promise<void>

function routedTarget(label: string, send: CommentSender): CommentTarget {
  return { label, submit: submission => send(submission.message, submission.docPaths) }
}
export function newSessionTarget(navigate: (path: string) => void, opts?: { name?: string }): CommentTarget {
  return routedTarget('Send to a new chat', async (message, docPaths) => {
    let destination = 'chat/new'
    try {
      const { key } = await api.createChatSession({ name: opts?.name || 'Document comments' })
      await api.sendChat(message, key, docPaths.length ? { files: docPaths } : undefined)
      destination = `chat/${key}`
    } catch {}
    navigate(destination)
  })
}
export function sameSessionTarget(send: CommentSender): CommentTarget { return routedTarget('Send to this chat', send) }
export function planningTarget(send: CommentSender): CommentTarget { return routedTarget('Send to planning', send) }
