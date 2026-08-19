type Props = {
  prefix: string;
  margin: number;
  pages: string;
  needsSplit: boolean;
  disabled: boolean;
  onPrefix: (v: string) => void;
  onMargin: (v: number) => void;
  onPages: (v: string) => void;
  onAssemble: () => void;
};

export function Controls(p: Props) {
  return (
    <div className="controls">
      <label>Song name<input value={p.prefix} onChange={(e) => p.onPrefix(e.target.value)} /></label>
      <label>Margin (px)<input type="number" value={p.margin} onChange={(e) => p.onMargin(Number(e.target.value))} /></label>
      <label>
        Page split {p.needsSplit ? "(required for this count)" : "(optional, e.g. 5,4)"}
        <input value={p.pages} placeholder="auto" onChange={(e) => p.onPages(e.target.value)} />
      </label>
      <button disabled={p.disabled} onClick={p.onAssemble}>Assemble</button>
    </div>
  );
}
