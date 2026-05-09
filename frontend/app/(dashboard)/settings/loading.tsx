export default function SettingsLoading() {
    return (
        <div className="p-8 max-w-4xl mx-auto animate-pulse">
            {/* Header skeleton */}
            <div className="mb-8">
                <div className="h-8 w-28 bg-muted rounded" />
                <div className="h-4 w-64 bg-muted rounded mt-2" />
            </div>

            {/* Settings sections skeleton */}
            <div className="space-y-6">
                {[...Array(3)].map((_, i) => (
                    <div key={i} className="p-6 border border-border rounded-lg">
                        <div className="h-5 w-32 bg-muted rounded mb-4" />
                        <div className="h-4 w-full bg-muted rounded mb-2" />
                        <div className="h-4 w-3/4 bg-muted rounded" />
                    </div>
                ))}
            </div>
        </div>
    );
}
