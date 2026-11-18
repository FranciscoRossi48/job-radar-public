import { describe, expect, it } from "vitest";
import { formatBytes, scoreTone } from "./format";

describe("scoreTone", () => {
  it("uses the shortlist bands", () => {
    expect(scoreTone(92)).toBe("high");
    expect(scoreTone(75)).toBe("mid");
    expect(scoreTone(52)).toBe("low");
    expect(scoreTone(null)).toBe("none");
  });
});

describe("formatBytes", () => {
  it("formats local file sizes", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(2048)).toBe("2.0 KB");
  });
});
