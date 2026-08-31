/** M5 路由：/books（书架）/edit/:eid?（编辑页）/books/:id（阅读器）/annotations（标注列表）。 */
import { BrowserRouter, Link, NavLink, Route, Routes } from 'react-router-dom'
import Books from './pages/Books'
import Edit from './pages/Edit'
import Reader from './pages/Reader'
import Annotations from './pages/Annotations'

const NAV = [
  { to: '/books', label: '书架' },
  { to: '/annotations', label: '标注' },
  { to: '/edit', label: '编辑器' },
]

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
          <Route path="/books/:id" element={<Reader />} />
          <Route path="/edit" element={<Edit />} />
          <Route path="/edit/:eid" element={<Edit />} />
          <Route path="/annotations" element={<Annotations />} />
        </Routes>
      </main>
      <footer className="site-foot">PodLore · 把播客变成你的书 · M5</footer>
    </BrowserRouter>
  )
}
