"use client";

import { useLanguage } from "@/contexts/LanguageContext";
import { SUPPORTED_LANGUAGES, Language } from "@/i18n/translations";
import { CheckOutlined, DownOutlined, GlobalOutlined } from "@ant-design/icons";
import type { MenuProps } from "antd";
import { Dropdown } from "antd";
import React from "react";
import { NAV_PRODUCT_LINK_CLASS } from "./navProductLinkClass";

const LanguageSwitcher: React.FC = () => {
  const { language, setLanguage, t } = useLanguage();

  const items: MenuProps["items"] = SUPPORTED_LANGUAGES.map(({ code, nativeName }) => ({
    key: code,
    label: (
      <span className="flex items-center justify-between gap-3">
        {nativeName}
        {language === code && <CheckOutlined className="text-[11px]" aria-hidden />}
      </span>
    ),
    onClick: () => setLanguage(code as Language),
  }));

  const current = SUPPORTED_LANGUAGES.find(({ code }) => code === language);

  return (
    <Dropdown trigger={["click"]} menu={{ items, selectedKeys: [language] }}>
      <button type="button" className={NAV_PRODUCT_LINK_CLASS} aria-label={t("Language")} data-testid="language-switcher">
        <GlobalOutlined className="text-[13px]" aria-hidden />
        {current?.nativeName ?? "English"}
        <DownOutlined className="pointer-events-none text-[10px]" aria-hidden />
      </button>
    </Dropdown>
  );
};

export default LanguageSwitcher;
