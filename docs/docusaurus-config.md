---
id: docusaurus-config
title: Documentation Site Structure
sidebar_label: Site Structure
sidebar_position: 0
description: Docusaurus project structure, sidebar configuration, and deployment guide for LLMOS Bridge documentation.
---

# Documentation Site Structure

This document defines the complete Docusaurus project structure for the LLMOS Bridge public documentation. It provides the directory layout, sidebar configuration, navigation hierarchy, and deployment setup.

---

## Directory Layout

```
docs/
├── docusaurus-config.md              ← This file (site structure reference)
│
├── overview/
│   ├── architecture.md               ← Architecture overview (layers, components, data flow)
│   ├── security.md                   ← Security deep-dive (scanners, intent verifier, permissions)
│   ├── events.md                     ← Event system (EventBus, topics, routing)
│   ├── orchestration.md              ← Orchestration engine (DAG, state, approval, rollback)
│   ├── perception.md                 ← Perception system (capture, OCR, vision, scene graph)
│   ├── sdk.md                        ← SDK reference (agents, providers, toolkit, clients)
│   ├── hub-isolation.md              ← Hub & module isolation (packaging, JSON-RPC, venv)
│   ├── triggers-recording.md         ← Triggers & recording (watchers, scheduler, replay)
│   ├── exceptions-logging.md         ← Exceptions, logging, module helpers
│   └── configuration.md              ← Configuration reference (all 22 sections)
│
├── protocol/
│   └── iml-protocol.md               ← IML Protocol v2 reference
│
├── modules/
│   ├── fundamentals/
│   │   └── module-system.md          ← Module system fundamentals
│   │
│   └── reference/                    ← Per-module documentation (18 modules)
│       ├── filesystem.md
│       ├── os_exec.md
│       ├── api_http.md
│       ├── browser.md
│       ├── excel.md
│       ├── word.md
│       ├── powerpoint.md
│       ├── database.md
│       ├── db_gateway.md
│       ├── gui.md
│       ├── computer_control.md
│       ├── vision.md
│       ├── window_tracker.md
│       ├── iot.md
│       ├── recording.md
│       ├── triggers.md
│       ├── security.md
│       └── module_manager.md
│
├── annotators/
│   └── decorators.md                 ← Annotators reference
│
└── guide/                            ← Future: tutorials, quickstart, etc.
```

---

## Docusaurus Configuration

### docusaurus.config.js

```javascript
/** @type {import('@docusaurus/types').Config} */
const config = {
  title: 'LLMOS Bridge',
  tagline: 'Bridge Large Language Models to the Operating System',
  favicon: 'img/favicon.ico',
  url: 'https://llmos-bridge.dev',
  baseUrl: '/',
  organizationName: 'llmos-bridge',
  projectName: 'llmos-bridge',
  onBrokenLinks: 'throw',
  onBrokenMarkdownLinks: 'warn',

  i18n: {
    defaultLocale: 'en',
    locales: ['en'],
  },

  presets: [
    [
      'classic',
      /** @type {import('@docusaurus/preset-classic').Options} */
      ({
        docs: {
          sidebarPath: './sidebars.js',
          editUrl: 'https://github.com/llmos-bridge/llmos-bridge/tree/main/docs/',
        },
        theme: {
          customCss: './src/css/custom.css',
        },
      }),
    ],
  ],

  themeConfig:
    /** @type {import('@docusaurus/preset-classic').ThemeConfig} */
    ({
      navbar: {
        title: 'LLMOS Bridge',
        items: [
          {
            type: 'docSidebar',
            sidebarId: 'docsSidebar',
            position: 'left',
            label: 'Documentation',
          },
          {
            href: 'https://github.com/llmos-bridge/llmos-bridge',
            label: 'GitHub',
            position: 'right',
          },
        ],
      },
      footer: {
        style: 'dark',
        links: [
          {
            title: 'Documentation',
            items: [
              { label: 'Architecture', to: '/docs/overview/architecture' },
              { label: 'Security', to: '/docs/overview/security' },
              { label: 'SDK', to: '/docs/overview/sdk' },
              { label: 'IML Protocol', to: '/docs/protocol/iml-protocol' },
              { label: 'Modules', to: '/docs/modules/fundamentals/module-system' },
              { label: 'Configuration', to: '/docs/overview/configuration' },
            ],
          },
          {
            title: 'Community',
            items: [
              { label: 'GitHub', href: 'https://github.com/llmos-bridge/llmos-bridge' },
              { label: 'Issues', href: 'https://github.com/llmos-bridge/llmos-bridge/issues' },
            ],
          },
        ],
        copyright: `Copyright LLMOS Bridge contributors.`,
      },
      prism: {
        theme: require('prism-react-renderer').themes.github,
        darkTheme: require('prism-react-renderer').themes.dracula,
        additionalLanguages: ['python', 'json', 'bash', 'yaml'],
      },
    }),
};

module.exports = config;
```

### sidebars.js

```javascript
/** @type {import('@docusaurus/plugin-content-docs').SidebarsConfig} */
const sidebars = {
  docsSidebar: [
    {
      type: 'category',
      label: 'Overview',
      collapsed: false,
      items: [
        'overview/architecture',
        'overview/security',
        'overview/events',
        'overview/orchestration',
        'overview/perception',
        'overview/sdk',
        'overview/hub-isolation',
        'overview/triggers-recording',
        'overview/exceptions-logging',
        'overview/configuration',
      ],
    },
    {
      type: 'category',
      label: 'Protocol',
      items: [
        'protocol/iml-protocol',
      ],
    },
    {
      type: 'category',
      label: 'Module System',
      items: [
        'modules/fundamentals/module-system',
      ],
    },
    {
      type: 'category',
      label: 'Annotators',
      items: [
        'annotators/decorators',
      ],
    },
    {
      type: 'category',
      label: 'Module Reference',
      collapsed: false,
      items: [
        {
          type: 'category',
          label: 'System',
          items: [
            'modules/reference/filesystem',
            'modules/reference/os_exec',
            'modules/reference/module_manager',
            'modules/reference/security',
            'modules/reference/recording',
            'modules/reference/triggers',
          ],
        },
        {
          type: 'category',
          label: 'Network',
          items: [
            'modules/reference/api_http',
          ],
        },
        {
          type: 'category',
          label: 'Automation',
          items: [
            'modules/reference/browser',
            'modules/reference/gui',
            'modules/reference/computer_control',
            'modules/reference/window_tracker',
          ],
        },
        {
          type: 'category',
          label: 'Database',
          items: [
            'modules/reference/database',
            'modules/reference/db_gateway',
          ],
        },
        {
          type: 'category',
          label: 'Document',
          items: [
            'modules/reference/excel',
            'modules/reference/word',
            'modules/reference/powerpoint',
          ],
        },
        {
          type: 'category',
          label: 'Perception',
          items: [
            'modules/reference/vision',
          ],
        },
        {
          type: 'category',
          label: 'Hardware',
          items: [
            'modules/reference/iot',
          ],
        },
      ],
    },
  ],
};

module.exports = sidebars;
```

---

## Navigation Hierarchy

The documentation follows a top-down reading order:

```
1. Overview (10 documents)
   ├── Architecture ── System context, layer architecture, component map, data flow
   ├── Security ── Scanner pipeline, intent verifier, permissions, profiles, audit
   ├── Events ── EventBus, topics, routing, UniversalEvent, session propagator
   ├── Orchestration ── DAG, state machine, approval, rollback, streaming, nodes
   ├── Perception ── Screen capture, OCR, OmniParser, Ultra, scene graph, cache
   ├── SDK ── ComputerUseAgent, ReactivePlanLoop, providers, toolkit, clients
   ├── Hub & Isolation ── Packaging, hub client, JSON-RPC, venv, health monitor
   ├── Triggers & Recording ── Watchers, scheduler, conflict, recorder, replayer
   ├── Exceptions & Logging ── 25+ exceptions, structlog, module helpers
   └── Configuration ── All 22 config sections, environment variables

2. IML Protocol Reference
   └── Plan structure, actions, templates, error handling, lifecycle states

3. Module System Fundamentals
   └── BaseModule, manifest, lifecycle, security integration, streaming

4. Annotators Reference
   └── Security decorators, streaming decorator, configuration annotations

5. Module Reference (18 modules)
   ├── System: filesystem, os_exec, module_manager, security, recording, triggers
   ├── Network: api_http
   ├── Automation: browser, gui, computer_control, window_tracker
   ├── Database: database, db_gateway
   ├── Document: excel, word, powerpoint
   ├── Perception: vision
   └── Hardware: iot
```

Each document is self-sufficient. A reader can open any module reference page and understand it without reading the others. However, reading in order provides the deepest understanding.

---

## Document Cross-References

| Source | References |
|--------|------------|
| Architecture | Protocol, Security, Modules, Events, Perception, API |
| Security | Scanner pipeline, Intent verifier, Permissions, Profiles, Audit |
| Events | EventBus hierarchy, Topics, Session propagator |
| Orchestration | DAG, State machine, Approval system, Rollback, Streaming |
| Perception | Screen capture, OmniParser, Ultra, Scene graph, Cache, Prefetcher |
| SDK | Providers, Agents, Reactive loop, Toolkit, Clients, Safeguards |
| Hub & Isolation | Packaging, JSON-RPC, Venv, Health monitor, Module signing |
| Triggers & Recording | Watchers, Scheduler, Conflict resolver, Recorder, Replayer |
| Exceptions & Logging | Exception hierarchy, Structlog, Module helpers |
| Configuration | All 22 config sections, Settings API |
| Protocol | Actions, Templates, Error handling |
| Module System | BaseModule, Manifest, Security decorators |
| Annotators | Security decorators, Streaming, Configuration |
| Module pages | Specific actions, params, security annotations |

---

## Content Standards

### Formatting

- **No emojis** in any document
- Code blocks use triple backticks with language identifiers
- Tables use GitHub-flavored Markdown
- ASCII diagrams for architecture flows
- Frontmatter includes `id`, `title`, `sidebar_label`, `sidebar_position`, `description`

### Structure per Module Page

Each module reference page follows this template:

```
# Module Name

One-line description.

| Property | Value |
| Module ID | ... |
| Version | ... |
| Type | ... |
| Platforms | ... |
| Dependencies | ... |
| Declared Permissions | ... |

## Actions

### action_name

Description.

| Parameter | Type | Required | Default | Description |

Returns / Security annotations.

## Implementation Notes
```

### Terminology

| Term | Definition |
|------|------------|
| **Plan** | IML v2 document submitted to the daemon |
| **Action** | Single operation within a plan |
| **Module** | Python class exposing typed actions |
| **Manifest** | Machine-readable capability declaration |
| **Profile** | Security permission template |
| **Annotator** | Decorator applied to action methods |
| **EventBus** | Topic-routed event backbone |
| **Stream** | Real-time progress channel from action to agent |

---

## Deployment

### Local Development

```bash
cd docs-site
npm install
npm run start
```

### Build

```bash
npm run build
```

### Deploy to GitHub Pages

```bash
GIT_USER=<username> npm run deploy
```

### Docker

```dockerfile
FROM node:20-alpine AS builder
WORKDIR /app
COPY docs-site/ .
RUN npm ci && npm run build

FROM nginx:alpine
COPY --from=builder /app/build /usr/share/nginx/html
```

---

## Future Additions

The `guide/` directory is reserved for:

- **Quickstart** — Get running in 5 minutes
- **SDK Tutorial** — Build an agent with langchain-llmos
- **Module Development** — Create a custom module
- **Security Hardening** — Production security configuration
- **Deployment** — Production deployment with systemd/Docker
- **Troubleshooting** — Common issues and solutions

These will be added as separate documents following the same formatting standards.
