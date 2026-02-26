"""Typed parameter models for the ``api_http`` module — httpx + aiosmtplib + imaplib."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# HTTP Methods
# ---------------------------------------------------------------------------


class HttpGetParams(BaseModel):
    url: str
    headers: dict[str, str] = Field(default_factory=dict)
    params: dict[str, str] = Field(default_factory=dict, description="Query parameters.")
    timeout: Annotated[int, Field(ge=1, le=300)] = 30
    follow_redirects: bool = True
    verify_ssl: bool = True
    auth: tuple[str, str] | None = Field(
        default=None, description="(username, password) for basic auth."
    )
    cookies: dict[str, str] = Field(default_factory=dict)


class HttpHeadParams(BaseModel):
    url: str
    headers: dict[str, str] = Field(default_factory=dict)
    timeout: Annotated[int, Field(ge=1, le=60)] = 10
    follow_redirects: bool = True
    verify_ssl: bool = True


class HttpPostParams(BaseModel):
    url: str
    headers: dict[str, str] = Field(default_factory=dict)
    body_json: dict[str, Any] | list[Any] | None = Field(default=None, description="JSON body (serialised to application/json).")
    data: dict[str, str] | None = Field(default=None, description="Form-encoded data.")
    form: dict[str, str] | None = Field(default=None, description="Multipart form data (no files).")
    raw_body: str | None = Field(default=None, description="Raw string body.")
    timeout: Annotated[int, Field(ge=1, le=300)] = 30
    follow_redirects: bool = True
    verify_ssl: bool = True
    auth: tuple[str, str] | None = None
    cookies: dict[str, str] = Field(default_factory=dict)


class HttpPutParams(BaseModel):
    url: str
    headers: dict[str, str] = Field(default_factory=dict)
    body_json: dict[str, Any] | list[Any] | None = Field(default=None, description="JSON body.")
    data: dict[str, str] | None = None
    raw_body: str | None = None
    timeout: Annotated[int, Field(ge=1, le=300)] = 30
    verify_ssl: bool = True
    auth: tuple[str, str] | None = None


class HttpPatchParams(BaseModel):
    url: str
    headers: dict[str, str] = Field(default_factory=dict)
    body_json: dict[str, Any] | list[Any] | None = Field(default=None, description="JSON body.")
    data: dict[str, str] | None = None
    timeout: Annotated[int, Field(ge=1, le=300)] = 30
    verify_ssl: bool = True


class HttpDeleteParams(BaseModel):
    url: str
    headers: dict[str, str] = Field(default_factory=dict)
    timeout: Annotated[int, Field(ge=1, le=300)] = 30
    verify_ssl: bool = True
    body_json: dict[str, Any] | None = Field(
        default=None, description="Optional JSON body for DELETE with payload."
    )


# ---------------------------------------------------------------------------
# File transfer
# ---------------------------------------------------------------------------


class DownloadFileParams(BaseModel):
    url: str
    destination: str
    headers: dict[str, str] = Field(default_factory=dict)
    timeout: Annotated[int, Field(ge=1, le=3600)] = 300
    verify_ssl: bool = True
    chunk_size: Annotated[int, Field(ge=1024, le=10_485_760)] = 65_536
    auth: tuple[str, str] | None = None
    overwrite: bool = True


class UploadFileParams(BaseModel):
    url: str
    file_path: str = Field(description="Path to the local file to upload.")
    field_name: str = Field(default="file", description="Form field name for the file.")
    extra_fields: dict[str, str] = Field(
        default_factory=dict, description="Additional multipart form fields."
    )
    headers: dict[str, str] = Field(default_factory=dict)
    timeout: Annotated[int, Field(ge=1, le=3600)] = 300
    verify_ssl: bool = True
    auth: tuple[str, str] | None = None


# ---------------------------------------------------------------------------
# GraphQL
# ---------------------------------------------------------------------------


class GraphqlQueryParams(BaseModel):
    url: str
    query: str = Field(description="GraphQL query or mutation string.")
    variables: dict[str, Any] = Field(default_factory=dict)
    operation_name: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    timeout: Annotated[int, Field(ge=1, le=300)] = 30
    verify_ssl: bool = True
    auth: tuple[str, str] | None = None


# ---------------------------------------------------------------------------
# OAuth2
# ---------------------------------------------------------------------------


class OAuth2GetTokenParams(BaseModel):
    token_url: str
    grant_type: Literal["client_credentials", "password", "authorization_code", "refresh_token"] = "client_credentials"
    client_id: str
    client_secret: str | None = None
    username: str | None = Field(default=None, description="For 'password' grant.")
    password: str | None = Field(default=None, description="For 'password' grant.")
    code: str | None = Field(default=None, description="For 'authorization_code' grant.")
    redirect_uri: str | None = Field(default=None, description="For 'authorization_code' grant.")
    refresh_token: str | None = Field(default=None, description="For 'refresh_token' grant.")
    scope: str | None = None
    extra_params: dict[str, str] = Field(default_factory=dict)
    timeout: Annotated[int, Field(ge=1, le=60)] = 30
    verify_ssl: bool = True


# ---------------------------------------------------------------------------
# HTML parsing
# ---------------------------------------------------------------------------


class ParseHtmlParams(BaseModel):
    html: str | None = Field(default=None, description="Raw HTML string to parse.")
    url: str | None = Field(default=None, description="Fetch and parse this URL if html is None.")
    selector: str | None = Field(
        default=None, description="CSS selector to extract elements."
    )
    extract: Literal["text", "html", "attrs", "links", "images", "tables", "meta"] = "text"
    timeout: Annotated[int, Field(ge=1, le=60)] = 30


# ---------------------------------------------------------------------------
# URL health
# ---------------------------------------------------------------------------


class CheckUrlAvailabilityParams(BaseModel):
    url: str
    timeout: Annotated[int, Field(ge=1, le=60)] = 10
    expected_status: Annotated[int, Field(ge=100, le=599)] | None = None
    verify_ssl: bool = True


# ---------------------------------------------------------------------------
# Email — outbound (SMTP)
# ---------------------------------------------------------------------------


class SendEmailParams(BaseModel):
    to: list[str] = Field(min_length=1)
    subject: str
    body: str
    body_format: Literal["plain", "html"] = "plain"
    cc: list[str] = Field(default_factory=list)
    bcc: list[str] = Field(default_factory=list)
    reply_to: str | None = None
    attachments: list[str] = Field(
        default_factory=list, description="Paths to local files to attach."
    )
    smtp_host: str = Field(default="localhost")
    smtp_port: Annotated[int, Field(ge=1, le=65535)] = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    use_tls: bool = True
    use_ssl: bool = False


# ---------------------------------------------------------------------------
# Email — inbound (IMAP)
# ---------------------------------------------------------------------------


class ReadEmailParams(BaseModel):
    imap_host: str
    imap_port: Annotated[int, Field(ge=1, le=65535)] = 993
    username: str
    password: str
    use_ssl: bool = True
    mailbox: str = "INBOX"
    max_count: Annotated[int, Field(ge=1, le=1000)] = 20
    unread_only: bool = False
    since_date: str | None = Field(
        default=None, description="ISO date string (YYYY-MM-DD) to filter emails after."
    )
    search_subject: str | None = None
    search_from: str | None = None
    download_attachments: bool = False
    attachment_dir: str | None = Field(
        default=None, description="Directory to save attachments."
    )


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------


class WebhookTriggerParams(BaseModel):
    url: str
    method: Literal["GET", "POST", "PUT", "PATCH"] = "POST"
    headers: dict[str, str] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)
    timeout: Annotated[int, Field(ge=1, le=60)] = 10
    verify_ssl: bool = True
    retry_on_failure: bool = False
    max_retries: Annotated[int, Field(ge=0, le=10)] = 3
    retry_delay: float = Field(default=1.0, ge=0.1, le=30.0)
    hmac_secret: str | None = Field(
        default=None, description="Sign payload with HMAC-SHA256 (X-Hub-Signature-256 header)."
    )


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------


class SetSessionParams(BaseModel):
    session_id: str = Field(description="Logical session name.")
    base_url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    cookies: dict[str, str] = Field(default_factory=dict)
    auth: tuple[str, str] | None = None
    timeout: Annotated[int, Field(ge=1, le=300)] = 30
    verify_ssl: bool = True


class CloseSessionParams(BaseModel):
    session_id: str


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

PARAMS_MAP: dict[str, type[BaseModel]] = {
    # HTTP methods
    "http_get": HttpGetParams,
    "http_head": HttpHeadParams,
    "http_post": HttpPostParams,
    "http_put": HttpPutParams,
    "http_patch": HttpPatchParams,
    "http_delete": HttpDeleteParams,
    # File transfer
    "download_file": DownloadFileParams,
    "upload_file": UploadFileParams,
    # GraphQL
    "graphql_query": GraphqlQueryParams,
    # OAuth2
    "oauth2_get_token": OAuth2GetTokenParams,
    # HTML parsing
    "parse_html": ParseHtmlParams,
    # URL health
    "check_url_availability": CheckUrlAvailabilityParams,
    # Email
    "send_email": SendEmailParams,
    "read_email": ReadEmailParams,
    # Webhooks
    "webhook_trigger": WebhookTriggerParams,
    # Session
    "set_session": SetSessionParams,
    "close_session": CloseSessionParams,
}
