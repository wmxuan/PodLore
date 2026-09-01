/**
 * M7 搜索页：词云点击的落点 + 独立搜索入口。
 * - 读取 URL ?q= 自动执行搜索（首页词云点击跳转）
 * - 搜索框 ins 风（圆角、毛玻璃、placeholder 灰）
 * - 结果列表复用 M6 /api/search（hybrid engine）+ 来源卡片（书/章/段）+ score
 * - 命中段附 context_before / context_after（M6 已返回）
 * - 空态：输入引导
 */
import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { searchParas, fmtTs, type SearchResponse, type SearchHit } from '../lib/api'

export default function SearchPage() {
  const [params, setParams] = useSearchParams()
  const [input, setInput] = useState(params.get('q') ?? '')
  const [loading, setLoading] = useState(false)
  const [resp, setResp] = useState<SearchResponse | null>(null)
  const [err, setErr] = useState('')
  const inputRef = useRef<HTMLInputElement | null>(null)
  const nav = useNavigate()

  const doSearch = (q: string) => {
    const query = q.trim()
    if (!query) {
      setResp(null)
      setErr('')
      return
    }
    setLoading(true)
    setErr('')
    searchParas(query, { top_k: 10, engine: 'hybrid', include_context: true })
      .then(r => setResp(r))
      .catch(e => setErr(e.message))
      .finally(() => setLoading(false))
  }

  // URL ?q= 变化时触发（首页词云点击 / 浏览器前进后退）
  useEffect(() => {
    const q = params.get('q') ?? ''
    setInput(q)
    doSearch(q)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params])

  const onSubmit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    const q = input.trim()
    if (!q) return
    setParams(q ? { q } : {})
  }

  const onHitClick = (h: SearchHit) => {
    nav(`/books/${h.book_id}?para=${h.para_id}`)
  }

  return (
    <div className="search-page">
      <section className="search-hero">
        <p className="eyebrow">SEMANTIC SEARCH</p>
        <h1>搜观点，不是搜关键词。</h1>
        <p className="muted small">
          跨书语义召回，把"我读到过什么"找回来。
          {resp && resp.embedding_ready === false && (
            <span className="warn-tag">· 语义引擎未启用（关键词兜底）</span>
          )}
        </p>
      </section>

      <form className="search-form" onSubmit={onSubmit}>
        <input
          ref={inputRef}
          className="search-input"
          type="text"
          placeholder="输入问题或关键词，如「护城河」「欧莱雅 护发」"
          value={input}
          onChange={e => setInput(e.target.value)}
          autoFocus
        />
        <button className="search-btn" type="submit" disabled={loading || !input.trim()}>
          {loading ? '搜索中…' : '搜索'}
        </button>
      </form>

      {err && <div className="search-error">加载失败：{err}</div>}

      {resp && (
        <section className="search-results">
          <div className="results-head muted small">
            <span>找到 {resp.total} 条</span>
            <span className="engine-tag">引擎：{resp.engine}</span>
          </div>
          {resp.results.length === 0 ? (
            <div className="results-empty">
              <p>没有匹配结果。</p>
              <p className="muted small">试试更短的关键词，或换一种说法。</p>
            </div>
          ) : (
            <ul className="hit-list">
              {resp.results.map((h, i) => (
                <li key={`${h.para_id}-${i}`} className="hit-card" onClick={() => onHitClick(h)}>
                  <div className="hit-head">
                    <div className="hit-book">
                      <img src={h.cover_url ?? ''} alt="" loading="lazy" className="hit-cover" />
                      <div>
                        <div className="hit-book-title">{h.book_title}</div>
                        <div className="muted small">
                          {h.chapter_title} · 段 {h.para_seq} · {fmtTs(h.start_ts)}–{fmtTs(h.end_ts)}
                        </div>
                      </div>
                    </div>
                    <div className="hit-score">
                      {typeof h.score === 'number' && (
                        <span className="score-tag" data-engine={h.engine_hit ?? ''}>
                          {h.score.toFixed(3)}
                        </span>
                      )}
                    </div>
                  </div>
                  {h.context_before && (
                    <p className="hit-ctx muted small">… {h.context_before}</p>
                  )}
                  <p className="hit-text">{h.para_text}</p>
                  {h.context_after && (
                    <p className="hit-ctx muted small">{h.context_after} …</p>
                  )}
                  <div className="hit-foot">
                    <Link
                      to={`/books/${h.book_id}?para=${h.para_id}`}
                      className="hit-open"
                      onClick={e => e.stopPropagation()}
                    >
                      在书中打开 →
                    </Link>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}
    </div>
  )
}
