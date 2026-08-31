/** M5 阅读器：播放器同步高亮 + 划线/笔记 + 选中搜索。
 *
 *  关键约束（来自 M5 指令）：
 *  1. 播放器同步：用 book_paras.start_ts/end_ts 匹配 currentTime；
 *     点击段落 → audio.currentTime = 段落 start_ts 跳转播放。
 *  2. 自动滚动跟随但不抢：用户手动滚动（wheel/键盘）期间临时暂停自动滚动，
 *     800ms 无手动滚动后恢复；用户主动点击播放按钮或拖动进度条后恢复。
 *  3. 锚定：标注 {book_id, book_para_id, offset_start, offset_end}，书冻结永不错位。
 *  4. 选中搜索：先 SQLite LIKE 占位，交互跑通后 M6 再语义。
 */
import React, { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import type {
  Annotation, BookFull, SearchHit,
} from '../lib/api'
import {
  createAnnotation, fmtTs, getBook, searchBookParas,
} from '../lib/api'

type Color = 'blue' | 'yellow' | 'green' | 'pink'
const COLORS: Color[] = ['blue', 'yellow', 'green', 'pink']

// 把段落文字按该段落的 annotations 拆成若干切片：未标注/标注（带 hl-color/mark class）
function splitParaHighlights(text: string, anns: Annotation[]) {
  if (!anns.length) return [{ text, mark: null as Annotation | null }]
  const slices: { offset: number; end: number; mark: Annotation }[] = []
  anns.forEach(a => slices.push({ offset: a.offset_start, end: a.offset_end, mark: a }))
  slices.sort((a, b) => a.offset - b.offset)
  // 去重覆盖：若重叠就只保留先出现的（SQLite 无重叠约束，前端尽量优雅）
  const keep: typeof slices = []
  let cursor = 0
  for (const s of slices) {
    if (s.end <= s.offset) continue
    if (s.offset < cursor) {
      // 与前一个重叠，取交集后半段或跳过
      if (cursor < s.end) keep.push({ ...s, offset: cursor })
      cursor = Math.max(cursor, s.end)
    } else {
      keep.push(s)
      cursor = s.end
    }
  }
  const out: { text: string; mark: Annotation | null }[] = []
  cursor = 0
  for (const k of keep) {
    if (k.offset > cursor) out.push({ text: text.slice(cursor, k.offset), mark: null })
    out.push({ text: text.slice(k.offset, k.end), mark: k.mark })
    cursor = k.end
  }
  if (cursor < text.length) out.push({ text: text.slice(cursor), mark: null })
  return out
}

export default function Reader() {
  const { id = '' } = useParams()
  const [sp] = useSearchParams()
  const initParaId = Number(sp.get('para') || 0) || undefined
  const navigate = useNavigate()

  const [book, setBook] = useState<BookFull | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [anns, setAnns] = useState<Annotation[]>([])

  const [playing, setPlaying] = useState(false)
  const [currentTime, setCurrentTime] = useState(0)
  const [duration, setDuration] = useState(0)
  const [speed, setSpeed] = useState<1 | 1.25 | 1.5 | 2>(1)
  const audioRef = useRef<HTMLAudioElement | null>(null)

  // 用户是否在「手动」滚动；是则暂停自动跟随
  const userScrollRef = useRef<number>(0)     // 最后手动滚动时间戳
  const autoFollowRef = useRef(true)          // 当前是否应自动跟随
  const containerRef = useRef<HTMLDivElement | null>(null)
  const paraRefs = useRef<Map<number, HTMLElement>>(new Map())

  // 选中文本浮动条
  const selRef = useRef<{
    book_para_id: number; offset_start: number; offset_end: number; selected: string;
    rect: DOMRect;
  } | null>(null)
  const [selVisible, setSelVisible] = useState(false)
  const [selPos, setSelPos] = useState<{ top: number; left: number } | null>(null)
  const [color, setColor] = useState<Color>('blue')

  // 笔记弹层
  const [noteOpen, setNoteOpen] = useState<{ top: number; left: number } | null>(null)
  const [noteText, setNoteText] = useState('')
  const [noteBusy, setNoteBusy] = useState(false)

  // 搜索面板
  const [searchPanel, setSearchPanel] = useState<{ open: boolean; q: string; hits: SearchHit[]; busy: boolean; attachHit?: SearchHit }>(
    { open: false, q: '', hits: [], busy: false }
  )
  const [searchToast, setSearchToast] = useState('')

  // --------- 加载 book + 初始滚动（来自 ?para=） ----------
  useEffect(() => {
    let cancelled = false
    getBook(id).then(b => {
      if (cancelled) return
      setBook(b)
      setAnns(b.annotations || [])
      if (initParaId) {
        // 等 DOM 渲染后滚到对应段落
        requestAnimationFrame(() => {
          requestAnimationFrame(() => {
            const el = paraRefs.current.get(initParaId)
            el?.scrollIntoView({ behavior: 'smooth', block: 'center' })
          })
        })
      }
    }).catch(e => {
      if (!cancelled) setErr(String(e))
    })
    return () => { cancelled = true }
  }, [id, initParaId])

  // --------- 扁平 paragraphs 数组（便于二分查找 active 段） ----------
  const flat = useMemo(() => {
    const arr: { id: number; chapter_id: number; chapter_seq: number;
      seq: number; text: string; start_ts: number; end_ts: number }[] = []
    if (!book) return arr
    for (const ch of book.chapters) {
      for (const p of ch.paras) {
        arr.push({ id: p.id, chapter_id: ch.id, chapter_seq: ch.seq,
          seq: p.seq, text: p.text, start_ts: p.start_ts, end_ts: p.end_ts })
      }
    }
    return arr
  }, [book])

  const activeId = useMemo(() => {
    if (!flat.length) return -1
    // 二分：最大 start_ts <= currentTime 的段
    let lo = 0, hi = flat.length - 1, ans = 0
    while (lo <= hi) {
      const m = (lo + hi) >> 1
      if (flat[m].start_ts <= currentTime) { ans = m; lo = m + 1 }
      else hi = m - 1
    }
    const seg = flat[ans]
    if (seg && currentTime <= seg.end_ts) return seg.id
    // currentTime 超过最后一段
    return -1
  }, [flat, currentTime])

  const activeChapterSeq = useMemo(() => {
    const seg = flat.find(f => f.id === activeId)
    return seg?.chapter_seq ?? 1
  }, [flat, activeId])

  // visited（已读过 = 所有 id < activeId 的段）
  const visitedSet = useMemo(() => {
    const s = new Set<number>()
    if (activeId <= 0) return s
    const idx = flat.findIndex(f => f.id === activeId)
    for (let i = 0; i < idx; i++) s.add(flat[i].id)
    return s
  }, [flat, activeId])

  // --------- 音频绑定 + timeupdate ----------
  useEffect(() => {
    const a = audioRef.current
    if (!a) return
    const onTime = () => setCurrentTime(a.currentTime)
    const onLoaded = () => setDuration(a.duration || 0)
    const onPlay = () => { setPlaying(true); autoFollowRef.current = true }
    const onPause = () => setPlaying(false)
    const onEnded = () => setPlaying(false)
    a.addEventListener('timeupdate', onTime)
    a.addEventListener('loadedmetadata', onLoaded)
    a.addEventListener('play', onPlay)
    a.addEventListener('pause', onPause)
    a.addEventListener('ended', onEnded)
    return () => {
      a.removeEventListener('timeupdate', onTime)
      a.removeEventListener('loadedmetadata', onLoaded)
      a.removeEventListener('play', onPlay)
      a.removeEventListener('pause', onPause)
      a.removeEventListener('ended', onEnded)
    }
  }, [book?.audio_url])

  useEffect(() => {
    if (audioRef.current) audioRef.current.playbackRate = speed
  }, [speed, book?.audio_url])

  // --------- 自动滚动跟随（用户手动滚动时不抢） ----------
  useEffect(() => {
    if (activeId < 0 || !autoFollowRef.current) return
    const el = paraRefs.current.get(activeId)
    if (!el) return
    // 若段落在可视区内部则不强制 scroll（避免用户阅读到中部被拉走）
    const rect = el.getBoundingClientRect()
    const vh = window.innerHeight || 600
    const topPad = 120, bottomPad = 160
    if (rect.top >= topPad && rect.bottom <= vh - bottomPad) return
    el.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }, [activeId])

  // 用户滚动容器 → 暂停自动跟随 800ms
  useEffect(() => {
    const onScroll = () => {
      userScrollRef.current = Date.now()
      autoFollowRef.current = false
    }
    const id = window.setInterval(() => {
      if (!autoFollowRef.current
          && Date.now() - userScrollRef.current > 800
          && playing) {
        // 800ms 没有滚动 + 正在播放 → 恢复自动跟随
        autoFollowRef.current = true
      }
    }, 250)
    window.addEventListener('scroll', onScroll, { passive: true })
    window.addEventListener('wheel', onScroll, { passive: true })
    window.addEventListener('keydown', onScroll)
    return () => {
      window.clearInterval(id)
      window.removeEventListener('scroll', onScroll)
      window.removeEventListener('wheel', onScroll)
      window.removeEventListener('keydown', onScroll)
    }
  }, [playing])

  // --------- 播放 / 暂停 / seek ----------
  const togglePlay = () => {
    const a = audioRef.current
    if (!a) return
    if (!playing) {
      a.play().then(() => { autoFollowRef.current = true }).catch(() => { /* user gesture */ })
    } else {
      a.pause()
    }
  }

  const onSeek = (e: React.ChangeEvent<HTMLInputElement>) => {
    const v = Number(e.target.value)
    if (audioRef.current) audioRef.current.currentTime = v
    setCurrentTime(v)
    autoFollowRef.current = true
  }

  const jumpPara = (p: { id: number; start_ts: number }) => {
    if (audioRef.current) {
      audioRef.current.currentTime = p.start_ts
      audioRef.current.play().then(() => { autoFollowRef.current = true }).catch(() => {})
    }
    const el = paraRefs.current.get(p.id)
    el?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }

  // --------- 段落级 annotations（按 book_para_id 分组） ----------
  const annsByPara = useMemo(() => {
    const m = new Map<number, Annotation[]>()
    for (const a of anns) {
      if (!m.has(a.book_para_id)) m.set(a.book_para_id, [])
      m.get(a.book_para_id)!.push(a)
    }
    return m
  }, [anns])

  // --------- 选中文本：计算偏移 & 浮动条 ----------
  const clearSelection = () => {
    window.getSelection()?.removeAllRanges()
    selRef.current = null
    setSelVisible(false)
    setSelPos(null)
  }

  const handleSelection = () => {
    const sel = window.getSelection()
    if (!sel || sel.isCollapsed || sel.rangeCount === 0) return
    const range = sel.getRangeAt(0)
    // 锚节点 & 终点节点必须都在同一 .reader-para 内
    let paraEl: HTMLElement | null = null
    let n: Node | null = range.startContainer
    while (n) {
      if (n instanceof HTMLElement && n.classList.contains('reader-para')) { paraEl = n; break }
      n = n.parentNode
    }
    if (!paraEl) return
    const n2: Node | null = range.endContainer
    let check: Node | null = n2
    while (check) {
      if (check === paraEl) break
      if (check instanceof HTMLElement && check.classList.contains('reader-para')) return  // 跨段
      check = check.parentNode
    }
    const paraId = Number(paraEl.getAttribute('data-para-id') || 0)
    if (!paraId) return
    const para = flat.find(p => p.id === paraId)
    if (!para) return

    // 计算 offset_start/end：按 reader-para-body 内所有文本节点累计长度。
    // 注意：walker.nextNode 会跳过初始 currentNode（树根），所以从第一个文本节点开始。
    const walker = document.createTreeWalker(
      paraEl.querySelector('.reader-para-body') as Node,
      NodeFilter.SHOW_TEXT,
    )
    let offset_start = 0, offset_end = 0
    let sFound = false, eFound = false
    let cur: Node | null = walker.nextNode()
    while (cur) {
      const txtNode = cur as Text
      const len = txtNode.data.length
      const isStartContainer = (txtNode === range.startContainer)
      const isEndContainer   = (txtNode === range.endContainer)
      if (!sFound && isStartContainer) {
        offset_start += range.startOffset
        sFound = true
      }
      if (isEndContainer) {
        // 如已匹配 start：offset_end = 已经累计到 start 的绝对偏移 + (end 相对本节点)
        offset_end = (sFound ? offset_start - range.startOffset : 0) + range.endOffset
        eFound = true
        break
      }
      if (!sFound) offset_start += len
      cur = walker.nextNode()
    }
    if (!sFound || !eFound) return
    if (offset_end <= offset_start) return
    if (offset_end > para.text.length) return

    const rect = range.getBoundingClientRect()
    selRef.current = {
      book_para_id: paraId, offset_start, offset_end,
      selected: para.text.slice(offset_start, offset_end),
      rect,
    }
    const pageTop  = rect.top  + (window.scrollY || window.pageYOffset)
    const pageLeft = rect.left + (window.scrollX || window.pageXOffset)
    setSelPos({ top: pageTop - 44, left: Math.max(8, pageLeft) })
    setSelVisible(true)
    setSearchPanel(s => ({ ...s, attachHit: undefined }))
  }

  useEffect(() => {
    const onSel = () => setTimeout(handleSelection, 10)
    document.addEventListener('selectionchange', onSel)
    const onClick = (e: MouseEvent) => {
      // 点到浮动条/弹层/搜索面板内部 → 不关闭
      const tgt = e.target as HTMLElement | null
      if (tgt?.closest('.selection-bar')) return
      if (tgt?.closest('.note-popover')) return
      if (tgt?.closest('.search-panel')) return
      const sel = window.getSelection()
      if (!sel || sel.isCollapsed) {
        if (!noteOpen) clearSelection()
      }
    }
    document.addEventListener('mousedown', onClick)
    return () => {
      document.removeEventListener('selectionchange', onSel)
      document.removeEventListener('mousedown', onClick)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [noteOpen])

  // --------- 划线（annotations create） ----------
  const doHighlight = async (c: Color = color) => {
    const s = selRef.current
    if (!s || !book) return
    try {
      const a = await createAnnotation(book.id, {
        book_para_id: s.book_para_id,
        offset_start: s.offset_start,
        offset_end: s.offset_end,
        color: c,
      })
      setAnns(prev => [a, ...prev])
    } catch (e: any) {
      setSearchToast(`划线失败：${e.message || e}`)
      setTimeout(() => setSearchToast(''), 1600)
    } finally {
      clearSelection()
    }
  }

  // --------- 笔记 ----------
  const openNotePopover = () => {
    const s = selRef.current
    if (!s || !selPos) return
    setNoteText('')
    // 笔记弹层出现在浮动条下（和浮动条近似对齐）
    setNoteOpen({ top: selPos.top + 38, left: Math.min(window.innerWidth - 344, selPos.left) })
  }

  const submitNote = async () => {
    const s = selRef.current
    if (!s || !book) return
    setNoteBusy(true)
    try {
      const a = await createAnnotation(book.id, {
        book_para_id: s.book_para_id,
        offset_start: s.offset_start,
        offset_end: s.offset_end,
        color: color,
        note_text: noteText || null,
      })
      setAnns(prev => [a, ...prev])
      setSearchToast(noteText ? '笔记已保存' : '划线已保存')
      setTimeout(() => setSearchToast(''), 1400)
    } catch (e: any) {
      setSearchToast(`保存失败：${e.message || e}`)
      setTimeout(() => setSearchToast(''), 1600)
    } finally {
      setNoteBusy(false)
      setNoteOpen(null)
      clearSelection()
    }
  }

  // --------- 选中搜索（SQLite LIKE 占位） ----------
  const openSearch = async () => {
    const s = selRef.current
    if (!s) return
    const q = s.selected.trim()
    if (!q) return
    setSearchPanel({ open: true, q, hits: [], busy: true })
    clearSelection()
    try {
      const res = await searchBookParas(q, 10)
      setSearchPanel(p => ({ ...p, busy: false, hits: res.rows }))
    } catch (e: any) {
      setSearchPanel(p => ({ ...p, busy: false }))
      setSearchToast(`搜索失败：${e.message || e}`)
      setTimeout(() => setSearchToast(''), 1600)
    }
  }

  const attachSearchResultToNote = async (hit: SearchHit) => {
    const q = searchPanel.q
    const note = `【搜索关联】"${q}" → 《${hit.book_title}》·${hit.chapter_title} #${hit.para_seq}\n${hit.para_text}`
    setSearchPanel(p => ({ ...p, attachHit: hit }))
    // 如果当前没选文本，就把搜索结果加在读者第一段作为笔记（仅作为 M5 交互链路跑通）
    // 更优：打开「笔记弹层」预填 note = 搜索结果，保存到当前读者当前段落（段1，book 有内容）
    if (!book) return
    const firstPara = flat[0]
    if (!firstPara) return
    // 保存「笔记 + 搜索附文」到 firstPara 全文 0~10 字（任意稳定锚定；书冻结→永久有效）
    const os = 0, oe = Math.min(10, firstPara.text.length) || firstPara.text.length
    if (oe <= os) return
    try {
      const a = await createAnnotation(book.id, {
        book_para_id: firstPara.id, offset_start: os, offset_end: oe,
        color: 'blue', note_text: note,
      })
      setAnns(prev => [a, ...prev])
      setSearchToast('搜索结果已附加到笔记')
      setTimeout(() => setSearchToast(''), 1600)
    } catch (e: any) {
      setSearchToast(`附加失败：${e.message || e}`)
      setTimeout(() => setSearchToast(''), 1600)
    }
  }

  // --------- 渲染 ----------
  if (err) return (
    <div className="page err">
      <h1 className="title-xl">加载失败</h1>
      <p>{err}</p>
      <Link className="btn-outline" to="/books">← 回到书架</Link>
    </div>
  )
  if (!book) return <div className="page loading">正在打开书…</div>

  const hasAudio = !!book.audio_url

  return (
    <div className="page" ref={containerRef}>
      {/* 顶部返回 */}
      <div className="topbar">
        <Link className="btn-link" to="/books">← 书架</Link>
        <button className="btn-outline" onClick={() => navigate('/annotations')}>我的标注 →</button>
      </div>

      <div className="reader-layout">
        {/* 章节导航（侧栏） */}
        <aside className="reader-toc">
          <div className="toc-title">目录</div>
          {book.chapters.map(ch => (
            <a key={ch.id}
               className={'toc-item' + (ch.seq === activeChapterSeq ? ' active' : '')}
               href={`#ch-${ch.id}`}
               onClick={(e) => {
                 e.preventDefault()
                 document.getElementById(`ch-${ch.id}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
               }}>
              <div style={{ color: ch.seq === activeChapterSeq ? 'inherit' : 'var(--color-text-secondary)',
                            fontSize: 11, letterSpacing: '0.1em' }}>
                第 {ch.seq} 章
              </div>
              {ch.title}
            </a>
          ))}
        </aside>

        <section className="reader-main">
          <div className="reader-hero">
            {book.cover_url
              ? <img className="cover" src={book.cover_url} alt="" />
              : <div className="cover placeholder" />}
            <div>
              <p className="eyebrow">v {book.version} · 阅读器</p>
              <h1>{book.title}</h1>
              <div className="stat-row">
                <span>{book.chapter_count} 章</span>
                <span>{book.para_count} 段</span>
                {hasAudio && <span>音频 · {fmtTs(duration || 0)}</span>}
                <span>{anns.length} 条标注</span>
              </div>
            </div>
          </div>

          {book.chapters.map(ch => (
            <section key={ch.id} id={`ch-${ch.id}`} className="reader-chapter">
              <h2>{ch.title}</h2>
              {ch.paras.map(p => {
                const panns = annsByPara.get(p.id) || []
                const hasNote = panns.some(a => !!a.note_text)
                return (
                  <article
                    key={p.id}
                    ref={(el) => {
                      if (el) paraRefs.current.set(p.id, el)
                      else paraRefs.current.delete(p.id)
                    }}
                    data-para-id={p.id}
                    className={
                      'reader-para'
                      + (activeId === p.id ? ' active' : '')
                      + (visitedSet.has(p.id) ? ' visited' : '')
                      + (hasNote ? ' has-note' : '')
                    }
                    onClick={(e) => {
                      // 选中文字不触发跳转
                      if (window.getSelection()?.toString()) return
                      // 忽略点击在划线 mark 内部（不想因为 mark inline 阻止 selection）
                      const tgt = e.target as HTMLElement
                      if (tgt.closest('.hl')) return
                      jumpPara(p)
                    }}
                  >
                    <p className="reader-para-meta">段 {p.seq} · {fmtTs(p.start_ts)}</p>
                    <div className="reader-para-body">
                      {splitParaHighlights(p.text, panns).map((s, i) => s.mark
                        ? <mark key={i} className={`hl hl-${s.mark.color}`} title={s.mark.note_text || undefined}>
                            {s.text}
                          </mark>
                        : <React.Fragment key={i}>{s.text}</React.Fragment>)}
                    </div>
                  </article>
                )
              })}
            </section>
          ))}

          <div className="reader-spacer" />
        </section>
      </div>

      {/* 选中文本浮动条 */}
      {selVisible && selPos && (
        <div className="selection-bar" style={{ top: selPos.top, left: selPos.left }}>
          <div className="colors">
            {COLORS.map(c => (
              <span key={c} className={`dot ${c} ${color === c ? 'on' : ''}`}
                    onClick={() => setColor(c)} title={c} />
            ))}
          </div>
          <div className="sep" />
          <button onClick={() => doHighlight()}>划线</button>
          <button onClick={openNotePopover}>笔记</button>
          <div className="sep" />
          <button onClick={openSearch}>搜索</button>
        </div>
      )}

      {/* 笔记弹层 */}
      {noteOpen && (
        <div className="note-popover" style={{ top: noteOpen.top, left: noteOpen.left }}>
          <h4>添加笔记（{color} 色划线并附记）</h4>
          <textarea autoFocus
                    value={noteText}
                    onChange={e => setNoteText(e.target.value)}
                    placeholder="写下你的思考、灵感、延伸…" />
          <div className="actions">
            <button className="btn-link" onClick={() => setNoteOpen(null)}>取消</button>
            <button className="btn-primary" disabled={noteBusy} onClick={submitNote}>
              {noteBusy ? '保存中' : '保存笔记'}
            </button>
          </div>
        </div>
      )}

      {/* 搜索结果面板 */}
      {searchPanel.open && (
        <aside className="search-panel">
          <header>
            <div>
              <h4>选中搜索（关键词 · SQLite LIKE）</h4>
              <div className="q">“{searchPanel.q}”{searchPanel.busy ? ' · 搜索中…' : ''}</div>
            </div>
            <button className="btn-link" onClick={() => setSearchPanel(p => ({ ...p, open: false }))}>✕</button>
          </header>
          <div className="search-hits">
            {searchPanel.busy && (
              <div className="empty"><p>搜索中…（M6 升级为语义搜索）</p></div>
            )}
            {!searchPanel.busy && searchPanel.hits.length === 0 && (
              <div className="empty"><p>没有命中。M6 语义搜索会更聪明。</p></div>
            )}
            {searchPanel.hits.map(h => {
              const idx = h.para_text.indexOf(searchPanel.q)
              const before = idx >= 0 ? h.para_text.slice(0, idx) : h.para_text
              const matched = idx >= 0 ? h.para_text.slice(idx, idx + searchPanel.q.length) : ''
              const after  = idx >= 0 ? h.para_text.slice(idx + searchPanel.q.length) : ''
              return (
                <div key={h.para_id} className="search-hit">
                  <div className="hit-head">
                    <span>{h.book_title}</span>
                    <span className="muted">#{h.chapter_seq} {h.chapter_title}</span>
                  </div>
                  <div className="hit-text">{before}<b>{matched}</b>{after}</div>
                  <footer>
                    <Link className="btn-link"
                          to={`/books/${h.book_id}?para=${h.para_id}`}
                          onClick={() => setSearchPanel(p => ({ ...p, open: false }))}>
                      {book && h.book_id === book.id ? '定位本段 →' : '打开原文 →'}
                    </Link>
                    <button className="btn-outline"
                            onClick={() => attachSearchResultToNote(h)}
                            disabled={!!searchPanel.attachHit}>
                      {searchPanel.attachHit?.para_id === h.para_id ? '已附加' : '附加到笔记'}
                    </button>
                  </footer>
                </div>
              )
            })}
          </div>
          <footer className="foot">
            <span>共 {searchPanel.hits.length} 条（当前引擎 {searchPanel.busy ? '' : 'sqlite_like'}）</span>
            <span className="muted">M6 → embedding + FTS 混合</span>
          </footer>
        </aside>
      )}

      {/* 全局轻提示 */}
      {searchToast && (
        <div style={{
          position: 'fixed', left: '50%', bottom: 96, zIndex: 80,
          transform: 'translateX(-50%)',
          background: 'var(--color-dark)', color: '#fff',
          borderRadius: 999, padding: '8px 16px',
          fontSize: 13, boxShadow: '0 10px 24px rgba(0,0,0,0.22)',
        }}>
          {searchToast}
        </div>
      )}

      {/* 底部播放器（毛玻璃） */}
      {hasAudio
        ? (
          <>
            <audio ref={audioRef} src={book.audio_url!} preload="metadata" crossOrigin="anonymous" />
            <div className="player-bar">
              <div className="player-inner">
                <button className={'player-btn' + (playing ? ' pause' : '')}
                        onClick={togglePlay} title={playing ? '暂停' : '播放'}>
                  {playing ? '❚❚' : '▶'}
                </button>
                <div className="progress">
                  <input className="progress-bar"
                         type="range" min={0} max={duration || 0} step={0.1}
                         value={currentTime}
                         onChange={onSeek} />
                  <div className="progress-time">
                    {fmtTs(currentTime)} / {fmtTs(duration)}
                  </div>
                </div>
                <button className="speed-switch"
                        onClick={() => setSpeed(s => s === 1 ? 1.25 : s === 1.25 ? 1.5 : s === 1.5 ? 2 : 1)}>
                  {speed}×
                </button>
              </div>
            </div>
          </>
        )
        : (
          <div className="player-bar no-audio">
            该单集暂无可用音频链接，M5 阅读体验（划线/笔记/搜索）仍可用。
          </div>
        )
      }
    </div>
  )
}
