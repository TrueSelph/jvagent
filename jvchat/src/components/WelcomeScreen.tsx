interface WelcomeScreenProps {
  agentName: string;
  agentAvatar?: string;
  description?: string;
}

export function WelcomeScreen({
  agentName,
  agentAvatar,
  description,
}: WelcomeScreenProps) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center pb-8">
      <div className="mx-auto my-auto flex w-full max-w-5xl flex-col">
        <div className="flex flex-col items-center justify-center">
          {agentAvatar && (
            <div className="fade-in slide-in-from-bottom-1 mb-6 animate-in delay-150 duration-200">
              <img
                src={agentAvatar}
                alt={agentName}
                className="h-32 w-32 rounded-full object-cover shadow-lg"
              />
            </div>
          )}
          <h1 className="fade-in slide-in-from-bottom-1 animate-in font-semibold text-2xl duration-200 text-zinc-900 dark:text-zinc-50">
            Hello! I'm {agentName}
          </h1>
          {description && (
            <div className="fade-in slide-in-from-bottom-1 animate-in delay-75 duration-200">
              <p className="mt-2 max-w-lg text-center text-sm leading-relaxed text-zinc-500 dark:text-zinc-400 sm:text-[0.9375rem]">
                {description}
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
