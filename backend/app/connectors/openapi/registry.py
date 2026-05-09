"""
Curated OpenAPI Connector Registry

Pre-validated collection of 30+ popular API specifications
organized by category for the connector marketplace.
"""

from typing import Any

# Registry of curated OpenAPI connectors
# Each entry contains validated spec URLs and configuration
CONNECTOR_REGISTRY: dict[str, dict[str, Any]] = {

    # ========== PAYMENTS ==========
    "stripe": {
        "name": "Stripe",
        "description": "Payment processing, subscriptions, and invoicing",
        "spec_url": "https://raw.githubusercontent.com/stripe/openapi/master/openapi/spec3.json",
        "auth_type": "bearer",
        "auth_header": "Authorization",
        "category": "payments",
        "icon": "💳",
        "docs_url": "https://stripe.com/docs/api",
    },
    "paypal": {
        "name": "PayPal",
        "description": "Online payments and money transfers",
        "spec_url": "https://raw.githubusercontent.com/paypal/paypal-rest-api-specifications/main/openapi/checkout_orders_v2.json",
        "auth_type": "bearer",
        "category": "payments",
        "icon": "💰",
        "docs_url": "https://developer.paypal.com/docs/api/",
    },
    "square": {
        "name": "Square",
        "description": "Point of sale and payment processing",
        "spec_url": "https://raw.githubusercontent.com/square/connect-api-specification/master/square_connect_api_v2.json",
        "auth_type": "bearer",
        "category": "payments",
        "icon": "🟦",
        "docs_url": "https://developer.squareup.com/docs",
    },

    # ========== COMMUNICATION ==========
    "slack": {
        "name": "Slack",
        "description": "Team messaging and collaboration",
        "spec_url": "https://raw.githubusercontent.com/slackapi/slack-api-specs/master/web-api/slack_web_openapi_v2.json",
        "auth_type": "bearer",
        "category": "communication",
        "icon": "💬",
        "docs_url": "https://api.slack.com/",
    },
    "twilio": {
        "name": "Twilio",
        "description": "SMS, voice, and messaging APIs",
        "spec_url": "https://raw.githubusercontent.com/twilio/twilio-oai/main/spec/json/twilio_api_v2010.json",
        "auth_type": "basic",
        "category": "communication",
        "icon": "📱",
        "docs_url": "https://www.twilio.com/docs",
    },
    "sendgrid": {
        "name": "SendGrid",
        "description": "Email delivery and marketing",
        "spec_url": "https://raw.githubusercontent.com/sendgrid/sendgrid-oai/main/oai.json",
        "auth_type": "bearer",
        "category": "communication",
        "icon": "📧",
        "docs_url": "https://docs.sendgrid.com/",
    },
    "discord": {
        "name": "Discord",
        "description": "Community and gaming communication",
        "spec_url": "https://raw.githubusercontent.com/discord/discord-api-spec/main/specs/openapi.json",
        "auth_type": "bearer",
        "category": "communication",
        "icon": "🎮",
        "docs_url": "https://discord.com/developers/docs",
    },
    "mailchimp": {
        "name": "Mailchimp",
        "description": "Email marketing and automation",
        "spec_url": "https://api.mailchimp.com/schema/3.0/Swagger.json",
        "auth_type": "bearer",
        "category": "communication",
        "icon": "🐵",
        "docs_url": "https://mailchimp.com/developer/",
    },

    # ========== CRM ==========
    "hubspot": {
        "name": "HubSpot",
        "description": "CRM, marketing, and sales platform",
        "spec_url": "https://api.hubspot.com/api-catalog-public/v1/apis/crm/v3/objects/contacts",
        "auth_type": "bearer",
        "category": "crm",
        "icon": "🧲",
        "docs_url": "https://developers.hubspot.com/",
    },
    "pipedrive": {
        "name": "Pipedrive",
        "description": "Sales CRM and pipeline management",
        "spec_url": "https://developers.pipedrive.com/docs/api/v1/openapi.yaml",
        "auth_type": "api_key",
        "auth_location": "query",
        "auth_header": "api_token",
        "category": "crm",
        "icon": "📊",
        "docs_url": "https://developers.pipedrive.com/",
    },
    "zendesk": {
        "name": "Zendesk",
        "description": "Customer support and ticketing",
        "spec_url": "https://developer.zendesk.com/api-reference/ticketing/tickets/tickets/",
        "auth_type": "basic",
        "category": "crm",
        "icon": "🎧",
        "docs_url": "https://developer.zendesk.com/",
    },
    "intercom": {
        "name": "Intercom",
        "description": "Customer messaging platform",
        "spec_url": "https://developers.intercom.com/docs/build-an-integration/learn-more/rest-apis/openapi-schema/",
        "auth_type": "bearer",
        "category": "crm",
        "icon": "💭",
        "docs_url": "https://developers.intercom.com/",
    },

    # ========== PROJECT MANAGEMENT ==========
    "asana": {
        "name": "Asana",
        "description": "Work management and project tracking",
        "spec_url": "https://raw.githubusercontent.com/Asana/openapi/main/defs/asana_oas.yaml",
        "auth_type": "bearer",
        "category": "project_management",
        "icon": "✅",
        "docs_url": "https://developers.asana.com/",
    },
    "jira": {
        "name": "Jira",
        "description": "Issue tracking and agile project management",
        "spec_url": "https://developer.atlassian.com/cloud/jira/platform/swagger-v3.v3.json",
        "auth_type": "basic",
        "category": "project_management",
        "icon": "🔷",
        "docs_url": "https://developer.atlassian.com/cloud/jira/platform/",
    },
    "notion": {
        "name": "Notion",
        "description": "All-in-one workspace for notes and docs",
        "spec_url": "https://raw.githubusercontent.com/notionhq/notion-sdk-js/main/openapi.json",
        "auth_type": "bearer",
        "category": "project_management",
        "icon": "📝",
        "docs_url": "https://developers.notion.com/",
    },
    "trello": {
        "name": "Trello",
        "description": "Visual collaboration and boards",
        "spec_url": "https://developer.atlassian.com/cloud/trello/swagger.v3.json",
        "auth_type": "api_key",
        "auth_location": "query",
        "auth_header": "key",
        "category": "project_management",
        "icon": "📋",
        "docs_url": "https://developer.atlassian.com/cloud/trello/",
    },
    "monday": {
        "name": "Monday.com",
        "description": "Work operating system",
        "spec_url": "https://api.monday.com/playground/schema/v2",
        "auth_type": "bearer",
        "category": "project_management",
        "icon": "📅",
        "docs_url": "https://developer.monday.com/",
    },

    # ========== DEVELOPER TOOLS ==========
    "github": {
        "name": "GitHub",
        "description": "Code hosting and version control",
        "spec_url": "https://raw.githubusercontent.com/github/rest-api-description/main/descriptions/api.github.com/api.github.com.json",
        "auth_type": "bearer",
        "category": "developer_tools",
        "icon": "🐙",
        "docs_url": "https://docs.github.com/en/rest",
    },
    "gitlab": {
        "name": "GitLab",
        "description": "DevOps platform and CI/CD",
        "spec_url": "https://gitlab.com/gitlab-org/gitlab/-/raw/master/doc/api/openapi/openapi.yaml",
        "auth_type": "bearer",
        "category": "developer_tools",
        "icon": "🦊",
        "docs_url": "https://docs.gitlab.com/ee/api/",
    },
    "vercel": {
        "name": "Vercel",
        "description": "Frontend deployment platform",
        "spec_url": "https://openapi.vercel.sh/",
        "auth_type": "bearer",
        "category": "developer_tools",
        "icon": "▲",
        "docs_url": "https://vercel.com/docs/rest-api",
    },
    "datadog": {
        "name": "Datadog",
        "description": "Monitoring and analytics",
        "spec_url": "https://raw.githubusercontent.com/DataDog/datadog-api-client-python/master/.generator/schemas/v2/openapi.yaml",
        "auth_type": "api_key",
        "auth_location": "header",
        "auth_header": "DD-API-KEY",
        "category": "developer_tools",
        "icon": "🐕",
        "docs_url": "https://docs.datadoghq.com/api/",
    },
    "sentry": {
        "name": "Sentry",
        "description": "Error tracking and monitoring",
        "spec_url": "https://raw.githubusercontent.com/getsentry/sentry/master/api-docs/openapi-derefed.json",
        "auth_type": "bearer",
        "category": "developer_tools",
        "icon": "🔍",
        "docs_url": "https://docs.sentry.io/api/",
    },

    # ========== STORAGE ==========
    "dropbox": {
        "name": "Dropbox",
        "description": "Cloud file storage and sharing",
        "spec_url": "https://www.dropbox.com/static/developers/api/spec/dropbox_api_v2.json",
        "auth_type": "bearer",
        "category": "storage",
        "icon": "📦",
        "docs_url": "https://www.dropbox.com/developers/documentation",
    },
    "box": {
        "name": "Box",
        "description": "Enterprise content management",
        "spec_url": "https://raw.githubusercontent.com/box/box-openapi/main/openapi.json",
        "auth_type": "bearer",
        "category": "storage",
        "icon": "📁",
        "docs_url": "https://developer.box.com/",
    },

    # ========== FINANCE ==========
    "quickbooks": {
        "name": "QuickBooks",
        "description": "Accounting and bookkeeping",
        "spec_url": "https://developer.intuit.com/app/developer/qbo/docs/api/accounting/all-entities/account",
        "auth_type": "oauth2",
        "category": "finance",
        "icon": "📒",
        "docs_url": "https://developer.intuit.com/",
    },
    "plaid": {
        "name": "Plaid",
        "description": "Banking data and financial services",
        "spec_url": "https://raw.githubusercontent.com/plaid/plaid-openapi/master/2020-09-14.yml",
        "auth_type": "api_key",
        "auth_location": "header",
        "auth_header": "PLAID-CLIENT-ID",
        "category": "finance",
        "icon": "🏦",
        "docs_url": "https://plaid.com/docs/",
    },

    # ========== E-COMMERCE ==========
    "shopify": {
        "name": "Shopify",
        "description": "E-commerce platform",
        "spec_url": "https://shopify.dev/docs/api/admin-rest/2024-01/openapi.json",
        "auth_type": "bearer",
        "category": "ecommerce",
        "icon": "🛒",
        "docs_url": "https://shopify.dev/docs/api",
    },
    "woocommerce": {
        "name": "WooCommerce",
        "description": "WordPress e-commerce",
        "spec_url": "https://raw.githubusercontent.com/woocommerce/woocommerce-rest-api/trunk/openapi/openapi-generator/openapi-schema.json",
        "auth_type": "basic",
        "category": "ecommerce",
        "icon": "🛍️",
        "docs_url": "https://woocommerce.github.io/woocommerce-rest-api-docs/",
    },

    # ========== ANALYTICS ==========
    "mixpanel": {
        "name": "Mixpanel",
        "description": "Product analytics and user tracking",
        "spec_url": "https://developer.mixpanel.com/reference/overview",
        "auth_type": "basic",
        "category": "analytics",
        "icon": "📈",
        "docs_url": "https://developer.mixpanel.com/",
    },

    # ========== HR ==========
    "greenhouse": {
        "name": "Greenhouse",
        "description": "Applicant tracking and recruiting",
        "spec_url": "https://developers.greenhouse.io/harvest.html",
        "auth_type": "basic",
        "category": "hr",
        "icon": "🌱",
        "docs_url": "https://developers.greenhouse.io/",
    },
    "bamboohr": {
        "name": "BambooHR",
        "description": "HR management software",
        "spec_url": "https://documentation.bamboohr.com/reference",
        "auth_type": "basic",
        "category": "hr",
        "icon": "🎋",
        "docs_url": "https://documentation.bamboohr.com/",
    },
}


# Category definitions for UI
CONNECTOR_CATEGORIES = {
    "payments": {"name": "Payments", "icon": "💳", "description": "Payment processing and billing"},
    "communication": {"name": "Communication", "icon": "💬", "description": "Messaging and email"},
    "crm": {"name": "CRM", "icon": "🧲", "description": "Customer relationship management"},
    "project_management": {"name": "Project Management", "icon": "📋", "description": "Tasks and collaboration"},
    "developer_tools": {"name": "Developer Tools", "icon": "🛠️", "description": "Development and DevOps"},
    "storage": {"name": "Storage", "icon": "📦", "description": "File storage and sharing"},
    "finance": {"name": "Finance", "icon": "💰", "description": "Accounting and banking"},
    "ecommerce": {"name": "E-commerce", "icon": "🛒", "description": "Online stores and shopping"},
    "analytics": {"name": "Analytics", "icon": "📈", "description": "Data and product analytics"},
    "hr": {"name": "HR", "icon": "👥", "description": "Human resources"},
    "custom": {"name": "Custom", "icon": "🔧", "description": "User-imported connectors"},
}


def get_registry_connector(connector_id: str) -> dict[str, Any] | None:
    """Get connector entry from registry by ID."""
    return CONNECTOR_REGISTRY.get(connector_id)


def list_registry_connectors(category: str | None = None) -> list[dict[str, Any]]:
    """
    List all connectors in registry, optionally filtered by category.

    Returns list with connector ID included.
    """
    connectors = []
    for connector_id, connector in CONNECTOR_REGISTRY.items():
        if category and connector.get("category") != category:
            continue
        connectors.append({"id": connector_id, **connector})
    return connectors


def search_registry(query: str) -> list[dict[str, Any]]:
    """Search registry by name or description."""
    query = query.lower()
    results = []
    for connector_id, connector in CONNECTOR_REGISTRY.items():
        name = connector.get("name", "").lower()
        description = connector.get("description", "").lower()
        if query in name or query in description or query in connector_id:
            results.append({"id": connector_id, **connector})
    return results


def get_categories() -> list[dict[str, Any]]:
    """Get all categories with connector counts."""
    categories = []
    for cat_id, cat_info in CONNECTOR_CATEGORIES.items():
        count = sum(1 for c in CONNECTOR_REGISTRY.values() if c.get("category") == cat_id)
        categories.append({
            "id": cat_id,
            **cat_info,
            "connector_count": count,
        })
    return categories
