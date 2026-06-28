import { useParams } from "react-router"

export default function ReleasePage() {
  const { id } = useParams<{ id: string }>()
  return (
    <div className="p-8">
      <h1 className="text-2xl font-semibold">Release #{id}</h1>
      <p className="text-muted-foreground mt-2">Coming in Phase 5</p>
    </div>
  )
}
