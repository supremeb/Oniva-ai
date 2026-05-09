export default function SkillsLoading() {
    return (
        <div className="p-8 max-w-6xl mx-auto animate-pulse">
            {/* Header skeleton */}
            <div className="flex items-center justify-between mb-8">
                <div>
                    <div className="h-8 w-24 bg-muted rounded" />
                    <div className="h-4 w-64 bg-muted rounded mt-2" />
                </div>
            </div>

            {/* Skills grid skeleton */}
            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
                {[...Array(6)].map((_, i) => (
                    <div key={i} className="h-32 bg-muted rounded-lg" />
                ))}
            </div>
        </div>
    );
}
