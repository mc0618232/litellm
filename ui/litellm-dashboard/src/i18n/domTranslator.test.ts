// @vitest-environment jsdom
import { afterEach, describe, expect, it } from "vitest";
import { startDomTranslation } from "./domTranslator";

describe("startDomTranslation", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("translates exact-match text nodes and restores them on cleanup", () => {
    document.body.innerHTML = "<h2>Usage Metrics</h2><p>Total Requests</p>";
    const stop = startDomTranslation("zh-TW");

    expect(document.querySelector("h2")!.textContent).toBe("用量指標");
    expect(document.querySelector("p")!.textContent).toBe("總請求數");

    stop();
    expect(document.querySelector("h2")!.textContent).toBe("Usage Metrics");
    expect(document.querySelector("p")!.textContent).toBe("Total Requests");
  });

  it("leaves non-dictionary text (user data) untouched", () => {
    document.body.innerHTML = "<span>my-secret-key-alias</span>";
    const stop = startDomTranslation("zh-TW");
    expect(document.querySelector("span")!.textContent).toBe("my-secret-key-alias");
    stop();
  });

  it("translates placeholder attributes", () => {
    document.body.innerHTML = '<input placeholder="Select user to filter..." />';
    const stop = startDomTranslation("zh-TW");
    expect(document.querySelector("input")!.getAttribute("placeholder")).toBe("選擇要篩選的使用者...");
    stop();
    expect(document.querySelector("input")!.getAttribute("placeholder")).toBe("Select user to filter...");
  });

  it("translates content rendered after start via the mutation observer", async () => {
    const stop = startDomTranslation("zh-TW");
    const div = document.createElement("div");
    div.textContent = "Daily Spend";
    document.body.appendChild(div);

    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(div.textContent).toBe("每日花費");
    stop();
  });

  it("is a no-op for English", () => {
    document.body.innerHTML = "<h2>Usage Metrics</h2>";
    const stop = startDomTranslation("en");
    expect(document.querySelector("h2")!.textContent).toBe("Usage Metrics");
    stop();
  });
});
