/** M0 占位首页；M5 起接路由，M7 实现首页「知识宇宙」。 */
function App() {
  return (
    <main
      style={{
        maxWidth: 720,
        margin: '0 auto',
        padding: '96px 24px',
        textAlign: 'center',
      }}
    >
      <h1 style={{ fontSize: 32, marginBottom: 12 }}>PodLore</h1>
      <p style={{ color: 'var(--color-text-secondary)', margin: 0 }}>
        把播客变成你的书 · 工程骨架已就绪（M0）
      </p>
    </main>
  )
}

export default App
