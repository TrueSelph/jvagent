import { useState } from 'react'
import { GraphViewer } from './GraphViewer'
import { AppGraphProvider } from '../context/AppGraphContext'

interface AppLayoutProps {
  children: React.ReactNode
}

export function AppLayout({ children }: AppLayoutProps) {
  const [graphModalOpen, setGraphModalOpen] = useState(false)

  return (
    <AppGraphProvider openGraph={() => setGraphModalOpen(true)}>
      <div className="flex h-screen flex-col overflow-hidden bg-zinc-100 dark:bg-zinc-950">
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
          {children}
        </div>

        {graphModalOpen && (
          <GraphViewer onClose={() => setGraphModalOpen(false)} isEmbedded={true} />
        )}
      </div>
    </AppGraphProvider>
  )
}
