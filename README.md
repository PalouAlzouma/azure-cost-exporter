# Azure Cost Exporter for Prometheus

[🇫🇷 Lire en français](README.fr.md)

A lightweight Prometheus exporter that exposes **real Azure Cost Management
metrics** using **Managed Identity (MSI) authentication** — no secrets,
no API keys, no service principals required.

> There is currently no official Grafana plugin or open source exporter for
> Azure Cost Management in self-hosted environments.
> This project fills that gap.

---

## Screenshots

### Cost dashboard — real Azure costs and daily breakdown

![Cost Dashboard 1](screenshots/dashboard-costs-1.png)

### Cost dashboard — projections and budget alerts

![Cost Dashboard 2](screenshots/dashboard-costs-2.png)

### Alertmanager — budget alerts routing

![Alertmanager](screenshots/alertmanager.png)

---

## Features

| Feature               | Description                                        |
| --------------------- | -------------------------------------------------- |
| Managed Identity auth | No credentials to manage or rotate                 |
| Resource group costs  | Daily and monthly totals per resource group        |
| Subscription costs    | Daily and monthly totals for the full subscription |
| Scrape health metric  | Instant visibility on exporter status              |
| Multi-environment     | `environment` label on all metrics                 |
| Docker Compose        | Ready-to-run with Prometheus and Grafana           |
| Grafana dashboard     | Importable JSON dashboard included                 |

---

## Exposed Metrics

| Metric                                    | Labels                                             | Description                             |
| ----------------------------------------- | -------------------------------------------------- | --------------------------------------- |
| `azure_cost_rg_month_total_eur`           | subscription_id, resource_group, environment       | Monthly cost for a resource group       |
| `azure_cost_rg_daily_eur`                 | subscription_id, resource_group, environment, date | Daily cost for a resource group         |
| `azure_cost_subscription_month_total_eur` | subscription_id                                    | Monthly cost for the subscription       |
| `azure_cost_subscription_daily_eur`       | subscription_id, date                              | Daily cost for the subscription         |
| `azure_cost_scrape_success`               | resource_group, environment                        | 1 if last scrape succeeded, 0 otherwise |
| `azure_cost_scrape_timestamp_seconds`     | resource_group, environment                        | Timestamp of last successful scrape     |

---

## Prerequisites

### 1. Azure VM with Managed Identity

The exporter must run on an Azure VM with a system-assigned Managed Identity.

```bash
# Verify that Managed Identity is enabled on your VM
az vm identity show \
  --resource-group <your-resource-group> \
  --name <your-vm-name>
```

### 2. Cost Management Reader role

Assign the role at the **subscription scope** so the exporter can read costs
for both the resource group and the full subscription.

```bash
# Get the principal ID of the VM Managed Identity
PRINCIPAL_ID=$(az vm show \
  --resource-group <your-resource-group> \
  --name <your-vm-name> \
  --query "identity.principalId" -o tsv)

# Get your subscription ID
SUBSCRIPTION_ID=$(az account show --query "id" -o tsv)

# Assign Cost Management Reader at subscription scope
az role assignment create \
  --assignee "$PRINCIPAL_ID" \
  --role "Cost Management Reader" \
  --scope "/subscriptions/$SUBSCRIPTION_ID"
```

### 3. Microsoft.CostManagement provider

The Cost Management provider must be registered on your subscription.

```bash
az provider register --namespace Microsoft.CostManagement
az provider show \
  --namespace Microsoft.CostManagement \
  --query "registrationState"
# Expected output: Registered
```

> **Note:** Azure Cost Management data has a 24-48 hour delay after resource
> deployment. Metrics may return empty data during the first two days after setup.

---

## Quick Start

### 1. Clone the repository

```bash
git clone https://github.com/<your-username>/azure-cost-exporter.git
cd azure-cost-exporter
```

### 2. Create a .env file

```bash
cat > .env << EOF
SUBSCRIPTION_ID=<your-subscription-id>
RESOURCE_GROUP=<your-resource-group>
ENVIRONMENT=dev
SCRAPE_INTERVAL=300
EOF
```

### 3. Start the stack

```bash
docker compose up -d
```

### 4. Verify metrics are available

```bash
curl -s http://localhost:9101/metrics | grep azure_cost
```

Expected output:

```
azure_cost_rg_month_total_eur{environment="dev",resource_group="my-rg",subscription_id="..."} 12.45
azure_cost_subscription_month_total_eur{subscription_id="..."} 87.96
azure_cost_scrape_success{environment="dev",resource_group="my-rg"} 1.0
```

---

## Grafana Dashboard

A ready-to-import Grafana dashboard is included in this repository.

### Import steps

1. Open Grafana at `http://localhost:3000`
2. Go to **Dashboards** → **Import**
3. Click **Upload JSON file**
4. Select `dashboard-costs.json` from this repository
5. Select your Prometheus datasource
6. Click **Import**

### Dashboard sections

| Section                | Panels                                                                               |
| ---------------------- | ------------------------------------------------------------------------------------ |
| Real Azure Costs       | Monthly cost per RG, Monthly cost subscription, Exporter status                      |
| Daily Costs            | Daily cost bar chart per RG, Daily cost bar chart subscription                       |
| Analysis & Projections | Daily average, End-of-month projection, % of month elapsed, Cumulative vs projection |
| Budget Alerts          | Alert status panels for 50%, 75%, 90% thresholds                                     |

---

## Configuration

| Variable          | Required | Default | Description                                          |
| ----------------- | -------- | ------- | ---------------------------------------------------- |
| `SUBSCRIPTION_ID` | Yes      | —       | Azure subscription ID                                |
| `RESOURCE_GROUP`  | Yes      | —       | Resource group to monitor                            |
| `ENVIRONMENT`     | No       | `""`    | Label added to all metrics (e.g. dev, staging, prod) |
| `SCRAPE_INTERVAL` | No       | `300`   | Scrape interval in seconds                           |

---

## Architecture

```
Azure VM (Managed Identity enabled)
  │
  │  GET http://169.254.169.254/metadata/identity/oauth2/token
  ▼
IMDS — Instance Metadata Service
  │
  │  OAuth2 token (scope: management.azure.com)
  ▼
Azure Cost Management API
  POST /subscriptions/{id}/providers/Microsoft.CostManagement/query
  Timeframe: BillingMonthToDate — Granularity: Daily
  │
  │  Daily cost rows per resource group + subscription
  ▼
azure-cost-exporter :9101/metrics
  │
  ▼
Prometheus scrape (every 5 minutes)
  │
  ▼
Grafana dashboard
```

---

## Example PromQL Queries

```promql
# Monthly cost for a specific resource group
azure_cost_rg_month_total_eur{resource_group="my-rg"}

# Daily average cost this month (resource group)
azure_cost_rg_month_total_eur{resource_group="my-rg"}
  / on(resource_group, environment)
  count by (resource_group, environment) (
    azure_cost_rg_daily_eur{resource_group="my-rg"}
  )

# End-of-month projection
(
  azure_cost_rg_month_total_eur{resource_group="my-rg"}
  / on(resource_group, environment)
  count by (resource_group, environment) (
    azure_cost_rg_daily_eur{resource_group="my-rg"}
  )
) * 30

# Subscription monthly cost
azure_cost_subscription_month_total_eur

# Exporter health check
azure_cost_scrape_success == 0
```

---

## Prometheus Alert Rules (optional)

Add these rules to your Prometheus configuration to trigger alerts
when Azure costs exceed budget thresholds.

```yaml
groups:
  - name: azure_costs
    rules:
      - alert: AzureCostBudget50
        expr: |
          azure_cost_rg_month_total_eur
            / on(resource_group, environment)
          count by (resource_group, environment) (azure_cost_rg_daily_eur)
            * 30 > 50
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "Azure budget 50% reached — {{ $labels.resource_group }}"
          description: "Projected end-of-month cost exceeds 50% of budget."

      - alert: AzureCostBudget75
        expr: |
          azure_cost_rg_month_total_eur
            / on(resource_group, environment)
          count by (resource_group, environment) (azure_cost_rg_daily_eur)
            * 30 > 75
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "Azure budget 75% reached — {{ $labels.resource_group }}"
          description: "Projected end-of-month cost exceeds 75% of budget."

      - alert: AzureCostBudget90
        expr: |
          azure_cost_rg_month_total_eur
            / on(resource_group, environment)
          count by (resource_group, environment) (azure_cost_rg_daily_eur)
            * 30 > 90
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "Azure budget 90% reached — {{ $labels.resource_group }}"
          description: "Projected end-of-month cost exceeds 90% of budget. Immediate action required."
```

---

## Limitations

| Limitation            | Details                                                       |
| --------------------- | ------------------------------------------------------------- |
| Managed Identity only | Does not support service principal authentication             |
| Single resource group | Monitors one resource group per instance                      |
| EUR currency          | Metrics are in EUR (currency returned by Cost Management API) |
| Data delay            | Azure Cost Management has a 24-48h data delay                 |
| Azure only            | Designed specifically for Azure Cost Management API           |

---

## Running multiple instances

To monitor multiple resource groups, run one instance per resource group
with different environment variables:

```yaml
# docker-compose.yml — multiple resource groups
services:
  cost-exporter-dev:
    build: .
    ports:
      - "9101:9101"
    environment:
      SUBSCRIPTION_ID: "${SUBSCRIPTION_ID}"
      RESOURCE_GROUP: "my-rg-dev"
      ENVIRONMENT: "dev"

  cost-exporter-prod:
    build: .
    ports:
      - "9102:9101"
    environment:
      SUBSCRIPTION_ID: "${SUBSCRIPTION_ID}"
      RESOURCE_GROUP: "my-rg-prod"
      ENVIRONMENT: "prod"
```

Then add both targets to Prometheus:

```yaml
scrape_configs:
  - job_name: "azure-cost-exporter"
    scrape_interval: 5m
    scrape_timeout: 30s
    static_configs:
      - targets:
          - "cost-exporter-dev:9101"
          - "cost-exporter-prod:9101"
```

---

## Contributing

Contributions are welcome. Feel free to open an issue or submit a pull request.

Areas where contributions are especially appreciated:

- Support for additional currencies
- Support for user-assigned Managed Identity
- Additional PromQL examples
- Kubernetes deployment manifests (Deployment + ConfigMap)
- Helm chart

---

## License

MIT — see [LICENSE](LICENSE)
