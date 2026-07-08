import { describe, expect, it } from "vitest";
import { DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES, translate, translations } from "./translations";

describe("translations", () => {
  it("defaults to English (key passthrough)", () => {
    expect(DEFAULT_LANGUAGE).toBe("en");
    expect(translate("en", "Virtual Keys")).toBe("Virtual Keys");
  });

  it("falls back to the key when a translation is missing", () => {
    expect(translate("zh-TW", "Some untranslated string")).toBe("Some untranslated string");
  });

  it("translates sidebar labels to Traditional Chinese", () => {
    expect(translate("zh-TW", "Virtual Keys")).toBe("虛擬金鑰");
    expect(translate("zh-TW", "AI GATEWAY")).toBe("AI 閘道");
    expect(translate("zh-TW", "Logout")).toBe("登出");
  });

  it("lists every supported language in the translations map", () => {
    SUPPORTED_LANGUAGES.forEach(({ code }) => {
      expect(translations).toHaveProperty(code);
    });
  });
});
