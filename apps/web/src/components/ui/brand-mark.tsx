/** The Talos L and amber dot on the approved dark app tile. */
export function BrandMark({
  className = "",
  decorative = false,
}: {
  className?: string;
  decorative?: boolean;
}) {
  return (
    <img
      alt={decorative ? "" : "Talos"}
      aria-hidden={decorative || undefined}
      className={`talos-brand-mark ${className}`}
      draggable={false}
      src="/favicon.svg"
    />
  );
}
