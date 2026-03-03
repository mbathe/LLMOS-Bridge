# Changelog

All notable changes to the **api_http** module will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this module adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-02-01

### Added

- Full HTTP method suite: `http_get`, `http_head`, `http_post`, `http_put`, `http_patch`, `http_delete`.
- Streaming file download (`download_file`) with chunked transfer and progress tracking.
- Multipart file upload (`upload_file`) with extra form fields support.
- GraphQL queries and mutations (`graphql_query`) via HTTP POST.
- OAuth2 token acquisition (`oauth2_get_token`) for all standard grant types: client_credentials, password, authorization_code, refresh_token.
- HTML parsing (`parse_html`) with BeautifulSoup (optional) and regex fallback. Supports extraction modes: text, html, attrs, links, images, tables, meta.
- URL health check (`check_url_availability`) with latency measurement and expected status code validation.
- Email outbound (`send_email`) via SMTP with STARTTLS/SSL, CC/BCC, reply-to, and file attachments.
- Email inbound (`read_email`) via IMAP with search filters (unread, date, subject, sender) and attachment download.
- Webhook triggering (`webhook_trigger`) with HMAC-SHA256 signing, exponential-backoff retry, and configurable HTTP method.
- Persistent session management (`set_session`, `close_session`) for reusable httpx.AsyncClient instances.
- SSRF protection: all outbound URLs validated against private/loopback/link-local/metadata IP ranges.
- Security decorators: `@requires_permission`, `@sensitive_action`, `@audit_trail`, `@rate_limited`, `@data_classification`.
