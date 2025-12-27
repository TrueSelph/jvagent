import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { Login } from './components/Login'
import { AgentSelector } from './components/AgentSelector'
import { ChatInterface } from './components/ChatInterface'
import { getToken } from './utils/storage'

function PrivateRoute({ children }: { children: React.ReactNode }) {
  const token = getToken()
  return token ? <>{children}</> : <Navigate to="/login" replace />
}

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route
          path="/agents"
          element={
            <PrivateRoute>
              <AgentSelector />
            </PrivateRoute>
          }
        />
        <Route
          path="/chat/:agentId"
          element={<ChatInterface />}
        />
        <Route path="/" element={<Navigate to="/agents" replace />} />
        <Route path="*" element={
          <div className="min-h-screen flex items-center justify-center">
            <div className="text-center">
              <h1 className="text-2xl font-bold text-gray-900 mb-2">404 - Page Not Found</h1>
              <p className="text-gray-600 mb-4">The page you're looking for doesn't exist.</p>
              <a href="/agents" className="text-indigo-600 hover:text-indigo-700">Go to Agents</a>
            </div>
          </div>
        } />
      </Routes>
    </BrowserRouter>
  )
}

export default App

