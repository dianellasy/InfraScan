import { beforeEach, describe, expect, it, vi } from "vitest";

const parse = vi.fn();
vi.mock("exifr", () => ({ default: { parse: (...args: unknown[]) => parse(...args) } }));

const { readPhotoMetadata, toDateTimeLocal } = await import("./photoMetadata.ts");

describe("photo metadata", () => {
  beforeEach(() => parse.mockReset());

  it("reads capture time and GPS from EXIF", async () => {
    parse.mockResolvedValue({
      DateTimeOriginal: new Date(2026, 10, 20, 14, 5, 59),
      latitude: 28.6139,
      longitude: 77.209,
    });
    expect(await readPhotoMetadata(new Blob(["jpeg"]))).toEqual({
      capturedAt: "2026-11-20T14:05",
      latitude: 28.6139,
      longitude: 77.209,
    });
  });

  it("returns nothing for photos without EXIF or unreadable files", async () => {
    parse.mockResolvedValueOnce(undefined);
    expect(await readPhotoMetadata(new Blob(["x"]))).toEqual({});
    parse.mockRejectedValueOnce(new Error("not a JPEG"));
    expect(await readPhotoMetadata(new Blob(["x"]))).toEqual({});
  });

  it("formats datetime-local values in local time", () => {
    expect(toDateTimeLocal(new Date(2026, 0, 2, 3, 4))).toBe("2026-01-02T03:04");
  });
});
