interface WelcomeScreenProps {
  agentName: string
}

export function WelcomeScreen({ agentName }: WelcomeScreenProps) {
  return (
    <div className="flex-1 flex items-center justify-center">
      <div className="text-center max-w-2xl px-4">
        <h1 className="text-4xl font-bold text-gray-900 mb-4">
          Welcome to {agentName}
        </h1>
        <p className="text-lg text-gray-600 mb-8">
          Start a conversation by typing a message below. I'm here to help!
        </p>
        <div className="space-y-2 text-sm text-gray-500">
          <p>💡 Try asking questions or giving instructions</p>
          <p>🔄 You can switch between conversations using the sidebar</p>
          <p>✨ Responses stream in real-time</p>
        </div>
      </div>
    </div>
  )
}

