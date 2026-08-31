/** 书架页：卡片三列 ins 网格；点击卡片提示 M5 开发中。 */
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { listBooks, type Book } from '../lib/api'

export default function Books() {
  const [books, setBooks] = useState<Book[]>([])
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    listBooks().then(setBooks).catch(e => setErr(String(e)))
  }, [])

  return (
    <div className="page">
      <header className="topbar">
        <h1 className="title-xl">书架</h1>
        <Link className="btn-outline" to={`/edit/6a7b23ba17676351c570589d`}>去编辑示例单集</Link>
      </header>

      {err && <div className="err">{err}</div>}

      {books.length === 0 && !err && (
        <div className="empty">
          <p>书架还是空的。</p>
          <Link className="btn-primary" to={`/edit/6a7b23ba17676351c570589d`}>
            先把示例单集加入书架
          </Link>
        </div>
      )}

      <div className="book-grid">
        {books.map(b => (
          <Link className="book-card" key={b.id} to={`/books/${b.id}`}>
            <div className="cover-wrap">
              {b.cover_url
                ? <img className="cover" src={b.cover_url} alt="" />
                : <div className="cover placeholder">封面</div>}
              <span className="version">v{b.version}</span>
            </div>
            <div className="card-body">
              <h3 className="card-title">{b.title}</h3>
              <div className="card-meta">
                <span>{b.chapter_count} 章</span>
                <span>{b.para_count} 段</span>
                <span>{new Date(b.created_at).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })}</span>
              </div>
            </div>
          </Link>
        ))}
      </div>

      {books.length > 0 && (
        <p className="hint">点击卡片打开书（M5 阅读器开发中）。</p>
      )}
    </div>
  )
}
