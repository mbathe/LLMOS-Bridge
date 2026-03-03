"use client";

import { Breadcrumb } from "antd";
import { usePathname } from "next/navigation";
import Link from "next/link";
import { HomeOutlined } from "@ant-design/icons";

const nameMap: Record<string, string> = {
  overview: "Overview",
  plans: "Plans",
  modules: "Modules",
  hub: "Module Hub",
  triggers: "Triggers",
  recordings: "Recordings",
  security: "Security",
  permissions: "Permissions",
  scanners: "Scanners",
  audit: "Audit Log",
  system: "System",
  config: "Configuration",
  monitoring: "Monitoring",
};

export function BreadcrumbNav() {
  const pathname = usePathname();
  const parts = pathname.split("/").filter(Boolean);

  const items = [
    {
      title: (
        <Link href="/overview">
          <HomeOutlined />
        </Link>
      ),
    },
    ...parts.map((part, index) => {
      const path = "/" + parts.slice(0, index + 1).join("/");
      const isLast = index === parts.length - 1;
      const label = nameMap[part] ?? part;
      return {
        title: isLast ? label : <Link href={path}>{label}</Link>,
      };
    }),
  ];

  return <Breadcrumb items={items} style={{ marginBottom: 16 }} />;
}
