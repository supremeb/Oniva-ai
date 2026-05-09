export default function AgentsLoading() {
    return (
        <div className="p-8 max-w-6xl mx-auto animate-pulse">
            {/* Header skeleton */}
            <div className="flex items-center justify-between mb-8">
                <div>
                    <div className="h-8 w-32 bg-muted rounded" />
                    <div className="h-4 w-64 bg-muted rounded mt-2" />
                </div>
                <div className="h-10 w-32 bg-muted rounded-lg" />
            </div>

            {/* Search skeleton */}
            <div className="h-10 w-80 bg-muted rounded-lg mb-8" />

            {/* My Agents skeleton */}
            <div className="mb-10">
                <div className="h-4 w-24 bg-muted rounded mb-4" />
                <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                    {[...Array(3)].map((_, i) => (
                        <div key={i} className="h-32 bg-muted rounded-lg" />
                    ))}
                </div>
            </div>

            {/* Templates skeleton */}
            <div>
                <div className="h-4 w-24 bg-muted rounded mb-4" />
                <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                    {[...Array(4)].map((_, i) => (
                        <div key={i} className="h-32 bg-muted rounded-lg" />
                    ))}
                </div>
            </div>
        </div>
    );
}
