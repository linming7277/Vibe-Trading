import zhCN from "../locales/zh-CN.json";
import i18n, { APP_LANGUAGE, applyDocumentDirection, isRtl } from "../index";

describe("中文语言配置", () => {
  it("仅注册简体中文", () => {
    expect(APP_LANGUAGE).toBe("zh-CN");
    expect(i18n.language).toBe("zh-CN");
    expect(i18n.options.supportedLngs).toEqual(["zh-CN", "cimode"]);
    expect(i18n.getResourceBundle("zh-CN", "translation")).toEqual(zhCN);
  });

  it("始终使用从左到右的中文文档", () => {
    applyDocumentDirection();
    expect(isRtl()).toBe(false);
    expect(document.documentElement.lang).toBe("zh-CN");
    expect(document.documentElement.dir).toBe("ltr");
  });

  it("未知语言也回退到中文", async () => {
    await i18n.changeLanguage("en");
    expect(i18n.language).toBe("zh-CN");
    expect(i18n.t("layout.settings")).toBe("设置");
  });
});
