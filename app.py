# ==============================================================================
# Azure Cost Exporter for Prometheus
#
# A lightweight Prometheus exporter that exposes Azure Cost Management metrics
# using Managed Identity (MSI) authentication — no secrets required.
#
# Metrics exposed on :9101/metrics :
#   azure_cost_rg_month_total_eur       Monthly cost per resource group
#   azure_cost_rg_daily_eur             Daily cost per resource group
#   azure_cost_subscription_month_total_eur  Monthly cost for the subscription
#   azure_cost_subscription_daily_eur   Daily cost for the subscription
#   azure_cost_scrape_success           1 if last scrape succeeded, 0 otherwise
#   azure_cost_scrape_timestamp_seconds Timestamp of last successful scrape
#
# Requirements :
#   - Azure VM with a system-assigned Managed Identity
#   - Managed Identity must have the "Cost Management Reader" role
#     assigned at the subscription scope
#
# Environment variables :
#   SUBSCRIPTION_ID   Azure subscription ID (required)
#   RESOURCE_GROUP    Resource group name to monitor (required)
#   ENVIRONMENT       Environment label for metrics (e.g. dev, staging, prod)
#   SCRAPE_INTERVAL   Scrape interval in seconds (default: 300)
#
# Author : Palou
# License : MIT
# ==============================================================================

import os
import time
import logging
import requests
from prometheus_client import (
    start_http_server,
    Gauge,
    REGISTRY,
    PROCESS_COLLECTOR,
    PLATFORM_COLLECTOR,
)

# ------------------------------------------------------------------------------
# Logging configuration
# ------------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

# ------------------------------------------------------------------------------
# Configuration — loaded from environment variables
# ------------------------------------------------------------------------------

SUBSCRIPTION_ID = os.getenv("SUBSCRIPTION_ID", "")
RESOURCE_GROUP  = os.getenv("RESOURCE_GROUP", "")
ENVIRONMENT     = os.getenv("ENVIRONMENT", "")
SCRAPE_INTERVAL = int(os.getenv("SCRAPE_INTERVAL", "300"))

# Remove default process and platform collectors — keep metrics clean
REGISTRY.unregister(PROCESS_COLLECTOR)
REGISTRY.unregister(PLATFORM_COLLECTOR)

# ------------------------------------------------------------------------------
# Prometheus metrics definition
# ------------------------------------------------------------------------------

cost_rg_month_total = Gauge(
    "azure_cost_rg_month_total_eur",
    "Total Azure cost for the current month by resource group (EUR)",
    ["subscription_id", "resource_group", "environment"]
)

cost_rg_daily = Gauge(
    "azure_cost_rg_daily_eur",
    "Daily Azure cost by resource group (EUR)",
    ["subscription_id", "resource_group", "environment", "date"]
)

cost_sub_month_total = Gauge(
    "azure_cost_subscription_month_total_eur",
    "Total Azure cost for the current month for the entire subscription (EUR)",
    ["subscription_id"]
)

cost_sub_daily = Gauge(
    "azure_cost_subscription_daily_eur",
    "Daily Azure cost for the entire subscription (EUR)",
    ["subscription_id", "date"]
)

cost_scrape_success = Gauge(
    "azure_cost_scrape_success",
    "1 if the last Cost Management scrape succeeded, 0 otherwise",
    ["resource_group", "environment"]
)

cost_scrape_timestamp = Gauge(
    "azure_cost_scrape_timestamp_seconds",
    "Unix timestamp of the last successful Cost Management scrape",
    ["resource_group", "environment"]
)


# ------------------------------------------------------------------------------
# Azure Managed Identity authentication
# The IMDS endpoint (169.254.169.254) is available on all Azure VMs.
# No credentials are required — the VM identity is used automatically.
# ------------------------------------------------------------------------------

def get_msi_token() -> str:
    """
    Retrieve an OAuth2 access token from the Azure Instance Metadata Service (IMDS).
    The token is scoped to the Azure Resource Manager API.
    Requires a system-assigned or user-assigned Managed Identity on the VM.
    """
    url = (
        "http://169.254.169.254/metadata/identity/oauth2/token"
        "?api-version=2018-02-01"
        "&resource=https://management.azure.com/"
    )
    response = requests.get(url, headers={"Metadata": "true"}, timeout=10)
    response.raise_for_status()
    return response.json()["access_token"]


# ------------------------------------------------------------------------------
# Azure Cost Management API calls
# Uses the BillingMonthToDate timeframe with Daily granularity.
# ------------------------------------------------------------------------------

def fetch_costs_by_resource_group(token: str) -> dict:
    """
    Query the Azure Cost Management API for daily costs filtered
    to a specific resource group, for the current billing month.
    """
    url = (
        f"https://management.azure.com/subscriptions/{SUBSCRIPTION_ID}"
        f"/providers/Microsoft.CostManagement/query"
        f"?api-version=2023-03-01"
    )
    payload = {
        "type": "ActualCost",
        "timeframe": "BillingMonthToDate",
        "dataset": {
            "granularity": "Daily",
            "aggregation": {
                "totalCost": {"name": "Cost", "function": "Sum"}
            },
            "filter": {
                "dimensions": {
                    "name": "ResourceGroupName",
                    "operator": "In",
                    "values": [RESOURCE_GROUP]
                }
            }
        }
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    response = requests.post(url, json=payload, headers=headers, timeout=30)
    response.raise_for_status()
    return response.json()


def fetch_costs_by_subscription(token: str) -> dict:
    """
    Query the Azure Cost Management API for daily costs
    for the entire subscription, for the current billing month.
    """
    url = (
        f"https://management.azure.com/subscriptions/{SUBSCRIPTION_ID}"
        f"/providers/Microsoft.CostManagement/query"
        f"?api-version=2023-03-01"
    )
    payload = {
        "type": "ActualCost",
        "timeframe": "BillingMonthToDate",
        "dataset": {
            "granularity": "Daily",
            "aggregation": {
                "totalCost": {"name": "Cost", "function": "Sum"}
            }
        }
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    response = requests.post(url, json=payload, headers=headers, timeout=30)
    response.raise_for_status()
    return response.json()


# ------------------------------------------------------------------------------
# Metrics update loop
# ------------------------------------------------------------------------------

def update_metrics() -> None:
    """
    Fetch costs from Azure Cost Management API and update all Prometheus metrics.
    Sets azure_cost_scrape_success to 0 on any failure.

    Note : Azure Cost Management data has a 24-48h delay after resource deployment.
    Metrics may return empty data during the first two days after setup.
    """
    if not SUBSCRIPTION_ID:
        logger.error("SUBSCRIPTION_ID is not set")
        cost_scrape_success.labels(
            resource_group=RESOURCE_GROUP,
            environment=ENVIRONMENT
        ).set(0)
        return

    if not RESOURCE_GROUP:
        logger.error("RESOURCE_GROUP is not set")
        cost_scrape_success.labels(
            resource_group=RESOURCE_GROUP,
            environment=ENVIRONMENT
        ).set(0)
        return

    try:
        token = get_msi_token()
        logger.info("MSI token retrieved successfully")

        # --- Resource group costs ---
        data_rg = fetch_costs_by_resource_group(token)
        rows_rg = data_rg.get("properties", {}).get("rows", [])
        logger.info(
            "%d days of cost data retrieved for resource group %s",
            len(rows_rg), RESOURCE_GROUP
        )

        total_rg = 0.0
        for row in rows_rg:
            cost = float(row[0])
            date = str(row[1])
            total_rg += cost
            cost_rg_daily.labels(
                subscription_id=SUBSCRIPTION_ID,
                resource_group=RESOURCE_GROUP,
                environment=ENVIRONMENT,
                date=date
            ).set(cost)

        cost_rg_month_total.labels(
            subscription_id=SUBSCRIPTION_ID,
            resource_group=RESOURCE_GROUP,
            environment=ENVIRONMENT
        ).set(total_rg)
        logger.info("Monthly cost (%s) : %.2f EUR", RESOURCE_GROUP, total_rg)

        # --- Subscription costs ---
        data_sub = fetch_costs_by_subscription(token)
        rows_sub = data_sub.get("properties", {}).get("rows", [])
        logger.info(
            "%d days of cost data retrieved for subscription",
            len(rows_sub)
        )

        total_sub = 0.0
        for row in rows_sub:
            cost = float(row[0])
            date = str(row[1])
            total_sub += cost
            cost_sub_daily.labels(
                subscription_id=SUBSCRIPTION_ID,
                date=date
            ).set(cost)

        cost_sub_month_total.labels(
            subscription_id=SUBSCRIPTION_ID
        ).set(total_sub)
        logger.info("Monthly cost (subscription) : %.2f EUR", total_sub)

        cost_scrape_success.labels(
            resource_group=RESOURCE_GROUP,
            environment=ENVIRONMENT
        ).set(1)

        cost_scrape_timestamp.labels(
            resource_group=RESOURCE_GROUP,
            environment=ENVIRONMENT
        ).set(time.time())

    except Exception as e:
        logger.error("Cost Management scrape failed : %s", str(e))
        cost_scrape_success.labels(
            resource_group=RESOURCE_GROUP,
            environment=ENVIRONMENT
        ).set(0)


# ------------------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------------------

if __name__ == "__main__":
    logger.info("Azure Cost Exporter starting")
    logger.info("Subscription   : %s", SUBSCRIPTION_ID)
    logger.info("Resource Group : %s", RESOURCE_GROUP)
    logger.info("Environment    : %s", ENVIRONMENT)
    logger.info("Metrics port   : 9101")
    logger.info("Scrape interval: %ds", SCRAPE_INTERVAL)

    start_http_server(9101)
    logger.info("HTTP server started on :9101/metrics")

    update_metrics()

    while True:
        time.sleep(SCRAPE_INTERVAL)
        update_metrics()
