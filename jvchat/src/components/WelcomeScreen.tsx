interface WelcomeScreenProps {
  agentName: string
}

export function WelcomeScreen({ agentName }: WelcomeScreenProps) {
  return (
    <div className="flex-1 flex items-center justify-center px-4">
      <div className="text-center max-w-2xl w-full">
        <h1 className="text-2xl sm:text-3xl md:text-4xl font-bold text-gray-900 mb-3 sm:mb-4">
          Welcome to {agentName}
        </h1>
        <p className="text-base sm:text-lg text-gray-600 mb-6 sm:mb-8">
          Start a conversation by typing a message below. I'm here to help!
        </p>
        <div className="space-y-2 text-xs sm:text-sm text-gray-500">
          <p>💡 Try asking questions or giving instructions</p>
          <p className="hidden sm:block">🔄 You can switch between conversations using the sidebar</p>
          <p className="sm:hidden">🔄 Tap the menu button to switch conversations</p>
          <p>✨ Responses stream in real-time</p>
        </div>
      </div>
    </div>
  )
}

