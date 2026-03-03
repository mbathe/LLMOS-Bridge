import createNextIntlPlugin from "next-intl/plugin";

const withNextIntl = createNextIntlPlugin("./src/lib/i18n/request.ts");

/** @type {import('next').NextConfig} */
const nextConfig = {
  transpilePackages: ["antd", "@ant-design/icons", "@ant-design/charts", "react-markdown", "remark-gfm"],
  output: "standalone",
};

export default withNextIntl(nextConfig);
