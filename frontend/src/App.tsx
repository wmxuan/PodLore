/** M4 路由：/books（书架）/edit/:eid?（编辑页）/books/:id（阅读器占位）。 */
import { BrowserRouter, Link, NavLink, Route, Routes } from 'react-router-dom'
import Books from './pages/Books'
import Edit from './pages/Edit'

const NAV = [
  { to: '/books', label: '书架' },
  { to: '/edit', label: '编辑器' },
]

function ReaderPlaceholder() {
  return (
    <div className="page">
      <h1 className="title-xl">阅读器 · M5 开发中</h1>
      <p className="muted">这里将是播放器同步 + 划线/笔记 + 选中搜索的阅读空间。</p>
      <Link className="btn-outline" to="/books">← 回到书架</Link>
    </div>
  )
}

function Nav() {
  return (
    <nav className="site-nav">
      <div className="nav-inner">
        <Link to="/books" className="brand">PodLore · 播客变书</Link>
        <div className="nav-links">
          {NAV.map(n => (
            <NavLink key={n.to} to={n.to} className={({ isActive }) => 'nav-link' + (isActive ? ' active' : '')}>
              {n.label}
            </NavLink>
          ))}
        </div>
      </div>
    </nav>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <Nav />
      <main className="layout">
        <Routes>
          <Route path="/" element={<Books />} />
          <Route path="/books" element={<Books />} />
          <Route path="/books/:id" element={<ReaderPlaceholder />} />
          <Route path="/edit" element={<Edit />} />
          <Route path="/edit/:eid" element={<Edit />} />
        </Routes>
      </main>
      <footer className="site-foot">PodLore · 把播客变成你的书 · M4</footer>
    </BrowserRouter>
  )
}
