export function WarningsPanel({ warnings }: { warnings: string[] }) {
  if (warnings.length === 0) return null;
  return (
    <details className="warnings" open>
      <summary>{warnings.length} cleanup note(s)</summary>
      <ul>{warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>
    </details>
  );
}
