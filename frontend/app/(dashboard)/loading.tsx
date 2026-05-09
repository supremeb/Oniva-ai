export default function DashboardLoading() {
    return (
        <div className="p-8 max-w-6xl mx-auto animate-pulse">
            {/* Header skeleton */}
            <div className="mb-8">
                <div className="h-8 w-64 bg-muted rounded" />
                <div className="h-4 w-96 bg-muted rounded mt-2" />
            </div>

            {/* Quick actions skeleton */}
            <div className="mb-8">
                <div className="h-4 w-24 bg-muted rounded mb-4" />
                <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                    {[...Array(4)].map((_, i) => (
                        <div key={i} className="h-20 bg-muted rounded-lg" />
                    ))}
                </div>
            </div>

            {/* Agents skeleton */}
            <div className="mb-8">
                <div className="h-4 w-32 bg-muted rounded mb-4" />
                <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
                    {[...Array(4)].map((_, i) => (
                        <div key={i} className="h-16 bg-muted rounded-lg" />
                    ))}
                </div>
            </div>
        </div>
    );
}
