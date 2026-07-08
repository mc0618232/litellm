"use client";

import { getLocalStorageItem, setLocalStorageItem } from "@/utils/localStorageUtils";
import React, { createContext, useCallback, useContext, useEffect, useState } from "react";
import { DEFAULT_LANGUAGE, LANGUAGE_STORAGE_KEY, Language, translate } from "@/i18n/translations";

interface LanguageContextValue {
  language: Language;
  setLanguage: (language: Language) => void;
  t: (key: string) => string;
}

// Default value keeps components (and existing tests) working without a
// provider: everything renders in English via the key-passthrough fallback.
const LanguageContext = createContext<LanguageContextValue>({
  language: DEFAULT_LANGUAGE,
  setLanguage: () => {},
  t: (key: string) => key,
});

function isSupportedLanguage(value: string | null): value is Language {
  return value === "en" || value === "zh-TW";
}

export function LanguageProvider({ children }: { children: React.ReactNode }) {
  const [language, setLanguageState] = useState<Language>(DEFAULT_LANGUAGE);

  // Restore persisted choice after mount (static export prerenders on the
  // server where localStorage is unavailable).
  useEffect(() => {
    const stored = getLocalStorageItem(LANGUAGE_STORAGE_KEY);
    if (isSupportedLanguage(stored)) {
      setLanguageState(stored);
    }
  }, []);

  useEffect(() => {
    document.documentElement.lang = language === "zh-TW" ? "zh-Hant-TW" : "en";
  }, [language]);

  const setLanguage = useCallback((next: Language) => {
    setLanguageState(next);
    setLocalStorageItem(LANGUAGE_STORAGE_KEY, next);
  }, []);

  const t = useCallback((key: string) => translate(language, key), [language]);

  return <LanguageContext.Provider value={{ language, setLanguage, t }}>{children}</LanguageContext.Provider>;
}

export function useLanguage(): LanguageContextValue {
  return useContext(LanguageContext);
}
