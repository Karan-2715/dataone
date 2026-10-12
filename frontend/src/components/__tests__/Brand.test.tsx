import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Brand } from "../Brand";

describe("Brand", () => {
  it("constrains the full-width Veltris artwork in standard chrome", () => {
    render(<Brand />);
    for (const img of screen.getAllByAltText("Veltris")) {
      expect(img).toHaveClass("h-4", "w-[94px]", "object-contain");
    }
  });

  it("uses the smaller bounded artwork in compact chrome", () => {
    render(<Brand compact />);
    for (const img of screen.getAllByAltText("Veltris")) {
      expect(img).toHaveClass("h-[13px]", "w-[76px]", "object-contain");
    }
  });

  it("renders both theme variants so CSS (not client JS) can switch without a hydration mismatch", () => {
    render(<Brand />);
    const [navy, white] = screen.getAllByAltText("Veltris");
    expect(navy).toHaveAttribute("src", "/veltris-logo.svg");
    expect(navy).toHaveClass("brand-logo-navy");
    expect(white).toHaveAttribute("src", "/veltris-logo-white.svg");
    expect(white).toHaveClass("brand-logo-white");
  });

  it("forces a single white artwork when inverse is set, regardless of theme", () => {
    render(<Brand inverse />);
    expect(screen.getByAltText("Veltris")).toHaveAttribute("src", "/veltris-logo-white.svg");
  });
});
