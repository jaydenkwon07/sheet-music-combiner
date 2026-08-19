import { apiUrl } from "../api";

export function PagePreview({ pageUrls }: { pageUrls: string[] }) {
  return (
    <div className="preview">
      {pageUrls.map((u, i) => (
        <figure key={u}>
          <img src={apiUrl(u)} alt={`page ${i + 1}`} />
          <figcaption>Page {i + 1}</figcaption>
        </figure>
      ))}
    </div>
  );
}
