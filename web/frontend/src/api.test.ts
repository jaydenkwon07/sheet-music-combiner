import { describe, expect, it, vi } from "vitest";
import { assemble, createSession } from "./api";

describe("api client", () => {
  it("posts files to /api/session", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue({ ok: true, json: async () => ({ session_id: "s1", prefix: "Song", num_pieces: 2, files: [] }) });
    vi.stubGlobal("fetch", fetchMock);
    const info = await createSession([new File([""], "Song_1.png")]);
    expect(info.session_id).toBe("s1");
    expect(fetchMock.mock.calls[0][0]).toContain("/api/session");
  });

  it("throws detail on non-ok assemble", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: false, status: 422, json: async () => ({ detail: { needs_split: true } }) }),
    );
    await expect(assemble("s1", { prefix: "Song" })).rejects.toMatchObject({
      status: 422,
      detail: { needs_split: true },
    });
  });
});
