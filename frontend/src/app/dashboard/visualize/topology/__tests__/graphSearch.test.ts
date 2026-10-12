import { describe, expect, it } from "vitest";
import { searchGraphNodes } from "../graphSearch";

const nodes = [
  { id: "1", label: "customers", database: "CRM", group: "source", columns: [{ name: "email_address" }] },
  { id: "2", label: "customer_orders", database: "Warehouse", group: "target", columns: [{ name: "order_id" }] },
  { id: "3", label: "accounts", database: "CRM", group: "source", columns: [{ name: "customer_id" }] },
];

describe("searchGraphNodes", () => {
  it("ranks exact and prefix table matches before column matches", () => {
    expect(searchGraphNodes(nodes, "customers").map((match) => match.node.id)).toEqual(["1"]);
    expect(searchGraphNodes(nodes, "customer").map((match) => match.node.id)).toEqual(["2", "1", "3"]);
  });

  it("reports the matching column for context", () => {
    expect(searchGraphNodes(nodes, "email")[0]).toMatchObject({ node: { id: "1" }, matchedColumn: "email_address" });
  });

  it("searches database names and ignores blank queries", () => {
    expect(searchGraphNodes(nodes, "warehouse").map((match) => match.node.id)).toEqual(["2"]);
    expect(searchGraphNodes(nodes, "  ")).toEqual([]);
  });
});
