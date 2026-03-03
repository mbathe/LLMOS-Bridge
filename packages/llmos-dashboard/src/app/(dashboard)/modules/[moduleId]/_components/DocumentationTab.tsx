"use client";

import React, { useMemo } from "react";
import { Tabs, Card, Spin, Typography, Space } from "antd";
import {
  ReadOutlined,
  ThunderboltOutlined,
  ApiOutlined,
  HistoryOutlined,
  FileTextOutlined,
} from "@ant-design/icons";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { UseModuleDetailReturn } from "@/hooks/useModuleDetail";
import { EmptyState } from "@/components/common/EmptyState";

const { Text } = Typography;

interface DocumentationTabProps {
  hook: UseModuleDetailReturn;
}

/* ── GitHub-inspired markdown styles, fully theme-aware ── */
const mdCss = `
/* Base */
.md-doc {
  color: var(--ant-color-text);
  font-size: 14px;
  line-height: 1.8;
  word-wrap: break-word;
}

/* Headings */
.md-doc h1, .md-doc h2, .md-doc h3, .md-doc h4 {
  color: var(--ant-color-text);
  font-weight: 600;
  margin-top: 1.8em;
  margin-bottom: 0.6em;
  line-height: 1.35;
}
.md-doc h1 {
  font-size: 1.75em;
  padding-bottom: 0.35em;
  border-bottom: 2px solid var(--ant-color-primary-border);
}
.md-doc h2 {
  font-size: 1.4em;
  padding-bottom: 0.3em;
  border-bottom: 1px solid var(--ant-color-border-secondary, var(--ant-color-border));
}
.md-doc h3 { font-size: 1.15em; }
.md-doc h4 { font-size: 1em; color: var(--ant-color-text-secondary); }

/* First heading — no top margin */
.md-doc > h1:first-child,
.md-doc > h2:first-child,
.md-doc > h3:first-child { margin-top: 0; }

/* Paragraphs & text */
.md-doc p { margin: 0.8em 0; color: var(--ant-color-text); }
.md-doc strong, .md-doc b { color: var(--ant-color-text); font-weight: 600; }
.md-doc em { color: var(--ant-color-text-secondary); }

/* Links */
.md-doc a {
  color: var(--ant-color-primary);
  text-decoration: none;
  transition: color 0.2s;
}
.md-doc a:hover {
  color: var(--ant-color-primary-hover, var(--ant-color-primary));
  text-decoration: underline;
}

/* Inline code */
.md-doc code {
  font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
  background: var(--ant-color-fill-tertiary);
  color: var(--ant-color-text);
  padding: 2px 7px;
  border-radius: 6px;
  font-size: 0.88em;
  border: 1px solid var(--ant-color-border);
}

/* Code blocks */
.md-doc pre {
  background: var(--ant-color-fill-quaternary);
  border: 1px solid var(--ant-color-border);
  border-radius: 10px;
  padding: 18px 20px;
  overflow-x: auto;
  margin: 1.2em 0;
  line-height: 1.6;
}
.md-doc pre code {
  background: none;
  border: none;
  padding: 0;
  font-size: 0.88em;
  color: var(--ant-color-text);
}

/* Tables */
.md-doc table {
  border-collapse: separate;
  border-spacing: 0;
  width: 100%;
  margin: 1.2em 0;
  border-radius: 8px;
  overflow: hidden;
  border: 1px solid var(--ant-color-border);
}
.md-doc th {
  background: var(--ant-color-fill-quaternary);
  font-weight: 600;
  color: var(--ant-color-text);
  padding: 10px 14px;
  text-align: left;
  font-size: 0.92em;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  border-bottom: 2px solid var(--ant-color-border);
}
.md-doc td {
  padding: 10px 14px;
  color: var(--ant-color-text);
  border-bottom: 1px solid var(--ant-color-border);
}
.md-doc tr:last-child td { border-bottom: none; }
.md-doc tr:hover td {
  background: var(--ant-color-fill-quaternary);
}

/* Lists */
.md-doc ul, .md-doc ol {
  padding-left: 1.8em;
  margin: 0.6em 0;
}
.md-doc li {
  color: var(--ant-color-text);
  margin: 0.35em 0;
}
.md-doc li::marker { color: var(--ant-color-text-tertiary, var(--ant-color-text-secondary)); }

/* Blockquote */
.md-doc blockquote {
  border-left: 4px solid var(--ant-color-primary);
  margin: 1.2em 0;
  padding: 12px 18px;
  background: var(--ant-color-fill-quaternary);
  border-radius: 0 8px 8px 0;
  color: var(--ant-color-text-secondary);
}
.md-doc blockquote p { margin: 0.3em 0; color: inherit; }

/* Horizontal rule */
.md-doc hr {
  border: none;
  height: 2px;
  background: linear-gradient(
    90deg,
    transparent,
    var(--ant-color-border) 20%,
    var(--ant-color-border) 80%,
    transparent
  );
  margin: 2em 0;
}

/* Images */
.md-doc img {
  max-width: 100%;
  border-radius: 8px;
  border: 1px solid var(--ant-color-border);
  margin: 0.8em 0;
}

/* Task lists (GFM) */
.md-doc input[type="checkbox"] {
  margin-right: 6px;
  accent-color: var(--ant-color-primary);
}
`;

function MarkdownContent({ content }: { content: string }) {
  return (
    <>
      <style dangerouslySetInnerHTML={{ __html: mdCss }} />
      <div className="md-doc">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
      </div>
    </>
  );
}

export function DocumentationTab({ hook }: DocumentationTabProps) {
  const { data: docs, isLoading } = hook.docs;

  const tabs = useMemo(() => {
    if (!docs) return [];

    const items: {
      key: string;
      label: React.ReactNode;
      children: React.ReactNode;
    }[] = [];

    if (docs.readme !== null) {
      items.push({
        key: "readme",
        label: (
          <span>
            <ReadOutlined /> README
          </span>
        ),
        children: <MarkdownContent content={docs.readme} />,
      });
    }

    if (docs.actions !== null) {
      items.push({
        key: "actions",
        label: (
          <span>
            <ThunderboltOutlined /> Actions
          </span>
        ),
        children: <MarkdownContent content={docs.actions} />,
      });
    }

    if (docs.integration !== null) {
      items.push({
        key: "integration",
        label: (
          <span>
            <ApiOutlined /> Integration
          </span>
        ),
        children: <MarkdownContent content={docs.integration} />,
      });
    }

    if (docs.changelog !== null) {
      items.push({
        key: "changelog",
        label: (
          <span>
            <HistoryOutlined /> Changelog
          </span>
        ),
        children: <MarkdownContent content={docs.changelog} />,
      });
    }

    return items;
  }, [docs]);

  if (isLoading) {
    return (
      <div style={{ textAlign: "center", padding: 48 }}>
        <Spin size="large" />
      </div>
    );
  }

  if (!docs || tabs.length === 0) {
    return <EmptyState description="No documentation available for this module." />;
  }

  return (
    <Card
      title={
        <Space>
          <FileTextOutlined />
          <span>Documentation</span>
          <Text type="secondary" style={{ fontSize: 13, fontWeight: 400 }}>
            {tabs.length} section{tabs.length > 1 ? "s" : ""}
          </Text>
        </Space>
      }
      styles={{ body: { padding: "8px 24px 24px" } }}
    >
      <Tabs type="line" items={tabs} />
    </Card>
  );
}
