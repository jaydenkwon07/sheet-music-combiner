import { useRef, useState } from "react";

const ACCEPTED_RE = /\.(png|jpe?g|pdf)$/i;

export function DropZone({ onFiles }: { onFiles: (files: File[]) => void }) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [hover, setHover] = useState(false);
  const take = (list: FileList | null) => {
    if (!list) return;
    onFiles(Array.from(list).filter((f) => ACCEPTED_RE.test(f.name)));
  };
  return (
    <div
      className={`dropzone${hover ? " hover" : ""}`}
      onDragOver={(e) => { e.preventDefault(); setHover(true); }}
      onDragLeave={() => setHover(false)}
      onDrop={(e) => { e.preventDefault(); setHover(false); take(e.dataTransfer.files); }}
      onClick={() => inputRef.current?.click()}
    >
      <p>Drop numbered snippets (PNG, JPG, or PDF) here, or click to choose</p>
      <input
        ref={inputRef}
        type="file"
        accept="image/png,image/jpeg,application/pdf,.png,.jpg,.jpeg,.pdf"
        multiple
        hidden
        onChange={(e) => take(e.target.files)}
      />
    </div>
  );
}
