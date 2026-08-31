/** 后端 API 客户端（fetch 封装，抛带 detail 的错误）。 */

const BASE = ''

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(BASE + url, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    let detail = text
    try {
      detail = JSON.parse(text).detail ?? text
    } catch { /* ignore */ }
    throw new Error(`HTTP ${res.status} ${detail || res.statusText}`)
  }
  return (await res.json()) as T
}

export type Paragraph = {
  seq: number
  text: string
  start_ts: number
  end_ts: number
  is_ad?: boolean
  ad_reason?: string
}

export type Quote = { text: string; start_ts: number; end_ts: number; reason: string }
export type Outline = { seq: number; title: string; start_ts: number; end_ts: number }

export type TranscriptData = {
  eid: string
  title: string
  cover_url: string | null
  duration: number | null
  series_name: string | null
  summary: string | null
  outline: Outline[]
  quotes: Quote[]
  paragraphs: Paragraph[]
  ad_paragraphs: { seq: number; reason: string }[]
  transcript_status: string
  process_status: string
}

export type Book = {
  id: number
  episode_id: number
  title: string
  cover_url: string | null
  created_at: string
  version: number
  chapter_count: number
  para_count: number
}

export type Edit =
  | { para_seq: number; action: 'keep' }
  | { para_seq: number; action: 'replace'; new_text: string }
  | { para_seq: number; action: 'delete' }

export function getTranscript(eid: string) {
  return request<TranscriptData>(`/api/editor/episodes/${eid}/transcript`)
}

export function createBook(eid: string, edits: Edit[]) {
  return request<Book>(`/api/editor/episodes/${eid}/book`, {
    method: 'POST',
    body: JSON.stringify({ edits }),
  })
}

export function listBooks() {
  return request<{ books: Book[] }>('/api/editor/books').then(r => r.books)
}

export function fmtTs(s: number) {
  const sec = Math.max(0, Math.floor(s))
  const m = Math.floor(sec / 60)
  const r = sec % 60
  return `${m}:${r.toString().padStart(2, '0')}`
}
