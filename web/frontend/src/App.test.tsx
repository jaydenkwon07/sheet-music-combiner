import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { App } from "./App";
import * as api from "./api";

describe("App flow", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("uploads, shows piece count, assembles, and previews pages", async () => {
    vi.spyOn(api, "createSession").mockResolvedValue({
      session_id: "s1", prefix: "Song", num_pieces: 9, files: [],
    });
    vi.spyOn(api, "assemble").mockResolvedValue({
      counts: [5, 4], uniform_scale: 1.75, warnings: ["page 1: removed 1 stray group(s)"],
      page_urls: ["/api/session/s1/file/Song_page1.png", "/api/session/s1/file/Song_page2.png"],
      pdf_url: "/api/session/s1/file/Song.pdf",
    });

    render(<App />);
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [new File([""], "Song_1.png")] } });

    expect(await screen.findByText(/9 pieces/)).toBeTruthy();
    fireEvent.click(screen.getByText("Assemble"));

    expect(await screen.findByText(/2 page\(s\): 5, 4/)).toBeTruthy();
    expect(screen.getByText(/cleanup note/)).toBeTruthy();
    expect(screen.getByText("Download PDF")).toBeTruthy();
    await waitFor(() => expect(screen.getAllByRole("img")).toHaveLength(2));

    // Re-assembling stamps a fresh cache-busting version so the browser
    // refetches the regenerated previews (same URL, new content otherwise).
    expect(screen.getAllByRole("img")[0].getAttribute("src")).toContain("?v=1");
    fireEvent.click(screen.getByText("Assemble"));
    await waitFor(() =>
      expect(screen.getAllByRole("img")[0].getAttribute("src")).toContain("?v=2"),
    );
  });

  it("reveals the split prompt on the N=7 needs_split error", async () => {
    vi.spyOn(api, "createSession").mockResolvedValue({
      session_id: "s2", prefix: "Song", num_pieces: 7, files: [],
    });
    vi.spyOn(api, "assemble").mockRejectedValue({
      status: 422, detail: { needs_split: true, message: "N=7 cannot be balanced" },
    });

    render(<App />);
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [new File([""], "Song_1.png")] } });
    fireEvent.click(await screen.findByText("Assemble"));

    expect(await screen.findByText(/cannot be balanced/)).toBeTruthy();
    expect(screen.getByText(/required for this count/)).toBeTruthy();
  });
});
