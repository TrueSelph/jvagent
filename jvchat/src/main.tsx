import React from 'react'
import ReactDOM from 'react-dom/client'
import { ThemeProvider } from './context/ThemeContext'
import App from './App'
// assistant-ui ships a precompiled Tailwind v4 stylesheet; importing it through
// jvchat's Tailwind v3 PostCSS pass errors on its `@layer base`. Inject it as a
// raw string so it bypasses PostCSS and still themes the aui-* components.
import assistantUiCss from '@assistant-ui/styles/index.css?raw'
import './styles/assistant-ui-tokens.css'
import './styles/index.css'

const auiStyle = document.createElement('style')
auiStyle.setAttribute('data-assistant-ui-styles', '')
auiStyle.textContent = assistantUiCss
document.head.appendChild(auiStyle)

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ThemeProvider>
      <App />
    </ThemeProvider>
  </React.StrictMode>
)

