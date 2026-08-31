/** 编辑页：段落编辑 + AI 广告建议（一键删除/保留）+「加入书架」。ins 风设计规范。 */
import { useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { createBook, fmtTs, getTranscript, type Edit, type Paragraph } from '../lib/api'

/** 默认 demo eid：M2 真实验收的声动早咖啡。 */
const DEFAULT_EID = '6a7b23ba17676351c570589d'

export default function Edit() {
  const params = useParams()
  const eid = params.eid || DEFAULT_EID
  const navigate = useNavigate()

  const [data, setData] = useState(() => {
    // 客户端 side loader，避免 SSR 假设
    getTranscript(eid)
      .then(d => {
        setData(d as any)
        setParas(d.paragraphs.map(p => ({ ...p })))
      })
      .catch(err => setLoadErr(String(err)))
    return null as null | Awaited<ReturnType<typeof getTranscript>>
  })
  const [loadErr, setLoadErr] = useState<string | null>(null)
  const [paras, setParas] = useState<Paragraph[]>([])
  const [deleted, setDeleted] = useState<Set<number>>(new Set())
  const [edited, setEdited] = useState<Set<number>>(new Set())
  const [editingSeq, setEditingSeq] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [toast, setToast] = useState<string | null>(null)

  function updateParaText(seq: number, text: string) {
    setParas(ps => ps.map(p => (p.seq === seq ? { ...p, text } : p)))
  }

  function markEdited(seq: number) {
    setEdited(s => new Set(s).add(seq))
  }

  function toggleDelete(seq: number) {
    setDeleted(s => {
      const next = new Set(s)
      if (next.has(seq)) next.delete(seq); else next.add(seq)
      return next
    })
  }

  const keptCount = useMemo(() => paras.length - deleted.size, [paras, deleted])
  const adCount = useMemo(() => paras.filter(p => p.is_ad).length, [paras])

  function buildEdits(): Edit[] {
    const edits: Edit[] = []
    for (const p of paras) {
      if (deleted.has(p.seq)) {
        edits.push({ para_seq: p.seq, action: 'delete' })
      } else if (edited.has(p.seq)) {
        edits.push({ para_seq: p.seq, action: 'replace', new_text: p.text })
      }
    }
    return edits
  }

  async function onAddToShelf() {
    if (busy) return
    setBusy(true); setToast(null)
    try {
      const book = await createBook(eid, buildEdits())
      setToast(`加入书架成功 · 书 #${book.id} v${book.version}`)
      setTimeout(() => navigate('/books'), 900)
    } catch (e: any) {
      setToast(`失败：${e?.message ?? String(e)}`)
    } finally {
      setBusy(false)
    }
  }

  if (loadErr) {
    return <div className="page err">加载失败：{loadErr}</div>
  }
  if (!data || paras.length === 0) {
    return <div className="page loading">加载编辑稿中…（若首次加载慢请稍候，eid={eid}）</div>
  }

  return (
    <div className="page">
      <header className="hero">
        <div className="hero-meta">
          <p className="eyebrow">单集稿 · 编辑</p>
          <h1>{data.title}</h1>
          {data.summary && <p className="summary">{data.summary}</p>}
          <div className="stat-row">
            <span>段落 {paras.length}</span>
            <span>将保留 {keptCount}</span>
            <span>AI 广告 {adCount} 段</span>
            {data.process_status === 'done' || <span className="tag warn">加工未完成</span>}
          </div>
        </div>
        {data.cover_url && <img className="cover" src={data.cover_url} alt="" />}
      </header>

      {adCount > 0 && (
        <section className="panel thin">
          <div className="panel-title">
            <span>AI 广告建议 · <span style={{ color: 'var(--color-warning)' }}>陶土橘</span> 段落为疑似广告</span>
            <div className="row-actions">
              <button className="btn-outline" onClick={() => {
                const ads = paras.filter(p => p.is_ad).map(p => p.seq)
                setDeleted(s => { const n = new Set(s); ads.forEach(a => n.add(a)); return n })
              }}>一键删除广告</button>
              <button className="btn-outline" onClick={() => {
                setDeleted(s => {
                  const n = new Set(s)
                  paras.filter(p => p.is_ad).forEach(p => n.delete(p.seq))
                  return n
                })
              }}>全部保留</button>
            </div>
          </div>
        </section>
      )}

      <section className="panel paras-panel">
        <h3 className="panel-title">转写段落（点击段落编辑；按钮可单独删除）</h3>
        <ul className="paras">
          {paras.map(p => {
            const isDel = deleted.has(p.seq)
            const isAd = !!p.is_ad
            const isEdited = edited.has(p.seq)
            return (
              <li key={p.seq} className={[
                'para',
                isDel ? 'deleted' : '',
                isAd ? 'ad' : '',
                isEdited ? 'edited' : '',
                editingSeq === p.seq ? 'editing' : '',
              ].join(' ')}>
                <div className="para-head">
                  <span className="para-seq">段 {p.seq} · {fmtTs(p.start_ts)}</span>
                  <div className="para-actions">
                    {isAd && <span className="pill-ad">广告 · {p.ad_reason || 'AI标记'}</span>}
                    {isEdited && <span className="pill-edited">已编辑</span>}
                    <button
                      className="btn-link"
                      onClick={() => toggleDelete(p.seq)}
                    >{isDel ? '撤销删除' : '删除'}</button>
                  </div>
                </div>
                {editingSeq === p.seq ? (
                  <textarea
                    className="edit-area"
                    value={p.text}
                    autoFocus
                    onChange={e => updateParaText(p.seq, e.target.value)}
                    onBlur={() => { setEditingSeq(null); markEdited(p.seq) }}
                  />
                ) : (
                  <p className="para-text" onClick={() => setEditingSeq(p.seq)}>{p.text}</p>
                )}
              </li>
            )
          })}
        </ul>
      </section>

      <div className="shelf-sticky">
        <div className="shelf-inner">
          <div className="shelf-summary">
            <strong>加入书架</strong>
            <span>将保留 {keptCount} 段 · 编辑 {edited.size} 段 · 删除 {deleted.size} 段</span>
          </div>
          <button className="btn-primary" disabled={busy || keptCount === 0} onClick={onAddToShelf}>
            {busy ? '生成中…' : '加入书架'}
          </button>
        </div>
        {toast && <div className="toast">{toast}</div>}
      </div>
    </div>
  )
}
