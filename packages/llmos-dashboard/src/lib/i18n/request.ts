import { getRequestConfig } from "next-intl/server";

export default getRequestConfig(async () => {
  const locale = "en"; // Default; overridden by client-side store
  return {
    locale,
    messages: (await import(`../../../public/locales/${locale}/common.json`))
      .default,
  };
});
