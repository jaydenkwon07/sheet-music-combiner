import { render, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DropZone } from "./DropZone";

describe("DropZone file filter", () => {
  it("keeps png/jpg/jpeg/pdf and drops other files", () => {
    const onFiles = vi.fn();
    render(<DropZone onFiles={onFiles} />);
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, {
      target: {
        files: [
          new File([""], "Song_1.png"),
          new File([""], "Song_2.JPG"),
          new File([""], "Song_3.jpeg"),
          new File([""], "Song_4.pdf"),
          new File([""], "notes.txt"),
        ],
      },
    });
    expect(onFiles).toHaveBeenCalledTimes(1);
    const kept = onFiles.mock.calls[0][0].map((f: File) => f.name);
    expect(kept).toEqual(["Song_1.png", "Song_2.JPG", "Song_3.jpeg", "Song_4.pdf"]);
  });
});
