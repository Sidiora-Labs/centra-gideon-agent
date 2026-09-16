export function RowHitTarget({ label }: { label: string }) {
  return <button type="button" aria-label={label} data-row-target
    className="absolute inset-0 -z-10 cursor-pointer rounded-[inherit] outline-none" />
}
