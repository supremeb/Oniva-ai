export default function ChatLoading() {
    return (
        <div className="h-full flex animate-pulse">
            {/* Sidebar skeleton */}
            <div className="w-[220px] border-r border-border bg-background p-4 space-y-4">
                <div className="h-9 bg-muted rounded-lg" />
                <div className="space-y-2">
                    <div className="h-4 w-16 bg-muted rounded" />
                    {[...Array(3)].map((_, i) => (
                        <div key={i} className="h-10 bg-muted rounded-lg" />
                    ))}
                </div>
                <div className="space-y-2">
                    <div className="h-4 w-20 bg-muted rounded" />
                    {[...Array(2)].map((_, i) => (
                        <div key={i} className="h-10 bg-muted rounded-lg" />
                    ))}
                </div>
            </div>

            {/* Main content skeleton */}
            <div className="flex-1 flex flex-col items-center justify-center p-8">
                <div className="w-full max-w-2xl space-y-4">
                    <div className="h-8 w-48 bg-muted rounded mx-auto" />
                    <div className="h-4 w-64 bg-muted rounded mx-auto" />
                    <div className="h-12 bg-muted rounded-lg mt-8" />
                </div>
            </div>
        </div>
    );
}
