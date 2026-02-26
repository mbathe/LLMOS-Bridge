"""API/HTTP module — Implementation.

Covers:
  - Full HTTP method suite (GET, HEAD, POST, PUT, PATCH, DELETE) via httpx.AsyncClient
  - Streaming file download and multipart file upload
  - GraphQL queries and mutations
  - OAuth2 token acquisition (all standard grant types)
  - HTML parsing with BeautifulSoup (optional) and regex fallback
  - URL availability/health check
  - Email outbound via aiosmtplib (async, with smtplib fallback)
  - Email inbound via imaplib (run in thread)
  - Webhook trigger with HMAC signing and exponential-backoff retry
  - Persistent httpx.AsyncClient session management
"""

from __future__ import annotations

import asyncio
import email as email_lib
import email.mime.multipart
import email.mime.text
import email.mime.base
import email.encoders
import hashlib
import hmac
import imaplib
import io
import json
import os
import smtplib
import time
from email.parser import BytesParser
from pathlib import Path
from typing import Any

from llmos_bridge.modules.base import BaseModule, Platform
from llmos_bridge.modules.manifest import ActionSpec, ModuleManifest, ParamSpec
from llmos_bridge.protocol.params.api_http import (
    CheckUrlAvailabilityParams,
    CloseSessionParams,
    DownloadFileParams,
    GraphqlQueryParams,
    HttpDeleteParams,
    HttpGetParams,
    HttpHeadParams,
    HttpPatchParams,
    HttpPostParams,
    HttpPutParams,
    OAuth2GetTokenParams,
    ParseHtmlParams,
    ReadEmailParams,
    SendEmailParams,
    SetSessionParams,
    UploadFileParams,
    WebhookTriggerParams,
)

# ---------------------------------------------------------------------------
# Lazy httpx reference — populated in _check_dependencies
# ---------------------------------------------------------------------------
_httpx: Any = None


class ApiHttpModule(BaseModule):
    MODULE_ID = "api_http"
    VERSION = "1.0.0"
    SUPPORTED_PLATFORMS = [Platform.ALL]

    def __init__(self) -> None:
        self._sessions: dict[str, Any] = {}  # session_id -> httpx.AsyncClient
        super().__init__()

    # ------------------------------------------------------------------
    # Dependency check
    # ------------------------------------------------------------------

    def _check_dependencies(self) -> None:
        global _httpx
        try:
            import httpx as _httpx_mod  # noqa: PLC0415
            _httpx = _httpx_mod
        except ImportError as exc:
            from llmos_bridge.exceptions import ModuleLoadError  # noqa: PLC0415

            raise ModuleLoadError(
                "api_http",
                "httpx is required: pip install 'httpx>=0.27'",
            ) from exc

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_response(self, response: Any) -> dict[str, Any]:
        """Convert an httpx.Response to a standard result dict."""
        body = response.text
        body_json = None
        try:
            body_json = response.json()
        except Exception:
            pass

        elapsed_ms: float | None = None
        if hasattr(response, "elapsed") and response.elapsed is not None:
            elapsed_ms = response.elapsed.total_seconds() * 1000

        return {
            "status_code": response.status_code,
            "headers": dict(response.headers),
            "body": body,
            "body_json": body_json,
            "url": str(response.url),
            "elapsed_ms": elapsed_ms,
            "is_success": response.is_success,
        }

    def _make_auth(self, auth: tuple[str, str] | None) -> Any:
        """Return an httpx.BasicAuth if credentials provided, else None."""
        if auth:
            return _httpx.BasicAuth(auth[0], auth[1])
        return None

    # ------------------------------------------------------------------
    # HTTP Methods
    # ------------------------------------------------------------------

    async def _action_http_get(self, params: dict[str, Any]) -> dict[str, Any]:
        p = HttpGetParams.model_validate(params)
        async with _httpx.AsyncClient(
            verify=p.verify_ssl,
            follow_redirects=p.follow_redirects,
            timeout=p.timeout,
            cookies=p.cookies,
            auth=self._make_auth(p.auth),
        ) as client:
            response = await client.get(p.url, headers=p.headers, params=p.params)
        return self._build_response(response)

    async def _action_http_head(self, params: dict[str, Any]) -> dict[str, Any]:
        p = HttpHeadParams.model_validate(params)
        async with _httpx.AsyncClient(
            verify=p.verify_ssl,
            follow_redirects=p.follow_redirects,
            timeout=p.timeout,
        ) as client:
            response = await client.head(p.url, headers=p.headers)

        elapsed_ms: float | None = None
        if hasattr(response, "elapsed") and response.elapsed is not None:
            elapsed_ms = response.elapsed.total_seconds() * 1000

        return {
            "status_code": response.status_code,
            "headers": dict(response.headers),
            "elapsed_ms": elapsed_ms,
            "url": str(response.url),
            "is_success": response.is_success,
        }

    async def _action_http_post(self, params: dict[str, Any]) -> dict[str, Any]:
        p = HttpPostParams.model_validate(params)

        # Resolve body: precedence — json > data > form > raw_body
        kwargs: dict[str, Any] = {}
        if p.body_json is not None:
            kwargs["json"] = p.body_json
        elif p.data is not None:
            kwargs["data"] = p.data
        elif p.form is not None:
            kwargs["data"] = p.form
        elif p.raw_body is not None:
            kwargs["content"] = p.raw_body.encode()

        async with _httpx.AsyncClient(
            verify=p.verify_ssl,
            follow_redirects=p.follow_redirects,
            timeout=p.timeout,
            cookies=p.cookies,
            auth=self._make_auth(p.auth),
        ) as client:
            response = await client.post(p.url, headers=p.headers, **kwargs)
        return self._build_response(response)

    async def _action_http_put(self, params: dict[str, Any]) -> dict[str, Any]:
        p = HttpPutParams.model_validate(params)

        kwargs: dict[str, Any] = {}
        if p.body_json is not None:
            kwargs["json"] = p.body_json
        elif p.data is not None:
            kwargs["data"] = p.data
        elif p.raw_body is not None:
            kwargs["content"] = p.raw_body.encode()

        async with _httpx.AsyncClient(
            verify=p.verify_ssl,
            timeout=p.timeout,
            auth=self._make_auth(p.auth),
        ) as client:
            response = await client.put(p.url, headers=p.headers, **kwargs)
        return self._build_response(response)

    async def _action_http_patch(self, params: dict[str, Any]) -> dict[str, Any]:
        p = HttpPatchParams.model_validate(params)

        kwargs: dict[str, Any] = {}
        if p.body_json is not None:
            kwargs["json"] = p.body_json
        elif p.data is not None:
            kwargs["data"] = p.data

        async with _httpx.AsyncClient(
            verify=p.verify_ssl,
            timeout=p.timeout,
        ) as client:
            response = await client.patch(p.url, headers=p.headers, **kwargs)
        return self._build_response(response)

    async def _action_http_delete(self, params: dict[str, Any]) -> dict[str, Any]:
        p = HttpDeleteParams.model_validate(params)

        kwargs: dict[str, Any] = {}
        if p.body_json is not None:
            kwargs["json"] = p.body_json

        async with _httpx.AsyncClient(
            verify=p.verify_ssl,
            timeout=p.timeout,
        ) as client:
            response = await client.delete(p.url, headers=p.headers, **kwargs)
        return self._build_response(response)

    # ------------------------------------------------------------------
    # File transfer
    # ------------------------------------------------------------------

    async def _action_download_file(self, params: dict[str, Any]) -> dict[str, Any]:
        p = DownloadFileParams.model_validate(params)
        destination = Path(p.destination)

        if destination.exists() and not p.overwrite:
            raise FileExistsError(
                f"Destination already exists and overwrite=False: {destination}"
            )

        destination.parent.mkdir(parents=True, exist_ok=True)

        start = time.monotonic()
        bytes_downloaded = 0

        async with _httpx.AsyncClient(
            verify=p.verify_ssl,
            timeout=p.timeout,
            auth=self._make_auth(p.auth),
        ) as client:
            async with client.stream("GET", p.url, headers=p.headers) as response:
                response.raise_for_status()
                with open(destination, "wb") as fh:
                    async for chunk in response.aiter_bytes(p.chunk_size):
                        fh.write(chunk)
                        bytes_downloaded += len(chunk)

        elapsed_ms = (time.monotonic() - start) * 1000
        return {
            "bytes_downloaded": bytes_downloaded,
            "destination": str(destination),
            "elapsed_ms": elapsed_ms,
        }

    async def _action_upload_file(self, params: dict[str, Any]) -> dict[str, Any]:
        p = UploadFileParams.model_validate(params)
        file_path = Path(p.file_path)

        if not file_path.exists():
            raise FileNotFoundError(f"File to upload not found: {file_path}")

        file_bytes = await asyncio.to_thread(file_path.read_bytes)

        files = {p.field_name: (file_path.name, file_bytes)}
        data = dict(p.extra_fields)

        async with _httpx.AsyncClient(
            verify=p.verify_ssl,
            timeout=p.timeout,
            auth=self._make_auth(p.auth),
        ) as client:
            response = await client.post(
                p.url,
                headers=p.headers,
                files=files,
                data=data,
            )

        return self._build_response(response)

    # ------------------------------------------------------------------
    # GraphQL
    # ------------------------------------------------------------------

    async def _action_graphql_query(self, params: dict[str, Any]) -> dict[str, Any]:
        p = GraphqlQueryParams.model_validate(params)

        payload: dict[str, Any] = {"query": p.query, "variables": p.variables}
        if p.operation_name:
            payload["operationName"] = p.operation_name

        async with _httpx.AsyncClient(
            verify=p.verify_ssl,
            timeout=p.timeout,
            auth=self._make_auth(p.auth),
        ) as client:
            response = await client.post(p.url, json=payload, headers=p.headers)

        raw: dict[str, Any] = {}
        try:
            raw = response.json()
        except Exception:
            pass

        return {
            "data": raw.get("data"),
            "errors": raw.get("errors"),
            "extensions": raw.get("extensions"),
            "status_code": response.status_code,
            "is_success": response.is_success,
        }

    # ------------------------------------------------------------------
    # OAuth2
    # ------------------------------------------------------------------

    async def _action_oauth2_get_token(self, params: dict[str, Any]) -> dict[str, Any]:
        p = OAuth2GetTokenParams.model_validate(params)

        form_data: dict[str, str] = {
            "grant_type": p.grant_type,
            "client_id": p.client_id,
        }
        if p.client_secret:
            form_data["client_secret"] = p.client_secret
        if p.username:
            form_data["username"] = p.username
        if p.password:
            form_data["password"] = p.password
        if p.code:
            form_data["code"] = p.code
        if p.redirect_uri:
            form_data["redirect_uri"] = p.redirect_uri
        if p.refresh_token:
            form_data["refresh_token"] = p.refresh_token
        if p.scope:
            form_data["scope"] = p.scope
        form_data.update(p.extra_params)

        async with _httpx.AsyncClient(
            verify=p.verify_ssl,
            timeout=p.timeout,
        ) as client:
            response = await client.post(
                p.token_url,
                data=form_data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )

        try:
            raw = response.json()
        except Exception as exc:
            raise ValueError(
                f"OAuth2 token endpoint returned non-JSON body "
                f"(status={response.status_code}): {response.text[:500]}"
            ) from exc

        if not response.is_success:
            error_desc = raw.get("error_description") or raw.get("error") or response.text
            raise PermissionError(
                f"OAuth2 token request failed (status={response.status_code}): {error_desc}"
            )

        return {
            "access_token": raw.get("access_token"),
            "token_type": raw.get("token_type"),
            "expires_in": raw.get("expires_in"),
            "scope": raw.get("scope"),
            "refresh_token": raw.get("refresh_token"),
            "raw_response": raw,
        }

    # ------------------------------------------------------------------
    # HTML parsing
    # ------------------------------------------------------------------

    async def _action_parse_html(self, params: dict[str, Any]) -> dict[str, Any]:
        p = ParseHtmlParams.model_validate(params)

        html_content: str | None = p.html

        # Fetch from URL if no inline HTML provided
        if html_content is None:
            if p.url is None:
                raise ValueError("Either 'html' or 'url' must be provided for parse_html.")
            async with _httpx.AsyncClient(timeout=p.timeout) as client:
                response = await client.get(p.url)
                response.raise_for_status()
                html_content = response.text

        # Attempt BeautifulSoup parsing
        try:
            from bs4 import BeautifulSoup  # noqa: PLC0415

            soup = BeautifulSoup(html_content, "html.parser")
            root = soup.select(p.selector) if p.selector else [soup]

            if p.extract == "text":
                text_parts = [el.get_text(separator=" ", strip=True) for el in root]
                return {"extract": "text", "result": " ".join(text_parts)}

            if p.extract == "html":
                return {"extract": "html", "result": [str(el) for el in root]}

            if p.extract == "attrs":
                return {
                    "extract": "attrs",
                    "result": [el.attrs for el in root],  # type: ignore[attr-defined]
                }

            if p.extract == "links":
                links = []
                for el in root:
                    for tag in (el.find_all("a") if el.name != "a" else [el]):  # type: ignore[union-attr]
                        links.append({"text": tag.get_text(strip=True), "href": tag.get("href", "")})
                return {"extract": "links", "result": links}

            if p.extract == "images":
                images = []
                for el in root:
                    for tag in (el.find_all("img") if el.name != "img" else [el]):  # type: ignore[union-attr]
                        images.append({"src": tag.get("src", ""), "alt": tag.get("alt", "")})
                return {"extract": "images", "result": images}

            if p.extract == "tables":
                tables = []
                for el in root:
                    for table in (el.find_all("table") if el.name != "table" else [el]):  # type: ignore[union-attr]
                        rows = []
                        for tr in table.find_all("tr"):  # type: ignore[union-attr]
                            cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
                            rows.append(cells)
                        tables.append(rows)
                return {"extract": "tables", "result": tables}

            if p.extract == "meta":
                title_tag = soup.find("title")
                desc_tag = soup.find("meta", attrs={"name": "description"})
                canonical_tag = soup.find("link", attrs={"rel": "canonical"})
                og_tags = {
                    tag.get("property", ""): tag.get("content", "")
                    for tag in soup.find_all("meta")
                    if tag.get("property", "").startswith("og:")
                }
                return {
                    "extract": "meta",
                    "result": {
                        "title": title_tag.get_text(strip=True) if title_tag else None,
                        "description": desc_tag.get("content") if desc_tag else None,  # type: ignore[union-attr]
                        "og_tags": og_tags,
                        "canonical": canonical_tag.get("href") if canonical_tag else None,  # type: ignore[union-attr]
                    },
                }

            return {"extract": p.extract, "result": None}

        except ImportError:
            # Fallback: basic regex-based extraction
            return await self._parse_html_regex_fallback(html_content, p)

    async def _parse_html_regex_fallback(
        self, html_content: str, p: ParseHtmlParams
    ) -> dict[str, Any]:
        """Regex-based HTML extraction when BeautifulSoup is not available."""
        import re  # noqa: PLC0415

        if p.extract == "text":
            text = re.sub(r"<[^>]+>", " ", html_content)
            text = re.sub(r"\s+", " ", text).strip()
            return {"extract": "text", "result": text, "_fallback": True}

        if p.extract == "links":
            links = [
                {"text": m.group(2).strip(), "href": m.group(1)}
                for m in re.finditer(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html_content, re.DOTALL | re.IGNORECASE)
            ]
            return {"extract": "links", "result": links, "_fallback": True}

        if p.extract == "images":
            images = [
                {"src": m.group(1), "alt": m.group(2) or ""}
                for m in re.finditer(
                    r'<img[^>]+src=["\']([^"\']+)["\'](?:[^>]+alt=["\']([^"\']*)["\'])?[^>]*>',
                    html_content, re.IGNORECASE
                )
            ]
            return {"extract": "images", "result": images, "_fallback": True}

        if p.extract == "meta":
            title_m = re.search(r"<title[^>]*>(.*?)</title>", html_content, re.DOTALL | re.IGNORECASE)
            desc_m = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']', html_content, re.IGNORECASE)
            return {
                "extract": "meta",
                "result": {
                    "title": title_m.group(1).strip() if title_m else None,
                    "description": desc_m.group(1) if desc_m else None,
                    "og_tags": {},
                    "canonical": None,
                },
                "_fallback": True,
            }

        # For unsupported extracts in fallback, return raw text
        text = re.sub(r"<[^>]+>", " ", html_content)
        text = re.sub(r"\s+", " ", text).strip()
        return {"extract": p.extract, "result": text, "_fallback": True}

    # ------------------------------------------------------------------
    # URL health
    # ------------------------------------------------------------------

    async def _action_check_url_availability(self, params: dict[str, Any]) -> dict[str, Any]:
        p = CheckUrlAvailabilityParams.model_validate(params)
        start = time.monotonic()
        error: str | None = None
        status_code: int | None = None
        available = False

        try:
            async with _httpx.AsyncClient(
                verify=p.verify_ssl,
                follow_redirects=True,
                timeout=p.timeout,
            ) as client:
                try:
                    response = await client.head(p.url)
                except _httpx.UnsupportedProtocol:
                    response = await client.get(p.url)
                status_code = response.status_code
                if p.expected_status is not None:
                    available = status_code == p.expected_status
                else:
                    available = response.is_success
        except _httpx.TimeoutException as exc:
            error = f"Request timed out after {p.timeout}s: {exc}"
        except _httpx.ConnectError as exc:
            error = f"Connection error: {exc}"
        except Exception as exc:
            error = f"Unexpected error: {exc}"

        latency_ms = (time.monotonic() - start) * 1000
        return {
            "available": available,
            "status_code": status_code,
            "latency_ms": latency_ms,
            "url": p.url,
            "error": error,
        }

    # ------------------------------------------------------------------
    # Email — outbound (SMTP)
    # ------------------------------------------------------------------

    async def _action_send_email(self, params: dict[str, Any]) -> dict[str, Any]:
        p = SendEmailParams.model_validate(params)
        return await asyncio.to_thread(self._send_email_sync, p)

    def _send_email_sync(self, p: SendEmailParams) -> dict[str, Any]:
        # Build the MIME message
        msg = email.mime.multipart.MIMEMultipart("alternative" if not p.attachments else "mixed")
        msg["Subject"] = p.subject
        msg["From"] = p.smtp_user or ""
        msg["To"] = ", ".join(p.to)
        if p.cc:
            msg["Cc"] = ", ".join(p.cc)
        if p.reply_to:
            msg["Reply-To"] = p.reply_to

        # Body part
        body_part = email.mime.text.MIMEText(p.body, p.body_format, "utf-8")
        msg.attach(body_part)

        # Attachments
        for attachment_path in p.attachments:
            path = Path(attachment_path)
            if not path.exists():
                raise FileNotFoundError(f"Attachment file not found: {path}")
            with path.open("rb") as fh:
                data = fh.read()
            part = email.mime.base.MIMEBase("application", "octet-stream")
            part.set_payload(data)
            email.encoders.encode_base64(part)
            part.add_header("Content-Disposition", "attachment", filename=path.name)
            msg.attach(part)

        all_recipients = list(p.to) + list(p.cc) + list(p.bcc)
        message_id = msg.get("Message-ID", f"<llmos-{time.time()}@llmos-bridge>")
        recipients_accepted: list[str] = []

        # Try aiosmtplib first (already in sync thread, so use smtplib directly)
        if p.use_ssl:
            smtp_cls = smtplib.SMTP_SSL
            smtp = smtp_cls(p.smtp_host, p.smtp_port)
        else:
            smtp = smtplib.SMTP(p.smtp_host, p.smtp_port)
            if p.use_tls:
                smtp.starttls()

        try:
            if p.smtp_user and p.smtp_password:
                smtp.login(p.smtp_user, p.smtp_password)
            refused = smtp.sendmail(p.smtp_user or "", all_recipients, msg.as_bytes())
            recipients_accepted = [r for r in all_recipients if r not in refused]
        finally:
            smtp.quit()

        return {
            "sent": True,
            "message_id": message_id,
            "recipients_accepted": recipients_accepted,
        }

    # ------------------------------------------------------------------
    # Email — inbound (IMAP)
    # ------------------------------------------------------------------

    async def _action_read_email(self, params: dict[str, Any]) -> dict[str, Any]:
        p = ReadEmailParams.model_validate(params)
        messages = await asyncio.to_thread(self._read_email_sync, p)
        return {"messages": messages, "count": len(messages)}

    def _read_email_sync(self, p: ReadEmailParams) -> list[dict[str, Any]]:
        if p.use_ssl:
            imap = imaplib.IMAP4_SSL(p.imap_host, p.imap_port)
        else:
            imap = imaplib.IMAP4(p.imap_host, p.imap_port)

        try:
            imap.login(p.username, p.password)
            imap.select(p.mailbox, readonly=not p.download_attachments)

            # Build search criteria
            criteria_parts: list[str] = []
            if p.unread_only:
                criteria_parts.append("UNSEEN")
            if p.since_date:
                # Convert YYYY-MM-DD to DD-Mon-YYYY for IMAP
                from datetime import datetime  # noqa: PLC0415
                dt = datetime.fromisoformat(p.since_date)
                imap_date = dt.strftime("%d-%b-%Y")
                criteria_parts.append(f"SINCE {imap_date}")
            if p.search_subject:
                criteria_parts.append(f'SUBJECT "{p.search_subject}"')
            if p.search_from:
                criteria_parts.append(f'FROM "{p.search_from}"')

            criteria = " ".join(criteria_parts) if criteria_parts else "ALL"

            _status, data = imap.search(None, criteria)
            if _status != "OK":
                return []

            msg_ids = data[0].split() if data[0] else []
            # Most recent first — take last max_count
            msg_ids = msg_ids[-p.max_count:]
            msg_ids = list(reversed(msg_ids))

            messages: list[dict[str, Any]] = []
            attachment_dir = Path(p.attachment_dir) if p.attachment_dir else None
            if attachment_dir:
                attachment_dir.mkdir(parents=True, exist_ok=True)

            for uid in msg_ids:
                _status, msg_data = imap.fetch(uid, "(RFC822)")
                if _status != "OK" or not msg_data or msg_data[0] is None:
                    continue

                raw_bytes = msg_data[0][1]  # type: ignore[index]
                parsed = BytesParser().parsebytes(raw_bytes)

                body_text: str | None = None
                body_html: str | None = None
                has_attachments = False

                if parsed.is_multipart():
                    for part in parsed.walk():
                        content_type = part.get_content_type()
                        disposition = str(part.get("Content-Disposition", ""))

                        if "attachment" in disposition:
                            has_attachments = True
                            if p.download_attachments and attachment_dir:
                                filename = part.get_filename() or f"attachment_{uid.decode()}"
                                safe_name = Path(filename).name
                                dest = attachment_dir / safe_name
                                payload = part.get_payload(decode=True)
                                if payload:
                                    dest.write_bytes(payload)
                        elif content_type == "text/plain" and body_text is None:
                            payload = part.get_payload(decode=True)
                            if payload:
                                body_text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
                        elif content_type == "text/html" and body_html is None:
                            payload = part.get_payload(decode=True)
                            if payload:
                                body_html = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
                else:
                    payload = parsed.get_payload(decode=True)
                    if payload:
                        charset = parsed.get_content_charset() or "utf-8"
                        if parsed.get_content_type() == "text/html":
                            body_html = payload.decode(charset, errors="replace")
                        else:
                            body_text = payload.decode(charset, errors="replace")

                messages.append({
                    "uid": uid.decode(),
                    "subject": parsed.get("Subject", ""),
                    "from": parsed.get("From", ""),
                    "to": parsed.get("To", ""),
                    "date": parsed.get("Date", ""),
                    "body_text": body_text,
                    "body_html": body_html,
                    "has_attachments": has_attachments,
                })

            return messages

        finally:
            try:
                imap.close()
                imap.logout()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Webhooks
    # ------------------------------------------------------------------

    async def _action_webhook_trigger(self, params: dict[str, Any]) -> dict[str, Any]:
        p = WebhookTriggerParams.model_validate(params)

        headers = dict(p.headers)
        body_bytes = json.dumps(p.payload, ensure_ascii=False).encode("utf-8")

        # Optional HMAC signing
        if p.hmac_secret:
            signature = hmac.new(
                p.hmac_secret.encode("utf-8"),
                body_bytes,
                hashlib.sha256,
            ).hexdigest()
            headers["X-Hub-Signature-256"] = f"sha256={signature}"

        method = p.method.upper()
        attempts = 0
        status_code: int | None = None
        response_body: str = ""
        success = False
        last_exc: Exception | None = None

        max_attempts = (p.max_retries + 1) if p.retry_on_failure else 1

        for attempt in range(1, max_attempts + 1):
            attempts = attempt
            try:
                async with _httpx.AsyncClient(
                    verify=p.verify_ssl,
                    timeout=p.timeout,
                ) as client:
                    request_kwargs: dict[str, Any] = {
                        "headers": {**headers, "Content-Type": "application/json"},
                        "content": body_bytes,
                    }
                    if method == "GET":
                        request_kwargs.pop("content", None)
                        response = await client.get(p.url, headers=request_kwargs["headers"])
                    elif method == "POST":
                        response = await client.post(p.url, **request_kwargs)
                    elif method == "PUT":
                        response = await client.put(p.url, **request_kwargs)
                    elif method == "PATCH":
                        response = await client.patch(p.url, **request_kwargs)
                    else:
                        response = await client.post(p.url, **request_kwargs)

                    status_code = response.status_code
                    response_body = response.text
                    success = response.is_success

                    if success:
                        break

                    if p.retry_on_failure and attempt < max_attempts:
                        delay = p.retry_delay * (2 ** (attempt - 1))
                        await asyncio.sleep(delay)

            except Exception as exc:
                last_exc = exc
                if p.retry_on_failure and attempt < max_attempts:
                    delay = p.retry_delay * (2 ** (attempt - 1))
                    await asyncio.sleep(delay)
                else:
                    raise

        return {
            "status_code": status_code,
            "attempts": attempts,
            "success": success,
            "body": response_body,
        }

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    async def _action_set_session(self, params: dict[str, Any]) -> dict[str, Any]:
        p = SetSessionParams.model_validate(params)

        # Close existing session with same ID if present
        if p.session_id in self._sessions:
            try:
                await self._sessions[p.session_id].aclose()
            except Exception:
                pass

        client_kwargs: dict[str, Any] = {
            "verify": p.verify_ssl,
            "timeout": p.timeout,
            "headers": p.headers,
            "cookies": p.cookies,
        }
        if p.base_url:
            client_kwargs["base_url"] = p.base_url
        if p.auth:
            client_kwargs["auth"] = _httpx.BasicAuth(p.auth[0], p.auth[1])

        client = _httpx.AsyncClient(**client_kwargs)
        self._sessions[p.session_id] = client

        return {
            "session_id": p.session_id,
            "base_url": p.base_url,
            "created": True,
        }

    async def _action_close_session(self, params: dict[str, Any]) -> dict[str, Any]:
        p = CloseSessionParams.model_validate(params)

        if p.session_id not in self._sessions:
            return {"session_id": p.session_id, "closed": False, "reason": "Session not found."}

        client = self._sessions.pop(p.session_id)
        try:
            await client.aclose()
        except Exception as exc:
            return {"session_id": p.session_id, "closed": False, "reason": str(exc)}

        return {"session_id": p.session_id, "closed": True}

    # ------------------------------------------------------------------
    # Manifest
    # ------------------------------------------------------------------

    def get_manifest(self) -> ModuleManifest:
        return ModuleManifest(
            module_id=self.MODULE_ID,
            version=self.VERSION,
            description=(
                "HTTP requests, file transfer, email, GraphQL, OAuth2, "
                "and webhook automation."
            ),
            platforms=["all"],
            tags=["http", "api", "rest", "email", "webhook", "network"],
            dependencies=["httpx>=0.27"],
            declared_permissions=["network_access"],
            actions=[
                # ---- HTTP methods ----------------------------------------
                ActionSpec(
                    name="http_get",
                    description="Perform an HTTP GET request.",
                    params=[
                        ParamSpec("url", "string", "Target URL."),
                        ParamSpec("headers", "object", "Request headers.", required=False, default={}),
                        ParamSpec("params", "object", "Query parameters.", required=False, default={}),
                        ParamSpec("timeout", "integer", "Timeout in seconds.", required=False, default=30),
                        ParamSpec("follow_redirects", "boolean", "Follow HTTP redirects.", required=False, default=True),
                        ParamSpec("verify_ssl", "boolean", "Verify SSL certificate.", required=False, default=True),
                        ParamSpec("auth", "array", "BasicAuth [username, password].", required=False),
                        ParamSpec("cookies", "object", "Cookies to send.", required=False, default={}),
                    ],
                    returns="object",
                    returns_description="{status_code, headers, body, body_json, url, elapsed_ms, is_success}",
                    tags=["http", "rest", "get"],
                ),
                ActionSpec(
                    name="http_head",
                    description="Perform an HTTP HEAD request — retrieve headers without body.",
                    params=[
                        ParamSpec("url", "string", "Target URL."),
                        ParamSpec("headers", "object", "Request headers.", required=False, default={}),
                        ParamSpec("timeout", "integer", "Timeout in seconds.", required=False, default=10),
                        ParamSpec("follow_redirects", "boolean", "Follow redirects.", required=False, default=True),
                        ParamSpec("verify_ssl", "boolean", "Verify SSL.", required=False, default=True),
                    ],
                    returns="object",
                    returns_description="{status_code, headers, elapsed_ms, url, is_success}",
                    tags=["http", "head"],
                ),
                ActionSpec(
                    name="http_post",
                    description="Perform an HTTP POST request with optional JSON, form, or raw body.",
                    params=[
                        ParamSpec("url", "string", "Target URL."),
                        ParamSpec("headers", "object", "Request headers.", required=False, default={}),
                        ParamSpec("json", "object", "JSON body payload.", required=False),
                        ParamSpec("data", "object", "Form-encoded data.", required=False),
                        ParamSpec("form", "object", "Multipart form fields.", required=False),
                        ParamSpec("raw_body", "string", "Raw string body.", required=False),
                        ParamSpec("timeout", "integer", "Timeout in seconds.", required=False, default=30),
                        ParamSpec("follow_redirects", "boolean", "Follow redirects.", required=False, default=True),
                        ParamSpec("verify_ssl", "boolean", "Verify SSL.", required=False, default=True),
                        ParamSpec("auth", "array", "BasicAuth [username, password].", required=False),
                        ParamSpec("cookies", "object", "Cookies to send.", required=False, default={}),
                    ],
                    returns="object",
                    returns_description="{status_code, headers, body, body_json, url, elapsed_ms, is_success}",
                    tags=["http", "rest", "post"],
                ),
                ActionSpec(
                    name="http_put",
                    description="Perform an HTTP PUT request.",
                    params=[
                        ParamSpec("url", "string", "Target URL."),
                        ParamSpec("headers", "object", "Request headers.", required=False, default={}),
                        ParamSpec("json", "object", "JSON body.", required=False),
                        ParamSpec("data", "object", "Form-encoded body.", required=False),
                        ParamSpec("raw_body", "string", "Raw string body.", required=False),
                        ParamSpec("timeout", "integer", "Timeout in seconds.", required=False, default=30),
                        ParamSpec("verify_ssl", "boolean", "Verify SSL.", required=False, default=True),
                        ParamSpec("auth", "array", "BasicAuth [username, password].", required=False),
                    ],
                    returns="object",
                    returns_description="{status_code, headers, body, body_json, url, elapsed_ms, is_success}",
                    tags=["http", "rest", "put"],
                ),
                ActionSpec(
                    name="http_patch",
                    description="Perform an HTTP PATCH request.",
                    params=[
                        ParamSpec("url", "string", "Target URL."),
                        ParamSpec("headers", "object", "Request headers.", required=False, default={}),
                        ParamSpec("json", "object", "JSON body.", required=False),
                        ParamSpec("data", "object", "Form-encoded body.", required=False),
                        ParamSpec("timeout", "integer", "Timeout in seconds.", required=False, default=30),
                        ParamSpec("verify_ssl", "boolean", "Verify SSL.", required=False, default=True),
                    ],
                    returns="object",
                    returns_description="{status_code, headers, body, body_json, url, elapsed_ms, is_success}",
                    tags=["http", "rest", "patch"],
                ),
                ActionSpec(
                    name="http_delete",
                    description="Perform an HTTP DELETE request.",
                    params=[
                        ParamSpec("url", "string", "Target URL."),
                        ParamSpec("headers", "object", "Request headers.", required=False, default={}),
                        ParamSpec("timeout", "integer", "Timeout in seconds.", required=False, default=30),
                        ParamSpec("verify_ssl", "boolean", "Verify SSL.", required=False, default=True),
                        ParamSpec("json", "object", "Optional JSON body.", required=False),
                    ],
                    returns="object",
                    returns_description="{status_code, headers, body, body_json, url, elapsed_ms, is_success}",
                    tags=["http", "rest", "delete"],
                ),
                # ---- File transfer ----------------------------------------
                ActionSpec(
                    name="download_file",
                    description="Stream-download a file from a URL to a local destination path.",
                    params=[
                        ParamSpec("url", "string", "URL to download from."),
                        ParamSpec("destination", "string", "Local file path to save the download."),
                        ParamSpec("headers", "object", "Request headers.", required=False, default={}),
                        ParamSpec("timeout", "integer", "Timeout in seconds.", required=False, default=300),
                        ParamSpec("verify_ssl", "boolean", "Verify SSL.", required=False, default=True),
                        ParamSpec("chunk_size", "integer", "Download chunk size in bytes.", required=False, default=65536),
                        ParamSpec("auth", "array", "BasicAuth [username, password].", required=False),
                        ParamSpec("overwrite", "boolean", "Overwrite existing file.", required=False, default=True),
                    ],
                    returns="object",
                    returns_description="{bytes_downloaded, destination, elapsed_ms}",
                    tags=["http", "file", "download"],
                ),
                ActionSpec(
                    name="upload_file",
                    description="Upload a local file to a URL as multipart form data.",
                    params=[
                        ParamSpec("url", "string", "URL to upload to."),
                        ParamSpec("file_path", "string", "Path to the local file."),
                        ParamSpec("field_name", "string", "Form field name.", required=False, default="file"),
                        ParamSpec("extra_fields", "object", "Additional form fields.", required=False, default={}),
                        ParamSpec("headers", "object", "Request headers.", required=False, default={}),
                        ParamSpec("timeout", "integer", "Timeout in seconds.", required=False, default=300),
                        ParamSpec("verify_ssl", "boolean", "Verify SSL.", required=False, default=True),
                        ParamSpec("auth", "array", "BasicAuth [username, password].", required=False),
                    ],
                    returns="object",
                    returns_description="{status_code, headers, body, body_json, url, elapsed_ms, is_success}",
                    tags=["http", "file", "upload"],
                ),
                # ---- GraphQL ----------------------------------------
                ActionSpec(
                    name="graphql_query",
                    description="Execute a GraphQL query or mutation via HTTP POST.",
                    params=[
                        ParamSpec("url", "string", "GraphQL endpoint URL."),
                        ParamSpec("query", "string", "GraphQL query or mutation string."),
                        ParamSpec("variables", "object", "GraphQL variables.", required=False, default={}),
                        ParamSpec("operation_name", "string", "Operation name.", required=False),
                        ParamSpec("headers", "object", "HTTP headers.", required=False, default={}),
                        ParamSpec("timeout", "integer", "Timeout in seconds.", required=False, default=30),
                        ParamSpec("verify_ssl", "boolean", "Verify SSL.", required=False, default=True),
                        ParamSpec("auth", "array", "BasicAuth [username, password].", required=False),
                    ],
                    returns="object",
                    returns_description="{data, errors, extensions, status_code, is_success}",
                    tags=["graphql", "api"],
                ),
                # ---- OAuth2 ----------------------------------------
                ActionSpec(
                    name="oauth2_get_token",
                    description="Obtain an OAuth2 access token from a token endpoint.",
                    params=[
                        ParamSpec("token_url", "string", "OAuth2 token endpoint URL."),
                        ParamSpec(
                            "grant_type", "string", "OAuth2 grant type.",
                            enum=["client_credentials", "password", "authorization_code", "refresh_token"],
                            default="client_credentials",
                        ),
                        ParamSpec("client_id", "string", "OAuth2 client ID."),
                        ParamSpec("client_secret", "string", "OAuth2 client secret.", required=False),
                        ParamSpec("username", "string", "Username (for password grant).", required=False),
                        ParamSpec("password", "string", "Password (for password grant).", required=False),
                        ParamSpec("scope", "string", "Requested scope.", required=False),
                    ],
                    returns="object",
                    returns_description="{access_token, token_type, expires_in, scope, refresh_token, raw_response}",
                    tags=["oauth2", "auth", "api"],
                ),
                # ---- HTML parsing ----------------------------------------
                ActionSpec(
                    name="parse_html",
                    description="Parse and extract content from HTML (inline or fetched from URL).",
                    params=[
                        ParamSpec("html", "string", "Raw HTML string to parse.", required=False),
                        ParamSpec("url", "string", "Fetch and parse this URL if html is not provided.", required=False),
                        ParamSpec("selector", "string", "CSS selector to scope extraction.", required=False),
                        ParamSpec(
                            "extract", "string",
                            "What to extract: text | html | attrs | links | images | tables | meta.",
                            required=False, default="text",
                            enum=["text", "html", "attrs", "links", "images", "tables", "meta"],
                        ),
                        ParamSpec("timeout", "integer", "HTTP fetch timeout in seconds.", required=False, default=30),
                    ],
                    returns="object",
                    returns_description="{extract, result}",
                    tags=["html", "scraping", "parsing"],
                ),
                # ---- URL health ----------------------------------------
                ActionSpec(
                    name="check_url_availability",
                    description="Check whether a URL is reachable and return latency.",
                    params=[
                        ParamSpec("url", "string", "URL to check."),
                        ParamSpec("timeout", "integer", "Timeout in seconds.", required=False, default=10),
                        ParamSpec("expected_status", "integer", "Expected HTTP status code.", required=False),
                        ParamSpec("verify_ssl", "boolean", "Verify SSL.", required=False, default=True),
                    ],
                    returns="object",
                    returns_description="{available, status_code, latency_ms, url, error}",
                    tags=["health", "monitoring", "url"],
                ),
                # ---- Email outbound ----------------------------------------
                ActionSpec(
                    name="send_email",
                    description="Send an email via SMTP with optional attachments.",
                    params=[
                        ParamSpec("to", "array", "List of recipient email addresses."),
                        ParamSpec("subject", "string", "Email subject."),
                        ParamSpec("body", "string", "Email body content."),
                        ParamSpec("body_format", "string", "Body format: plain or html.", required=False, default="plain", enum=["plain", "html"]),
                        ParamSpec("cc", "array", "CC recipients.", required=False, default=[]),
                        ParamSpec("bcc", "array", "BCC recipients.", required=False, default=[]),
                        ParamSpec("reply_to", "string", "Reply-To address.", required=False),
                        ParamSpec("attachments", "array", "Paths to files to attach.", required=False, default=[]),
                        ParamSpec("smtp_host", "string", "SMTP server host.", required=False, default="localhost"),
                        ParamSpec("smtp_port", "integer", "SMTP server port.", required=False, default=587),
                        ParamSpec("smtp_user", "string", "SMTP username.", required=False),
                        ParamSpec("smtp_password", "string", "SMTP password.", required=False),
                        ParamSpec("use_tls", "boolean", "Use STARTTLS.", required=False, default=True),
                        ParamSpec("use_ssl", "boolean", "Use SSL (port 465).", required=False, default=False),
                    ],
                    returns="object",
                    returns_description="{sent, message_id, recipients_accepted}",
                    tags=["email", "smtp", "notification"],
                ),
                # ---- Email inbound ----------------------------------------
                ActionSpec(
                    name="read_email",
                    description="Read emails from an IMAP mailbox.",
                    params=[
                        ParamSpec("imap_host", "string", "IMAP server hostname."),
                        ParamSpec("username", "string", "IMAP username."),
                        ParamSpec("password", "string", "IMAP password."),
                        ParamSpec("imap_port", "integer", "IMAP port.", required=False, default=993),
                        ParamSpec("use_ssl", "boolean", "Use SSL.", required=False, default=True),
                        ParamSpec("mailbox", "string", "Mailbox/folder to read.", required=False, default="INBOX"),
                        ParamSpec("max_count", "integer", "Maximum number of messages.", required=False, default=20),
                        ParamSpec("unread_only", "boolean", "Fetch only unread messages.", required=False, default=False),
                        ParamSpec("since_date", "string", "ISO date (YYYY-MM-DD) to filter messages.", required=False),
                        ParamSpec("search_subject", "string", "Filter by subject keyword.", required=False),
                        ParamSpec("search_from", "string", "Filter by sender.", required=False),
                        ParamSpec("download_attachments", "boolean", "Save attachments to disk.", required=False, default=False),
                        ParamSpec("attachment_dir", "string", "Directory to save attachments.", required=False),
                    ],
                    returns="object",
                    returns_description="{messages: [{uid, subject, from, to, date, body_text, body_html, has_attachments}], count}",
                    tags=["email", "imap", "inbox"],
                ),
                # ---- Webhooks ----------------------------------------
                ActionSpec(
                    name="webhook_trigger",
                    description="Send a webhook request with optional HMAC signing and retry logic.",
                    params=[
                        ParamSpec("url", "string", "Webhook endpoint URL."),
                        ParamSpec("method", "string", "HTTP method.", required=False, default="POST", enum=["GET", "POST", "PUT", "PATCH"]),
                        ParamSpec("headers", "object", "Request headers.", required=False, default={}),
                        ParamSpec("payload", "object", "JSON payload.", required=False, default={}),
                        ParamSpec("timeout", "integer", "Timeout in seconds.", required=False, default=10),
                        ParamSpec("verify_ssl", "boolean", "Verify SSL.", required=False, default=True),
                        ParamSpec("retry_on_failure", "boolean", "Retry on non-2xx or error.", required=False, default=False),
                        ParamSpec("max_retries", "integer", "Maximum retry attempts.", required=False, default=3),
                        ParamSpec("retry_delay", "number", "Base retry delay in seconds.", required=False, default=1.0),
                        ParamSpec("hmac_secret", "string", "HMAC-SHA256 secret for request signing.", required=False),
                    ],
                    returns="object",
                    returns_description="{status_code, attempts, success, body}",
                    tags=["webhook", "http", "integration"],
                ),
                # ---- Session management ----------------------------------------
                ActionSpec(
                    name="set_session",
                    description="Create and cache a persistent httpx.AsyncClient session.",
                    params=[
                        ParamSpec("session_id", "string", "Logical session name."),
                        ParamSpec("base_url", "string", "Base URL prefix for all requests.", required=False),
                        ParamSpec("headers", "object", "Default headers.", required=False, default={}),
                        ParamSpec("cookies", "object", "Default cookies.", required=False, default={}),
                        ParamSpec("auth", "array", "BasicAuth [username, password].", required=False),
                        ParamSpec("timeout", "integer", "Default timeout in seconds.", required=False, default=30),
                        ParamSpec("verify_ssl", "boolean", "Verify SSL.", required=False, default=True),
                    ],
                    returns="object",
                    returns_description="{session_id, base_url, created}",
                    tags=["session", "http"],
                ),
                ActionSpec(
                    name="close_session",
                    description="Close and remove a cached httpx session.",
                    params=[
                        ParamSpec("session_id", "string", "Session ID to close."),
                    ],
                    returns="object",
                    returns_description="{session_id, closed}",
                    tags=["session", "http"],
                ),
            ],
        )
