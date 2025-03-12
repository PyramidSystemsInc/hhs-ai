terraform {
  required_providers {
    azurerm = {
      source = "hashicorp/azurerm"
    }
  }
}
locals {
  system_message = templatefile("./system-prompt.tpl", {})

  # Supported App Service regions with mappings to OpenAI regions
  asp_supported_regions = {
    "eastus2" = {
      location      = "East US 2"
      instances     = 1
      openai_region = "eastus2" # Direct mapping - OpenAI exists here and only supported on built in vectorization for search
    },
  }


  # Generate all ASP configurations
  all_asps = flatten([
    for region, config in local.asp_supported_regions : [
      for i in range(1, config.instances + 1) : {
        name          = "${region}-${i}"
        region        = region
        location      = config.location
        openai_region = config.openai_region
        custom_domain = "www.hhs.ai"
      }
    ]
  ])
  primary_region = [for k, v in var.regions : v if v.primary][0]
  embedding_regions = {
    for k, v in var.regions : k => v if v.supports_embedding
  }
}
provider "azurerm" {
  features {}
  subscription_id = var.subscription_id
}

# Resource group reference
data "azurerm_resource_group" "rg" {
  name = var.resource_group_name
}

# App Service Plans - one per region
resource "azurerm_service_plan" "asp" {
  for_each = { for asp in local.all_asps : asp.name => asp }

  name                = "hhs-${each.key}"
  location            = each.value.location
  resource_group_name = data.azurerm_resource_group.rg.name
  os_type             = "Linux"
  sku_name            = "P1v3" # Adjusted as needed
}

data "azurerm_search_service" "search" {
  name                = "yrci"
  resource_group_name = data.azurerm_resource_group.rg.name
}

data "azurerm_cognitive_account" "openai" {
  for_each = var.regions

  name                = "air-hr-${each.key}"
  resource_group_name = data.azurerm_resource_group.rg.name
}

# App Services - one per region
resource "azurerm_linux_web_app" "app" {

  for_each = { for asp in local.all_asps : asp.name => asp }

  name                    = "hhs-${each.key}"
  location                = each.value.location
  resource_group_name     = data.azurerm_resource_group.rg.name
  service_plan_id         = azurerm_service_plan.asp[each.key].id
  client_affinity_enabled = true

  app_settings = {
    "DEBUG"                               = false
    "OTEL_SERVICE_NAME"                   = "hhs${each.key}"
    OTEL_RESOURCE_ATTRIBUTES              = "service.instance.id=hhs${each.key}"
    # "APPINSIGHTS_PROFILERFEATURE_VERSION" = "disabled"
    # "APPINSIGHTS_SNAPSHOTFEATURE_VERSION" = "disabled"
    "AUTH_CLIENT_SECRET"                  = ""
    "AUTH_ENABLED"                        = "False"
    #"AZURE_COSMOSDB_ACCOUNT"                          = "db-yrci-large"
    #"AZURE_COSMOSDB_CONVERSATIONS_CONTAINER"          = "conversations"
    #"AZURE_COSMOSDB_DATABASE"                         = "db_conversation_history"
    "AZURE_COSMOSDB_MONGO_VCORE_CONNECTION_STRING" = ""
    "AZURE_COSMOSDB_MONGO_VCORE_CONTAINER"         = ""
    "AZURE_COSMOSDB_MONGO_VCORE_CONTENT_COLUMNS"   = ""
    "AZURE_COSMOSDB_MONGO_VCORE_DATABASE"          = ""
    "AZURE_COSMOSDB_MONGO_VCORE_FILENAME_COLUMN"   = ""
    "AZURE_COSMOSDB_MONGO_VCORE_INDEX"             = ""
    "AZURE_COSMOSDB_MONGO_VCORE_TITLE_COLUMN"      = ""
    "AZURE_COSMOSDB_MONGO_VCORE_URL_COLUMN"        = ""
    "AZURE_COSMOSDB_MONGO_VCORE_VECTOR_COLUMNS"    = ""
    "AZURE_OPENAI_EMBEDDING_ENDPOINT"              = ""
    "AZURE_OPENAI_EMBEDDING_KEY"                   = ""
    # "AZURE_OPENAI_EMBEDDING_DEPLOYMENT"               = var.regions[each.value.openai_region].supports_embedding ? azurerm_cognitive_deployment.embedding[each.value.openai_region].name : azurerm_cognitive_deployment.embedding[var.regions[each.value.openai_region].nearest_embedding_region].name
    "AZURE_OPENAI_EMBEDDING_NAME"                     = "text-embedding-3-large"
    "AZURE_OPENAI_ENDPOINT"                           = data.azurerm_cognitive_account.openai[each.value.openai_region].endpoint
    "AZURE_OPENAI_KEY"                                = data.azurerm_cognitive_account.openai[each.value.openai_region].primary_access_key
    "AZURE_OPENAI_MAX_TOKENS"                         = "8096"
    "AZURE_OPENAI_MODEL"                              = "gpt-4o"
    "AZURE_OPENAI_MODEL_NAME"                         = "gpt-4o"
    "AZURE_OPENAI_RESOURCE"                           = data.azurerm_cognitive_account.openai[each.value.openai_region].name
    "AZURE_OPENAI_STOP_SEQUENCE"                      = ""
    "AZURE_OPENAI_SYSTEM_MESSAGE"                     = local.system_message
    "AZURE_OPENAI_TEMPERATURE"                        = "0.7"
    "AZURE_OPENAI_TOP_P"                              = "0.95"
    "AZURE_SEARCH_CONTENT_COLUMNS"                    = "content"
    "AZURE_SEARCH_ENABLE_IN_DOMAIN"                   = "false"
    "AZURE_SEARCH_FILENAME_COLUMN"                    = "hierarchyPath"
    "AZURE_SEARCH_INDEX"                              = "hhs"
    "AZURE_SEARCH_KEY"                                = data.azurerm_search_service.search.primary_key
    "AZURE_SEARCH_PERMITTED_GROUPS_COLUMN"            = ""
    "AZURE_SEARCH_QUERY_TYPE"                         = "vector_semantic_hybrid"
    "AZURE_SEARCH_SEMANTIC_SEARCH_CONFIG"             = "simple-semantic-config"
    "AZURE_SEARCH_SERVICE"                            = data.azurerm_search_service.search.name
    "AZURE_SEARCH_STRICTNESS"                         = "3"
    # "AZURE_SEARCH_TITLE_COLUMN"                       = "partHeading"
    # "AZURE_SEARCH_TOP_K"                              = "50"
    # "AZURE_SEARCH_URL_COLUMN"                         = "partHeading"
    "AZURE_SEARCH_USE_SEMANTIC_SEARCH"                = "true"
    "AZURE_SEARCH_VECTOR_COLUMNS"                     = "vector"
    "ApplicationInsightsAgent_EXTENSION_VERSION"      = "~3"
    "DATASOURCE_TYPE"                                 = "AzureCognitiveSearch"
    "DEBUG"                                           = "True"
    "DiagnosticServices_EXTENSION_VERSION"            = "disabled"
    "ELASTICSEARCH_CONTENT_COLUMNS"                   = ""
    "ELASTICSEARCH_EMBEDDING_MODEL_ID"                = ""
    "ELASTICSEARCH_ENABLE_IN_DOMAIN"                  = "false"
    "ELASTICSEARCH_ENCODED_API_KEY"                   = ""
    "ELASTICSEARCH_ENDPOINT"                          = ""
    "ELASTICSEARCH_FILENAME_COLUMN"                   = ""
    "ELASTICSEARCH_INDEX"                             = ""
    "ELASTICSEARCH_QUERY_TYPE"                        = ""
    "ELASTICSEARCH_STRICTNESS"                        = "3"
    "ELASTICSEARCH_TITLE_COLUMN"                      = ""
    "ELASTICSEARCH_TOP_K"                             = "5"
    "ELASTICSEARCH_URL_COLUMN"                        = ""
    "ELASTICSEARCH_VECTOR_COLUMNS"                    = ""
    "InstrumentationEngine_EXTENSION_VERSION"         = "disabled"
    "MONGODB_APP_NAME"                                = ""
    "MONGODB_COLLECTION_NAME"                         = ""
    "MONGODB_CONTENT_COLUMNS"                         = ""
    "MONGODB_DATABASE_NAME"                           = ""
    "MONGODB_ENABLE_IN_DOMAIN"                        = "false"
    "MONGODB_ENDPOINT"                                = ""
    "MONGODB_FILENAME_COLUMN"                         = ""
    "MONGODB_INDEX_NAME"                              = ""
    "MONGODB_PASSWORD"                                = ""
    "MONGODB_STRICTNESS"                              = "3"
    "MONGODB_TITLE_COLUMN"                            = ""
    "MONGODB_TOP_K"                                   = "5"
    "MONGODB_URL_COLUMN"                              = ""
    "MONGODB_USERNAME"                                = ""
    "MONGODB_VECTOR_COLUMNS"                          = ""
    "SCM_DO_BUILD_DURING_DEPLOYMENT"                  = "true"
    "SnapshotDebugger_EXTENSION_VERSION"              = "disabled"
    "XDT_MicrosoftApplicationInsights_BaseExtensions" = "disabled"
    "XDT_MicrosoftApplicationInsights_Mode"           = "recommended"
    "XDT_MicrosoftApplicationInsights_PreemptSdk"     = "disabled"

  }

  site_config {
    application_stack {
      python_version = "3.11"
    }

    always_on           = true
    minimum_tls_version = "1.2"
    ftps_state          = "FtpsOnly"
    app_command_line    = "python3 -m gunicorn app:app"
    http2_enabled       = false
  }

  auth_settings_v2 {
    auth_enabled             = false
    default_provider         = "azureactivedirectory"
    excluded_paths           = []
    forward_proxy_convention = "NoProxy"
    http_route_api_prefix    = "/.auth"
    require_authentication   = true
    require_https            = true
    runtime_version          = "~1"
    unauthenticated_action   = "RedirectToLoginPage"

    active_directory_v2 {
      allowed_applications            = []
      allowed_audiences               = []
      allowed_groups                  = []
      allowed_identities              = []
      client_id                       = "7f5c56b7-a251-4dc7-95ff-9bdc89d4afb7"
      client_secret_setting_name      = "AUTH_CLIENT_SECRET"
      jwt_allowed_client_applications = []
      jwt_allowed_groups              = []
      login_parameters = {
        "response_type" = "code id_token"
        "scope"         = "openid offline_access profile https://graph.microsoft.com/User.Read"
      }
      tenant_auth_endpoint        = "https://login.microsoftonline.com/bffe4a04-f583-41be-9a3e-0fa4b1c82af3/v2.0"
      www_authentication_disabled = false
    }


    login {
      allowed_external_redirect_urls    = []
      cookie_expiration_convention      = "FixedTime"
      cookie_expiration_time            = "08:00:00"
      nonce_expiration_time             = "00:05:00"
      preserve_url_fragments_for_logins = false
      token_refresh_extension_time      = 72
      token_store_enabled               = true
      validate_nonce                    = true
    }

  }

  logs {
    detailed_error_messages = false
    failed_request_tracing  = false

    http_logs {
      file_system {
        retention_in_days = 5
        retention_in_mb   = 35
      }
    }
  }

  sticky_settings {
    app_setting_names = [
      # "APPINSIGHTS_INSTRUMENTATIONKEY",
      # "APPINSIGHTS_PROFILERFEATURE_VERSION",
      # "APPINSIGHTS_SNAPSHOTFEATURE_VERSION",
      "ApplicationInsightsAgent_EXTENSION_VERSION",
      "DiagnosticServices_EXTENSION_VERSION",
      "InstrumentationEngine_EXTENSION_VERSION",
      "SnapshotDebugger_EXTENSION_VERSION",
      "XDT_MicrosoftApplicationInsights_BaseExtensions",
      "XDT_MicrosoftApplicationInsights_Mode",
      "XDT_MicrosoftApplicationInsights_PreemptSdk",
      "APPLICATIONINSIGHTS_CONNECTION_STRING ",
      "APPLICATIONINSIGHTS_CONFIGURATION_CONTENT",
      "XDT_MicrosoftApplicationInsightsJava",
      "XDT_MicrosoftApplicationInsights_NodeJS",
    ]
  }

  identity {
    type = "SystemAssigned"
  }

  https_only = true

}
