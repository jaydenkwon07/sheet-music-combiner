import { apiUrl } from "../api";

export function PagePreview({ pageUrls, version }: { pageUrls: string[]; version: number }) {
  return (
    <div className="preview">
      {pageUrls.map((u, i) => (
        <figure key={`${u}-${version}`}>
          <img src={`${apiUrl(u)}?v=${version}`} alt={`page ${i + 1}`} />
          <figcaption>Page {i + 1}</figcaption>
        </figure>
      ))}
    </div>
  );
}
