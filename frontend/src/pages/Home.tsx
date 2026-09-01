/**
 * M7 首页：四模块 ins 网格 3 列布局。
 * ① 词云：点击词跳 /search?q=词（直达语义搜索）—— 首页没有搜索框，词云就是入口
 * ② 足迹热力图：30 天网格，莫兰迪色阶，悬停 tooltip
 * ③ 书架画廊：最近书封面墙（3 列 3:2 胶片感）
 * ④ 数据成就：4 数字（书/标注/笔记/单集）大号 Light 字体
 * 全部数据来自 GET /api/home（数据驱动，无假数据）。
 */
import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { getHome, type HomeData } from '../lib/api'

const CLOUD_COLORS = ['#a8b8c8', '#c8b8a8', '#b8a8a8', '#a8c8b8', '#b8a8c8', '#c8c8a8']

export default function Home() {
  const [data, setData] = useState<HomeData | null>(null)
  const [err, setErr] = useState<string>('')
  const nav = useNavigate()

  useEffect(() => {
    getHome().then(setData).catch(e => setErr(e.message))
  }, [])

  if (err) {
    return (
      <div className="home-empty">
        <h2>加载失败</h2>
        <p className="muted">{err}</p>
      </div>
    )
  }
  if (!data) {
    return (
      <div className="home-empty">
        <h2>沉淀加载中…</h2>
        <p className="muted">从你的标注和笔记里提取主题词</p>
      </div>
    )
  }

  // ---------- 空态 ----------
  const isEmpty = data.stats.books === 0 && data.stats.annotations === 0
  if (isEmpty) {
    return (
      <div className="home-empty">
        <h2>你的知识宇宙还没开始</h2>
        <p className="muted">导入第一集播客，开始把声音沉淀成书。</p>
        <Link to="/edit" className="cta">去编辑页 →</Link>
      </div>
    )
  }

  const maxCount = Math.max(1, ...data.footprint.map(f => f.count))
  const heatmapColor = (c: number) => {
    if (c === 0) return 'var(--c-foot-0)'
    const t = c / maxCount
    // 浅灰 → 雾蓝 → 灰蓝 → 陶土橘（莫兰迪色阶）
    if (t < 0.34) return 'var(--c-foot-1)'
    if (t < 0.67) return 'var(--c-foot-2)'
    return 'var(--c-foot-3)'
  }

  const onWordClick = (word: string) => {
    nav(`/search?q=${encodeURIComponent(word)}`)
  }

  const stats = data.stats
  const statsCards: { label: string; value: number; suffix?: string }[] = [
    { label: '沉淀书', value: stats.books, suffix: '本' },
    { label: '划线', value: stats.annotations, suffix: '条' },
    { label: '笔记', value: stats.notes, suffix: '条' },
    { label: '单集', value: stats.episodes, suffix: '集' },
  ]

  return (
    <div className="home">
      {/* Hero */}
      <section className="home-hero">
        <p className="eyebrow">YOUR KNOWLEDGE COSMOS</p>
        <h1>把听过的，<br />变成读得到的。</h1>
        <p className="hero-sub muted">
          {stats.books} 本书 · {stats.annotations} 条划线 · {stats.notes} 条笔记
          {data.word_cloud.length > 0 && <> · 主题词 {data.word_cloud.length} 个</>}
        </p>
      </section>

      {/* 数据成就（4 数字一行） */}
      <section className="home-section home-stats" aria-label="数据成就">
        {statsCards.map(s => (
          <div key={s.label} className="stat-card">
            <div className="stat-value">
              {s.value}<span className="stat-suffix">{s.suffix}</span>
            </div>
            <div className="stat-label muted">{s.label}</div>
          </div>
        ))}
      </section>

      {/* ins 3 列网格主体：词云 / 足迹 / 书架画廊 */}
      <section className="home-grid">
        {/* ① 词云 */}
        <div className="grid-cell cell-cloud" aria-label="主题词云">
          <div className="cell-head">
            <h3>主题词云</h3>
            <span className="muted small">从标注与摘要提取 · 点击直达搜索</span>
          </div>
          {data.word_cloud.length === 0 ? (
            <p className="muted small">还没有标注/笔记，主题词会在你划线后出现</p>
          ) : (
            <div className="word-cloud">
              {data.word_cloud.map((w, i) => {
                // 字号 14-32 px 映射；weight 0.4-1.0
                const size = 14 + Math.round((w.weight - 0.4) * 30)
                const color = CLOUD_COLORS[i % CLOUD_COLORS.length]
                return (
                  <button
                    key={w.word}
                    className="word-chip"
                    style={{
                      fontSize: `${size}px`,
                      background: color,
                    }}
                    onClick={() => onWordClick(w.word)}
                    title={`搜索「${w.word}」`}
                  >
                    {w.word}
                  </button>
                )
              })}
            </div>
          )}
        </div>

        {/* ② 足迹热力图 */}
        <div className="grid-cell cell-foot" aria-label="近 30 天沉淀足迹">
          <div className="cell-head">
            <h3>沉淀足迹</h3>
            <span className="muted small">近 30 天</span>
          </div>
          <div className="heatmap">
            {data.footprint.map(f => (
              <div
                key={f.date}
                className="hm-cell"
                style={{ background: heatmapColor(f.count) }}
                title={`${f.date} · 沉淀 ${f.count}`}
              >
                {f.count > 0 ? f.count : ''}
              </div>
            ))}
          </div>
          <div className="hm-legend muted small">
            <span>少</span>
            <span className="hm-legend-grid">
              <i style={{ background: 'var(--c-foot-0)' }} />
              <i style={{ background: 'var(--c-foot-1)' }} />
              <i style={{ background: 'var(--c-foot-2)' }} />
              <i style={{ background: 'var(--c-foot-3)' }} />
            </span>
            <span>多</span>
          </div>
        </div>

        {/* ③ 书架画廊 */}
        <div className="grid-cell cell-books" aria-label="最近书架">
          <div className="cell-head">
            <h3>书架画廊</h3>
            <Link to="/books" className="cell-more">全部 →</Link>
          </div>
          {data.books_recent.length === 0 ? (
            <p className="muted small">还没有书</p>
          ) : (
            <div className="book-gallery">
              {data.books_recent.map(b => (
                <Link key={b.id} to={`/books/${b.id}`} className="gallery-card">
                  <div className="gallery-cover">
                    {b.cover_url ? (
                      <img src={b.cover_url} alt={b.title} loading="lazy" />
                    ) : (
                      <div className="cover-ph">{b.title.slice(0, 2)}</div>
                    )}
                  </div>
                  <div className="gallery-meta">
                    <div className="gallery-title">{b.title}</div>
                    <div className="muted small">{b.chapters} 章 · {b.paras} 段</div>
                  </div>
                </Link>
              ))}
            </div>
          )}
        </div>
      </section>
    </div>
  )
}
