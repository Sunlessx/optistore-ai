import { render, screen } from "@testing-library/react";
import { StatusBadge } from "../components/ui";
import { qs } from "../lib/api";
import { formatBytes, formatPct, formatUsd } from "../lib/format";
import { Markdown } from "../lib/markdown";

describe("formatting", () => {
  it("formats bytes with binary units", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(formatBytes(5 * 1024 ** 3)).toBe("5.0 GB");
    expect(formatBytes(null)).toBe("—");
  });
  it("keeps precision for tiny dollar amounts", () => {
    expect(formatUsd(0.000123)).toBe("$0.000123");
    expect(formatUsd(12.5)).toBe("$12.50");
    expect(formatPct(12.345)).toBe("12.3%");
  });
  it("builds query strings without empty values", () => {
    expect(qs({ a: 1, b: "", c: undefined, d: false })).toBe("?a=1&d=false");
    expect(qs({})).toBe("");
  });
});

describe("Markdown preview", () => {
  it("renders headings, tables and lists", () => {
    render(<Markdown source={"# Title\n\n| Field | Value |\n| --- | --- |\n| Budget | $50,000 |\n\n- item [S1]"} />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Title");
    expect(screen.getByRole("cell", { name: "$50,000" })).toBeInTheDocument();
    expect(screen.getByText("item [S1]")).toBeInTheDocument();
  });
  it("never injects HTML from document content", () => {
    const { container } = render(<Markdown source={'<img src=x onerror="alert(1)"> <script>alert(1)</script>'} />);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
    expect(container.textContent).toContain("<script>");
  });
});

describe("StatusBadge", () => {
  it("always shows a text label alongside colour", () => {
    render(<StatusBadge status="partially_executed" />);
    expect(screen.getByText("partially executed")).toBeInTheDocument();
  });
});
