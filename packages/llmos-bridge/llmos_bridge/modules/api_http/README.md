# API HTTP Module

HTTP requests, file transfer, email, GraphQL, OAuth2, and webhook automation.

## Overview

The API HTTP module provides comprehensive network communication capabilities for
IML plans. It covers the full HTTP method suite (GET, HEAD, POST, PUT, PATCH, DELETE)
via `httpx.AsyncClient`, streaming file download and multipart upload, GraphQL queries
and mutations, OAuth2 token acquisition for all standard grant types, HTML parsing
with BeautifulSoup (optional) and regex fallback, URL health checking, email
outbound via SMTP, email inbound via IMAP, webhook triggering with HMAC signing and
exponential-backoff retry, and persistent session management.

All outbound URLs are validated against SSRF (Server-Side Request Forgery) protection
rules before any request is sent. Private IP ranges, loopback addresses, and
link-local addresses are blocked by default.

## Actions

| Action | Description | Risk | Permission |
|--------|-------------|------|------------|
| `http_get` | Perform an HTTP GET request | Low | `network.send` |
| `http_head` | Perform an HTTP HEAD request (headers only) | Low | `network.send` |
| `http_post` | Perform an HTTP POST with JSON, form, or raw body | Medium | `network.send` |
| `http_put` | Perform an HTTP PUT request | Medium | `network.send` |
| `http_patch` | Perform an HTTP PATCH request | Medium | `network.send` |
| `http_delete` | Perform an HTTP DELETE request | High | `network.send` |
| `download_file` | Stream-download a file to local disk | Medium | `network.send`, `filesystem.write` |
| `upload_file` | Upload a local file as multipart form data | Medium | `network.send`, `filesystem.read` |
| `graphql_query` | Execute a GraphQL query or mutation | Medium | `network.send` |
| `oauth2_get_token` | Obtain an OAuth2 access token | Medium | `network.send` |
| `parse_html` | Parse and extract content from HTML | Low | `network.send` |
| `check_url_availability` | Check URL reachability and latency | Low | `network.send` |
| `send_email` | Send an email via SMTP with attachments | High | `email.send` |
| `read_email` | Read emails from an IMAP mailbox | Medium | `email.read` |
| `webhook_trigger` | Send a webhook with HMAC signing and retry | Medium | `network.send` |
| `set_session` | Create a persistent httpx session | Low | `network.send` |
| `close_session` | Close a cached httpx session | Low | `network.send` |

## Quick Start

```yaml
actions:
  - id: fetch-api
    module: api_http
    action: http_get
    params:
      url: https://api.example.com/v1/users
      headers:
        Authorization: "Bearer {{env.API_TOKEN}}"
      timeout: 30

  - id: post-data
    module: api_http
    action: http_post
    params:
      url: https://api.example.com/v1/users
      body_json:
        name: Alice
        email: alice@example.com
      headers:
        Authorization: "Bearer {{env.API_TOKEN}}"
    depends_on: [fetch-api]
```

## Requirements

| Dependency | Required | Notes |
|-----------|----------|-------|
| `httpx>=0.27` | Yes | Async HTTP client for all HTTP operations |
| `beautifulsoup4` | Optional | Enhanced HTML parsing (falls back to regex) |
| `aiosmtplib` | Optional | Async SMTP for `send_email` (falls back to stdlib `smtplib`) |

## Configuration

Uses default LLMOS Bridge configuration. SSRF protection is built-in and always
active. Session management is handled internally via named session IDs.

### SSRF Protection

All outbound URLs are validated before requests are sent. The following are blocked
by default:

- Private IP ranges (10.x, 172.16-31.x, 192.168.x)
- Loopback addresses (127.x, ::1)
- Link-local addresses (169.254.x, fe80::)
- Metadata endpoints (169.254.169.254)

## Platform Support

| Platform | Status |
|----------|--------|
| Linux | Supported |
| macOS | Supported |
| Windows | Supported |

## Related Modules

- **database** -- Store API responses in local databases.
- **db_gateway** -- Semantic database gateway for API-to-database sync workflows.
- **filesystem** -- Read/write files involved in upload/download operations.
- **os_exec** -- Execute curl or wget commands for edge cases not covered by this module.
