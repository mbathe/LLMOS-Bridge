# API HTTP Module -- Integration Guide

Cross-module workflows and integration patterns for the `api_http` module.

---

## REST API to Database Sync

Fetch data from an external API and store it in a local database.

```yaml
actions:
  - id: connect-db
    module: database
    action: connect
    params:
      driver: sqlite
      database: /data/products.db

  - id: create-table
    module: database
    action: create_table
    params:
      table: products
      columns:
        - { name: id, type: "INTEGER PRIMARY KEY" }
        - { name: name, type: "TEXT NOT NULL" }
        - { name: price, type: "REAL" }
        - { name: category, type: "TEXT" }
    depends_on: [connect-db]

  - id: fetch-products
    module: api_http
    action: http_get
    params:
      url: https://api.store.com/v1/products
      headers: { Authorization: "Bearer {{env.STORE_TOKEN}}" }
      params: { limit: "100" }

  - id: insert-products
    module: database
    action: execute_query
    params:
      sql: "INSERT OR REPLACE INTO products (id, name, price, category) VALUES (?, ?, ?, ?)"
      params: "{{result.fetch-products.body_json}}"
    depends_on: [create-table, fetch-products]
```

---

## OAuth2-Protected API Workflow

Acquire an OAuth2 token, then use it for authenticated API calls.

```yaml
actions:
  - id: get-token
    module: api_http
    action: oauth2_get_token
    params:
      token_url: https://auth.example.com/oauth/token
      grant_type: client_credentials
      client_id: "{{env.CLIENT_ID}}"
      client_secret: "{{env.CLIENT_SECRET}}"
      scope: "read write"

  - id: fetch-data
    module: api_http
    action: http_get
    params:
      url: https://api.example.com/v2/resources
      headers:
        Authorization: "Bearer {{result.get-token.access_token}}"
    depends_on: [get-token]
```

---

## File Download and Processing

Download a file from a URL, then process it with the filesystem module.

```yaml
actions:
  - id: download-csv
    module: api_http
    action: download_file
    params:
      url: https://data.example.com/reports/monthly.csv
      destination: /tmp/monthly_report.csv
      timeout: 120

  - id: read-csv
    module: filesystem
    action: read_file
    params:
      path: /tmp/monthly_report.csv
    depends_on: [download-csv]

  - id: archive-report
    module: filesystem
    action: create_archive
    params:
      source: /tmp/monthly_report.csv
      destination: /data/archives/monthly_report.tar.gz
      format: tar.gz
    depends_on: [download-csv]
```

---

## Webhook Notification Pipeline

Execute an operation, then notify external systems via webhook.

```yaml
actions:
  - id: run-task
    module: os_exec
    action: run_command
    params:
      command: ["/usr/local/bin/deploy.sh", "--env", "staging"]
      timeout: 300

  - id: notify-slack
    module: api_http
    action: webhook_trigger
    params:
      url: https://hooks.slack.com/services/T00/B00/xxxx
      payload:
        text: "Deployment completed: {{result.run-task.exit_code}}"
      retry_on_failure: true
      max_retries: 3
    depends_on: [run-task]

  - id: notify-github
    module: api_http
    action: webhook_trigger
    params:
      url: https://api.github.com/repos/org/repo/dispatches
      headers:
        Authorization: "Bearer {{env.GITHUB_TOKEN}}"
        Accept: application/vnd.github.v3+json
      payload:
        event_type: deployment_complete
      hmac_secret: "{{env.WEBHOOK_SECRET}}"
    depends_on: [run-task]
```

---

## HTML Scraping to Database

Scrape a webpage and store extracted data in a database.

```yaml
actions:
  - id: connect-db
    module: db_gateway
    action: connect
    params:
      driver: sqlite
      database: /data/scraped.db

  - id: scrape-page
    module: api_http
    action: parse_html
    params:
      url: https://news.example.com/latest
      selector: "article.headline"
      extract: text

  - id: store-headlines
    module: db_gateway
    action: create
    params:
      entity: headlines
      data:
        content: "{{result.scrape-page.result}}"
        scraped_at: "{{env.TIMESTAMP}}"
    depends_on: [connect-db, scrape-page]
```

---

## Email Report with Database Data

Query a database and email the results as a report.

```yaml
actions:
  - id: connect-db
    module: database
    action: connect
    params:
      driver: sqlite
      database: /data/analytics.db

  - id: fetch-summary
    module: database
    action: fetch_results
    params:
      sql: "SELECT category, COUNT(*) as count, SUM(revenue) as total FROM sales GROUP BY category ORDER BY total DESC"
    depends_on: [connect-db]

  - id: send-report
    module: api_http
    action: send_email
    params:
      to: ["management@example.com"]
      subject: "Daily Sales Summary"
      body: "Sales report:\n\n{{result.fetch-summary.rows}}"
      smtp_host: smtp.example.com
      smtp_port: 587
      smtp_user: "{{env.SMTP_USER}}"
      smtp_password: "{{env.SMTP_PASS}}"
      use_tls: true
    depends_on: [fetch-summary]
```

---

## Session-Based API Interaction

Create a reusable session for multiple requests to the same API.

```yaml
actions:
  - id: create-session
    module: api_http
    action: set_session
    params:
      session_id: github-api
      base_url: https://api.github.com
      headers:
        Authorization: "Bearer {{env.GITHUB_TOKEN}}"
        Accept: application/vnd.github.v3+json
      timeout: 30

  - id: list-repos
    module: api_http
    action: http_get
    params:
      url: /user/repos
      params: { per_page: "10", sort: "updated" }
    depends_on: [create-session]

  - id: list-issues
    module: api_http
    action: http_get
    params:
      url: /repos/org/project/issues
      params: { state: "open" }
    depends_on: [create-session]

  - id: cleanup
    module: api_http
    action: close_session
    params:
      session_id: github-api
    depends_on: [list-repos, list-issues]
```

---

## Health Check Monitoring

Check availability of multiple services and trigger alerts on failure.

```yaml
actions:
  - id: check-api
    module: api_http
    action: check_url_availability
    params:
      url: https://api.example.com/health
      expected_status: 200
      timeout: 5

  - id: check-web
    module: api_http
    action: check_url_availability
    params:
      url: https://www.example.com
      expected_status: 200
      timeout: 5

  - id: alert-if-down
    module: api_http
    action: webhook_trigger
    params:
      url: https://hooks.pagerduty.com/services/xxxx
      payload:
        summary: "Service health check results"
        api_available: "{{result.check-api.available}}"
        web_available: "{{result.check-web.available}}"
    depends_on: [check-api, check-web]
```
