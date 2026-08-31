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

// ---------- Reader ----------

export type BookPara = {
  id: number
  seq: number
  text: string
  start_ts: number
  end_ts: number
}

export type BookChapter = {
  id: number
  seq: number
  title: string
  paras: BookPara[]
}

export type Annotation = {
  id: number
  book_id: number
  book_para_id: number
  offset_start: number
  offset_end: number
  color: 'blue' | 'yellow' | 'green' | 'pink'
  note_text: string | null
  created_at: string
  para_text?: string
  chapter_seq?: number
  book_title?: string
  cover_url?: string | null
}

export type BookFull = Book & {
  chapters: BookChapter[]
  annotations: Annotation[]
  audio_url?: string
  episode_eid?: string
}

export type SearchHit = {
  para_id: number
  book_id: number
  chapter_id: number
  para_seq: number
  para_text: string
  start_ts: number
  end_ts: number
  chapter_title: string
  chapter_seq: number
  book_title: string
  cover_url: string | null
}

// ---------- 编辑器（M4） ----------

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

// ---------- 阅读器 + 标注（M5） ----------

export function getBook(id: string | number) {
  return request<BookFull>(`/api/books/${id}`)
}

export function createAnnotation(bookId: number, payload: {
  book_para_id: number; offset_start: number; offset_end: number;
  color?: Annotation['color']; note_text?: string | null;
}) {
  return request<Annotation>(`/api/books/${bookId}/annotations`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function deleteAnnotation(id: number) {
  return request<{ status: string; id: number }>(`/api/annotations/${id}`, {
    method: 'DELETE',
  })
}

export function listAnnotations(bookId?: number) {
  const q = bookId ? `?book_id=${bookId}` : ''
  return request<{ count: number; rows: Annotation[] }>(`/api/annotations${q}`)
}

export function searchBookParas(q: string, top_k = 10) {
  const params = new URLSearchParams({ q, top_k: String(top_k) })
  return request<{ engine: string; query: string; hits: number; rows: SearchHit[] }>(
    `/api/search?${params.toString()}`,
  )
}

export function fmtTs(s: number) {
  const sec = Math.max(0, Math.floor(s))
  const m = Math.floor(sec / 60)
  const r = sec % 60
  return `${m}:${r.toString().padStart(2, '0')}`
}

