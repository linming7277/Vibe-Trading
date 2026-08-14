import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import zhCN from "./locales/zh-CN.json";

export const APP_LANGUAGE = "zh-CN" as const;

export function isRtl(): boolean {
  return false;
}

export function applyDocumentDirection(): void {
  if (typeof document === "undefined") return;
  document.documentElement.setAttribute("dir", "ltr");
  document.documentElement.setAttribute("lang", APP_LANGUAGE);
}

i18n
  .use(initReactI18next)
  .init({
    resources: {
      [APP_LANGUAGE]: { translation: zhCN },
    },
    lng: APP_LANGUAGE,
    fallbackLng: APP_LANGUAGE,
    supportedLngs: [APP_LANGUAGE],
    initAsync: false,
    interpolation: { escapeValue: false },
  });

applyDocumentDirection();

export default i18n;
