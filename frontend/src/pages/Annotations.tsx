/** M5 标注列表页：按书分组，点划线回跳 /books/:id?para=xxx，可删除单条。 */
import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import type { Annotation } from '../lib/api'
import { deleteAnnotation, listAnnotations } from '../lib/api'

export default function Annotations() {
  const [rows, setRows] = useState<Annotation[]>([])
  const [busy, setBusy] = useState(true)
  const [err, setErr] = useState<string | null>(null)
  const nav = useNavigate()

  const load = () => {
    setBusy(true); setErr(null)
    listAnnotations()
      .then(r => setRows(r.rows))
      .catch(e => setErr(String(e)))
      .finally(() => setBusy(false))
  }
  useEffect(() => { load() }, [])

  const remove = async (id: number) => {
    if (!confirm('删除这条标注？（划线和笔记都会删除）')) return
    try {
      await deleteAnnotation(id)
      setRows(rs => rs.filter(r => r.id !== id))
    } catch (e: any) {
      alert('删除失败：' + (e.message || e))
    }
  }

  // 按书分组
  const groups = new Map<number, Annotation[]>()
  for (const a of rows) {
    if (!groups.has(a.book_id)) groups.set(a.book_id, [])
    groups.get(a.book_id)!.push(a)
  }

  const formatDate = (s?: string) => {
    if (!s) return ''
    try {
      const d = new Date(s + 'Z')
      return d.toLocaleString('zh-CN', { hour12: false, timeZone: 'Asia/Shanghai' })
    } catch { return s }
  }

  if (busy) return <div className="page loading">加载标注…</div>

  return (
    <div className="page">
      <div className="topbar">
        <h1 className="title-xl" style={{ margin: 0 }}>我的标注</h1>
        <div className="row-actions">
          <button className="btn-link" onClick={load}>刷新</button>
          <button className="btn-outline" onClick={() => nav('/books')}>← 书架</button>
        </div>
      </div>

      {err && <div className="panel thin" style={{ color: '#a0463a' }}>{err}</div>}

      {groups.size === 0 && (
        <div className="empty">
          <p>还没有任何标注。</p>
          <Link className="btn-primary" to="/books">去书架打开一本书，选中文字就能划线和记笔记 →</Link>
        </div>
      )}

      {Array.from(groups.entries()).map(([bookId, list]) => {
        const sample = list[0]
        const total = list.filter(a => a.note_text).length
        return (
          <section key={bookId} className="ann-group">
            <div className="ann-header">
              {sample.cover_url
                ? <img className="cover" src={sample.cover_url} alt="" />
                : <div className="cover placeholder" />}
              <div>
                <h3>
                  <Link to={`/books/${bookId}`}>{sample.book_title ?? `Book #${bookId}`}</Link>
                </h3>
                <div className="muted">
                  {list.length} 条划线 · {total} 条笔记
                </div>
              </div>
              <Link className="btn-outline" to={`/books/${bookId}`}>打开书</Link>
            </div>

            {list.map(a => {
              const quote = (a.para_text ?? '').slice(a.offset_start, a.offset_end)
              return (
                <div key={a.id} className="ann-item">
                  <div>
                    <p className="ann-quote">
                      <mark className={`hl hl-${a.color}`}>{quote || '(空)'}</mark>
                    </p>
                    <div className="ann-meta">
                      章 {a.chapter_seq ?? '-'} · 段 · {formatDate(a.created_at)}
                      <span className="tag warn" style={{
                        marginLeft: 10, color: '#3a3a38',
                        border: 0,
                        background:
                          a.color === 'blue' ? 'rgba(124,143,166,0.26)' :
                          a.color === 'yellow' ? 'rgba(236,209,123,0.30)' :
                          a.color === 'green' ? 'rgba(150,193,154,0.28)' :
                          'rgba(227,162,178,0.28)'
                      }}>
                        {a.color}
                      </span>
                    </div>
                    {a.note_text && <div className="ann-note">{a.note_text}</div>}
                    <div style={{ marginTop: 10 }}>
                      <Link className="btn-link"
                            to={`/books/${bookId}?para=${a.book_para_id}`}>
                        在书中定位 →
                      </Link>
                    </div>
                  </div>
                  <button className="ann-del" onClick={() => remove(a.id)}>删除</button>
                </div>
              )
            })}
          </section>
        )
      })}
    </div>
  )
}
