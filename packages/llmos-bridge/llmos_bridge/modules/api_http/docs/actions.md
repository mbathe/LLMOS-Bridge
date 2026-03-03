# API HTTP Module -- Action Reference

## http_get

Perform an HTTP GET request.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `url` | string | Yes | -- | Target URL. |
| `headers` | object | No | `{}` | Request headers. |
| `params` | object | No | `{}` | Query parameters. |
| `timeout` | integer | No | `30` | Timeout in seconds (1--300). |
| `follow_redirects` | boolean | No | `true` | Follow HTTP redirects. |
| `verify_ssl` | boolean | No | `true` | Verify SSL certificate. |
| `auth` | array | No | -- | BasicAuth `[username, password]`. |
| `cookies` | object | No | `{}` | Cookies to send. |

**Returns:** `{status_code, headers, body, body_json, url, elapsed_ms, is_success}`

---

## http_head

Perform an HTTP HEAD request -- retrieve headers without body.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `url` | string | Yes | -- | Target URL. |
| `headers` | object | No | `{}` | Request headers. |
| `timeout` | integer | No | `10` | Timeout in seconds (1--60). |
| `follow_redirects` | boolean | No | `true` | Follow redirects. |
| `verify_ssl` | boolean | No | `true` | Verify SSL. |

**Returns:** `{status_code, headers, elapsed_ms, url, is_success}`

---

## http_post

Perform an HTTP POST request with optional JSON, form, or raw body.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `url` | string | Yes | -- | Target URL. |
| `headers` | object | No | `{}` | Request headers. |
| `body_json` | object | No | -- | JSON body (serialised to application/json). |
| `data` | object | No | -- | Form-encoded data. |
| `form` | object | No | -- | Multipart form fields (no files). |
| `raw_body` | string | No | -- | Raw string body. |
| `timeout` | integer | No | `30` | Timeout in seconds (1--300). |
| `follow_redirects` | boolean | No | `true` | Follow redirects. |
| `verify_ssl` | boolean | No | `true` | Verify SSL. |
| `auth` | array | No | -- | BasicAuth `[username, password]`. |
| `cookies` | object | No | `{}` | Cookies to send. |

**Returns:** `{status_code, headers, body, body_json, url, elapsed_ms, is_success}`

---

## http_put

Perform an HTTP PUT request.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `url` | string | Yes | -- | Target URL. |
| `headers` | object | No | `{}` | Request headers. |
| `body_json` | object | No | -- | JSON body. |
| `data` | object | No | -- | Form-encoded body. |
| `raw_body` | string | No | -- | Raw string body. |
| `timeout` | integer | No | `30` | Timeout in seconds (1--300). |
| `verify_ssl` | boolean | No | `true` | Verify SSL. |
| `auth` | array | No | -- | BasicAuth `[username, password]`. |

**Returns:** `{status_code, headers, body, body_json, url, elapsed_ms, is_success}`

---

## http_patch

Perform an HTTP PATCH request.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `url` | string | Yes | -- | Target URL. |
| `headers` | object | No | `{}` | Request headers. |
| `body_json` | object | No | -- | JSON body. |
| `data` | object | No | -- | Form-encoded body. |
| `timeout` | integer | No | `30` | Timeout in seconds (1--300). |
| `verify_ssl` | boolean | No | `true` | Verify SSL. |

**Returns:** `{status_code, headers, body, body_json, url, elapsed_ms, is_success}`

---

## http_delete

Perform an HTTP DELETE request.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `url` | string | Yes | -- | Target URL. |
| `headers` | object | No | `{}` | Request headers. |
| `timeout` | integer | No | `30` | Timeout in seconds (1--300). |
| `verify_ssl` | boolean | No | `true` | Verify SSL. |
| `body_json` | object | No | -- | Optional JSON body for DELETE with payload. |

**Returns:** `{status_code, headers, body, body_json, url, elapsed_ms, is_success}`

---

## download_file

Stream-download a file from a URL to a local destination path.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `url` | string | Yes | -- | URL to download from. |
| `destination` | string | Yes | -- | Local file path to save the download. |
| `headers` | object | No | `{}` | Request headers. |
| `timeout` | integer | No | `300` | Timeout in seconds (1--3600). |
| `verify_ssl` | boolean | No | `true` | Verify SSL. |
| `chunk_size` | integer | No | `65536` | Download chunk size in bytes (1024--10,485,760). |
| `auth` | array | No | -- | BasicAuth `[username, password]`. |
| `overwrite` | boolean | No | `true` | Overwrite existing file. |

**Returns:** `{bytes_downloaded, destination, elapsed_ms}`

---

## upload_file

Upload a local file to a URL as multipart form data.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `url` | string | Yes | -- | URL to upload to. |
| `file_path` | string | Yes | -- | Path to the local file. |
| `field_name` | string | No | `"file"` | Form field name for the file. |
| `extra_fields` | object | No | `{}` | Additional multipart form fields. |
| `headers` | object | No | `{}` | Request headers. |
| `timeout` | integer | No | `300` | Timeout in seconds (1--3600). |
| `verify_ssl` | boolean | No | `true` | Verify SSL. |
| `auth` | array | No | -- | BasicAuth `[username, password]`. |

**Returns:** `{status_code, headers, body, body_json, url, elapsed_ms, is_success}`

---

## graphql_query

Execute a GraphQL query or mutation via HTTP POST.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `url` | string | Yes | -- | GraphQL endpoint URL. |
| `query` | string | Yes | -- | GraphQL query or mutation string. |
| `variables` | object | No | `{}` | GraphQL variables. |
| `operation_name` | string | No | -- | Operation name. |
| `headers` | object | No | `{}` | HTTP headers. |
| `timeout` | integer | No | `30` | Timeout in seconds (1--300). |
| `verify_ssl` | boolean | No | `true` | Verify SSL. |
| `auth` | array | No | -- | BasicAuth `[username, password]`. |

**Returns:** `{data, errors, extensions, status_code, is_success}`

**Example:**
```json
{
  "action": "graphql_query",
  "module": "api_http",
  "params": {
    "url": "https://api.example.com/graphql",
    "query": "query { users { id name email } }",
    "headers": {"Authorization": "Bearer token123"}
  }
}
```

---

## oauth2_get_token

Obtain an OAuth2 access token from a token endpoint.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `token_url` | string | Yes | -- | OAuth2 token endpoint URL. |
| `grant_type` | string | No | `"client_credentials"` | Grant type: `client_credentials`, `password`, `authorization_code`, `refresh_token`. |
| `client_id` | string | Yes | -- | OAuth2 client ID. |
| `client_secret` | string | No | -- | OAuth2 client secret. |
| `username` | string | No | -- | Username (for password grant). |
| `password` | string | No | -- | Password (for password grant). |
| `code` | string | No | -- | Authorization code (for authorization_code grant). |
| `redirect_uri` | string | No | -- | Redirect URI (for authorization_code grant). |
| `refresh_token` | string | No | -- | Refresh token (for refresh_token grant). |
| `scope` | string | No | -- | Requested scope. |
| `extra_params` | object | No | `{}` | Additional parameters. |
| `timeout` | integer | No | `30` | Timeout in seconds (1--60). |
| `verify_ssl` | boolean | No | `true` | Verify SSL. |

**Returns:** `{access_token, token_type, expires_in, scope, refresh_token, raw_response}`

---

## parse_html

Parse and extract content from HTML (inline or fetched from URL).

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `html` | string | No | -- | Raw HTML string to parse. |
| `url` | string | No | -- | Fetch and parse this URL if `html` is not provided. |
| `selector` | string | No | -- | CSS selector to scope extraction. |
| `extract` | string | No | `"text"` | What to extract: `text`, `html`, `attrs`, `links`, `images`, `tables`, `meta`. |
| `timeout` | integer | No | `30` | HTTP fetch timeout in seconds (1--60). |

**Returns:** `{extract, result}`

---

## check_url_availability

Check whether a URL is reachable and return latency.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `url` | string | Yes | -- | URL to check. |
| `timeout` | integer | No | `10` | Timeout in seconds (1--60). |
| `expected_status` | integer | No | -- | Expected HTTP status code (100--599). |
| `verify_ssl` | boolean | No | `true` | Verify SSL. |

**Returns:** `{available, status_code, latency_ms, url, error}`

---

## send_email

Send an email via SMTP with optional attachments.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `to` | array | Yes | -- | List of recipient email addresses (min 1). |
| `subject` | string | Yes | -- | Email subject. |
| `body` | string | Yes | -- | Email body content. |
| `body_format` | string | No | `"plain"` | Body format: `plain` or `html`. |
| `cc` | array | No | `[]` | CC recipients. |
| `bcc` | array | No | `[]` | BCC recipients. |
| `reply_to` | string | No | -- | Reply-To address. |
| `attachments` | array | No | `[]` | Paths to local files to attach. |
| `smtp_host` | string | No | `"localhost"` | SMTP server host. |
| `smtp_port` | integer | No | `587` | SMTP server port (1--65535). |
| `smtp_user` | string | No | -- | SMTP username. |
| `smtp_password` | string | No | -- | SMTP password. |
| `use_tls` | boolean | No | `true` | Use STARTTLS. |
| `use_ssl` | boolean | No | `false` | Use SSL (port 465). |

**Returns:** `{sent, message_id, recipients_accepted}`

---

## read_email

Read emails from an IMAP mailbox.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `imap_host` | string | Yes | -- | IMAP server hostname. |
| `username` | string | Yes | -- | IMAP username. |
| `password` | string | Yes | -- | IMAP password. |
| `imap_port` | integer | No | `993` | IMAP port (1--65535). |
| `use_ssl` | boolean | No | `true` | Use SSL. |
| `mailbox` | string | No | `"INBOX"` | Mailbox/folder to read. |
| `max_count` | integer | No | `20` | Maximum number of messages (1--1000). |
| `unread_only` | boolean | No | `false` | Fetch only unread messages. |
| `since_date` | string | No | -- | ISO date (YYYY-MM-DD) to filter messages after. |
| `search_subject` | string | No | -- | Filter by subject keyword. |
| `search_from` | string | No | -- | Filter by sender. |
| `download_attachments` | boolean | No | `false` | Save attachments to disk. |
| `attachment_dir` | string | No | -- | Directory to save attachments. |

**Returns:** `{messages: [{uid, subject, from, to, date, body_text, body_html, has_attachments}], count}`

---

## webhook_trigger

Send a webhook request with optional HMAC signing and retry logic.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `url` | string | Yes | -- | Webhook endpoint URL. |
| `method` | string | No | `"POST"` | HTTP method: `GET`, `POST`, `PUT`, `PATCH`. |
| `headers` | object | No | `{}` | Request headers. |
| `payload` | object | No | `{}` | JSON payload. |
| `timeout` | integer | No | `10` | Timeout in seconds (1--60). |
| `verify_ssl` | boolean | No | `true` | Verify SSL. |
| `retry_on_failure` | boolean | No | `false` | Retry on non-2xx or error. |
| `max_retries` | integer | No | `3` | Maximum retry attempts (0--10). |
| `retry_delay` | number | No | `1.0` | Base retry delay in seconds (0.1--30.0). Exponential backoff. |
| `hmac_secret` | string | No | -- | HMAC-SHA256 secret for request signing (`X-Hub-Signature-256` header). |

**Returns:** `{status_code, attempts, success, body}`

---

## set_session

Create and cache a persistent httpx.AsyncClient session.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `session_id` | string | Yes | -- | Logical session name. |
| `base_url` | string | No | -- | Base URL prefix for all requests. |
| `headers` | object | No | `{}` | Default headers. |
| `cookies` | object | No | `{}` | Default cookies. |
| `auth` | array | No | -- | BasicAuth `[username, password]`. |
| `timeout` | integer | No | `30` | Default timeout in seconds (1--300). |
| `verify_ssl` | boolean | No | `true` | Verify SSL. |

**Returns:** `{session_id, base_url, created}`

---

## close_session

Close and remove a cached httpx session.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `session_id` | string | Yes | -- | Session ID to close. |

**Returns:** `{session_id, closed}`
