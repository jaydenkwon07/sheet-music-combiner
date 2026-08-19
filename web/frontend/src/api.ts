export type SessionInfo = {
  session_id: string;
  prefix: string;
  num_pieces: number;
  files: string[];
};

export type AssembleResult = {
  counts: number[];
  uniform_scale: number;
  warnings: string[];
  page_urls: string[];
  pdf_url: string;
};

const BASE = (import.meta.env?.VITE_API_BASE as string | undefined) ?? "";

export function apiUrl(path: string): string {
  return `${BASE}${path}`;
}

export async function createSession(files: File[]): Promise<SessionInfo> {
  const form = new FormData();
  for (const f of files) form.append("files", f, f.name);
  const res = await fetch(apiUrl("/api/session"), { method: "POST", body: form });
  if (!res.ok) throw { status: res.status, detail: (await res.json()).detail };
  return res.json();
}

export async function assemble(
  sid: string,
  body: { prefix: string; margin?: number; pages?: string },
): Promise<AssembleResult> {
  const res = await fetch(apiUrl(`/api/session/${sid}/assemble`), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw { status: res.status, detail: (await res.json()).detail };
  return res.json();
}
