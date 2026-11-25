import { EmptyState } from "@/components/StateViews";

interface PlaceholderPageProps {
  title: string;
  phase: number;
}

/** Visible-but-unimplemented page for Phase 3+ routes. No fake functionality. */
export function PlaceholderPage({ title, phase }: PlaceholderPageProps) {
  return (
    <div className="panel">
      <EmptyState
        icon="🚧"
        title={`${title} — coming in Phase ${phase}`}
        message="This area is part of a later development phase and is not implemented yet. Phase 2 covers the Dashboard, Portfolio, and Live SLB Data views."
      />
    </div>
  );
}
