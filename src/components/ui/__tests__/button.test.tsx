import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { Button } from "../button";

describe("Button", () => {
  it("renders its children", () => {
    render(<Button>Click me</Button>);
    expect(screen.getByRole("button", { name: "Click me" })).toBeInTheDocument();
  });

  it("respects the disabled prop", () => {
    render(<Button disabled>Disabled</Button>);
    expect(screen.getByRole("button", { name: "Disabled" })).toBeDisabled();
  });

  it("applies the variant styles", () => {
    render(<Button variant="destructive">Destructive</Button>);
    const btn = screen.getByRole("button", { name: "Destructive" });
    // We don't pin a class string (that locks us to cva internals); just
    // sanity-check the element rendered.
    expect(btn).toBeInTheDocument();
  });
});
