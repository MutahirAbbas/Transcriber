import { Route, Routes } from 'react-router-dom'

import { Sidebar } from './components/Sidebar'
import { CallDetailPage } from './pages/CallDetailPage'
import { CallsPage } from './pages/CallsPage'

export function App() {
  return (
    <div className="min-h-screen flex flex-col md:flex-row bg-ground">
      <Sidebar />
      <main className="flex-1 min-w-0 px-4 py-6 md:px-10 md:pt-8 md:pb-12">
        <Routes>
          <Route path="/" element={<CallsPage />} />
          <Route path="/calls/:id" element={<CallDetailPage />} />
          <Route path="*" element={<CallsPage />} />
        </Routes>
      </main>
    </div>
  )
}
