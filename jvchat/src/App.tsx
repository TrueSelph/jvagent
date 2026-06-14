import { lazy, Suspense } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { AppLayout } from './components/AppLayout'
import { getToken, getRefreshToken, isTokenExpired, clearAuthSession } from './utils/storage'

const Login = lazy(async () => {
  const m = await import('./components/Login')
  return { default: m.Login }
})
const ChatRedirect = lazy(async () => {
  const m = await import('./components/ChatRedirect')
  return { default: m.ChatRedirect }
})
const ChatInterface = lazy(async () => {
  const m = await import('./components/ChatInterface')
  return { default: m.ChatInterface }
})
const DebugInteractions = lazy(async () => {
  const m = await import('./components/DebugInteractions')
  return { default: m.DebugInteractions }
})

const routeFallback = (
  <div
    className="h-full min-h-0 flex items-center justify-center bg-zinc-50 dark:bg-zinc-900"
    role="status"
    aria-label="Loading"
  >
    <div className="animate-spin rounded-full h-10 w-10 border-2 border-zinc-400 border-t-transparent" />
  </div>
)

function PrivateRoute({ children }: { children: React.ReactNode }) {
  const token = getToken()
  if (!token) {
    return <Navigate to="/login" replace />
  }
  // Expired or invalid JWT with no refresh token — cannot recover; clear and send to login
  if (isTokenExpired(token) && !getRefreshToken()) {
    clearAuthSession()
    return <Navigate to="/login" replace />
  }
  return <>{children}</>
}

function App() {
  return (
    <BrowserRouter>
      <Suspense fallback={routeFallback}>
        <Routes>
        <Route path="/login" element={<Login />} />
        <Route
          path="/chat"
          element={
            <PrivateRoute>
              <AppLayout>
                <ChatRedirect />
              </AppLayout>
            </PrivateRoute>
          }
        />
        <Route
          path="/agents"
          element={<Navigate to="/chat" replace />}
        />
        <Route
          path="/chat/:agentId"
          element={
            <PrivateRoute>
              <AppLayout>
                <ChatInterface />
              </AppLayout>
            </PrivateRoute>
          }
        />
        <Route
          path="/debug"
          element={
            <PrivateRoute>
              <AppLayout>
                <DebugInteractions />
              </AppLayout>
            </PrivateRoute>
          }
        />
        <Route path="/" element={<Navigate to="/chat" replace />} />
        <Route path="*" element={
          <div className="h-full min-h-0 overflow-y-auto flex items-center justify-center bg-zinc-50 dark:bg-zinc-900">
            <div className="text-center">
              <h1 className="text-2xl font-bold text-zinc-900 dark:text-zinc-100 mb-2">404 - Page Not Found</h1>
              <p className="text-zinc-600 dark:text-zinc-400 mb-4">The page you're looking for doesn't exist.</p>
              <a href="/chat" className="text-zinc-600 hover:text-zinc-800 dark:text-zinc-400 dark:hover:text-zinc-200 underline">Go to chat</a>
            </div>
          </div>
        } />
        </Routes>
      </Suspense>
    </BrowserRouter>
  )
}

export default App
