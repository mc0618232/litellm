/**
 * Lightweight i18n dictionaries for the dashboard.
 *
 * Keys are the English source strings; `t(key)` falls back to the key itself,
 * so untranslated strings render in English and existing tests (which assert
 * English labels) keep passing with the default language.
 */

export type Language = "en" | "zh-TW";

export const DEFAULT_LANGUAGE: Language = "en";

export const LANGUAGE_STORAGE_KEY = "litellm_ui_language";

export const SUPPORTED_LANGUAGES: { code: Language; nativeName: string }[] = [
  { code: "en", nativeName: "English" },
  { code: "zh-TW", nativeName: "繁體中文" },
];

const zhTW: Record<string, string> = {
  // Sidebar group labels
  "AI GATEWAY": "AI 閘道",
  OBSERVABILITY: "可觀測性",
  "ACCESS CONTROL": "存取控制",
  "DEVELOPER TOOLS": "開發者工具",
  SETTINGS: "設定",

  // Sidebar items
  "Virtual Keys": "虛擬金鑰",
  Playground: "測試平台",
  Chat: "聊天",
  "Models + Endpoints": "模型與端點",
  Agentic: "代理功能",
  Agents: "AI 代理",
  "Workflow Runs": "工作流程執行",
  Memory: "記憶",
  "MCP Servers": "MCP 伺服器",
  Skills: "技能",
  Guardrails: "防護欄",
  Policies: "策略",
  Tools: "工具",
  "Search Tools": "搜尋工具",
  "Vector Stores": "向量儲存庫",
  "Tool Policies": "工具策略",
  Usage: "用量",
  Logs: "日誌",
  "Guardrails Monitor": "防護欄監控",
  Teams: "團隊",
  Projects: "專案",
  "Internal Users": "內部使用者",
  Organizations: "組織",
  "Access Groups": "存取群組",
  Budgets: "預算",
  "API Reference": "API 參考文件",
  "AI Hub": "AI 中心",
  "Learning Resources": "學習資源",
  Experimental: "實驗性功能",
  Caching: "快取",
  Prompts: "提示詞",
  "API Playground": "API 測試平台",
  "Tag Management": "標籤管理",
  "Old Usage": "舊版用量",
  Settings: "設定",
  "Router Settings": "路由設定",
  "Logging & Alerts": "日誌與警示",
  "Admin Settings": "管理員設定",
  "Cost Tracking": "成本追蹤",
  "UI Theme": "介面主題",

  // Navbar
  Docs: "文件",
  "Expand sidebar": "展開側邊欄",
  "Collapse sidebar": "收合側邊欄",
  Language: "語言",

  // User dropdown
  Logout: "登出",
  "User ID": "使用者 ID",
  Role: "角色",
  Premium: "進階版",
  Standard: "標準版",
  "Upgrade to Premium for advanced features": "升級至進階版以使用進階功能",
  "Hide New Feature Indicators": "隱藏新功能標示",
  "Hide All Prompts": "隱藏所有提示",
  "Hide Usage Indicator": "隱藏用量指示器",
  "Hide Blog Posts": "隱藏部落格文章",
  "Hide Bouncing Icon": "隱藏跳動圖示",
  Account: "帳戶",
};

export const translations: Record<Language, Record<string, string>> = {
  en: {},
  "zh-TW": zhTW,
};

export function translate(language: Language, key: string): string {
  return translations[language]?.[key] ?? key;
}
