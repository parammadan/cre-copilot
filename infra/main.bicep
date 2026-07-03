// CRE Copilot — Phase 0 foundations
// Provisions: Log Analytics + App Insights, Storage, Key Vault (RBAC mode),
// ADX cluster + database, and — behind the `deployFunctions` flag — the
// Python Function App with its managed identity + RBAC.
//
// New subscriptions ship with 0 App Service compute quota, so `deployFunctions`
// defaults to FALSE: we stand up the data + observability plane now, and flip
// the flag once the Functions quota request is granted (or run Functions locally).
//
// Deploy at RESOURCE GROUP scope (see deploy.sh).

@description('Location for all resources. Defaults to the resource group location.')
param location string = resourceGroup().location

@description('Short prefix for resource names.')
param prefix string = 'crecopilot'

@description('Name of the ADX database the agents query.')
param adxDatabaseName string = 'CopilotDb'

@description('Deploy the Function App tier. Requires App Service compute quota > 0.')
param deployFunctions bool = false

// A stable, globally-unique suffix derived from the RG id (safe for storage/kv/adx names).
var suffix = toLower(substring(uniqueString(resourceGroup().id), 0, 6))

// Names (storage/kv/adx have tight global-uniqueness + length rules).
var storageName  = '${prefix}st${suffix}'          // <=24 chars, lowercase alnum
var kvName       = '${prefix}-kv-${suffix}'
var adxName      = '${prefix}adx${suffix}'          // ADX: lowercase alnum, <=22
var funcAppName  = '${prefix}-func-${suffix}'
var planName     = '${prefix}-plan-${suffix}'
var lawName      = '${prefix}-law-${suffix}'
var aiName       = '${prefix}-ai-${suffix}'

// The identity running this deployment (you). Gets ADX admin + Key Vault access
// so Phases 1-4 can create tables, ingest data, and manage secrets.
var deployerId = deployer().objectId

// ---------------------------------------------------------------------------
// Observability: Log Analytics + Application Insights (the SRE story starts here)
// ---------------------------------------------------------------------------
resource law 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: lawName
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: aiName
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: law.id
  }
}

// ---------------------------------------------------------------------------
// Storage (required by the Functions runtime — kept ready for when we flip the flag)
// ---------------------------------------------------------------------------
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageName
  location: location
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
  }
}

// ---------------------------------------------------------------------------
// Key Vault — RBAC mode (no access policies), zero secrets in code
// ---------------------------------------------------------------------------
resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: kvName
  location: location
  properties: {
    sku: { family: 'A', name: 'standard' }
    tenantId: subscription().tenantId
    enableRbacAuthorization: true      // RBAC, not legacy access policies
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
  }
}

// ---------------------------------------------------------------------------
// Azure Data Explorer (Kusto) — Dev SKU. STOP when idle to save cost.
// ---------------------------------------------------------------------------
resource adx 'Microsoft.Kusto/clusters@2023-08-15' = {
  name: adxName
  location: location
  sku: {
    name: 'Dev(No SLA)_Standard_E2a_v4'
    tier: 'Basic'
    capacity: 1
  }
  identity: { type: 'SystemAssigned' }
}

resource adxDb 'Microsoft.Kusto/clusters/databases@2023-08-15' = {
  parent: adx
  name: adxDatabaseName
  location: location
  kind: 'ReadWrite'
  properties: {
    softDeletePeriod: 'P7D'
    hotCachePeriod: 'P7D'
  }
}

// You -> ADX database Admin (needed to create tables + ingest telemetry in Phase 1)
resource adxDeployerAdmin 'Microsoft.Kusto/clusters/databases/principalAssignments@2023-08-15' = {
  parent: adxDb
  name: 'deployerAdmin'
  properties: {
    principalId: deployerId
    principalType: 'User'
    role: 'Admin'
    tenantId: subscription().tenantId
  }
}

// ---------------------------------------------------------------------------
// RBAC — Key Vault for the deployer (manage secrets in later phases)
// ---------------------------------------------------------------------------
var kvSecretsOfficerRoleId = 'b86a8fe4-44ce-4948-aee7-ecc7f6a0f2b0'
resource kvDeployerRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, deployerId, kvSecretsOfficerRoleId)
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', kvSecretsOfficerRoleId)
    principalId: deployerId
    principalType: 'User'
  }
}

// ===========================================================================
// Function App tier — gated behind `deployFunctions` (needs compute quota > 0)
// ===========================================================================
resource plan 'Microsoft.Web/serverfarms@2023-12-01' = if (deployFunctions) {
  name: planName
  location: location
  sku: { name: 'Y1', tier: 'Dynamic' }
  kind: 'linux'
  properties: { reserved: true }
}

resource functionApp 'Microsoft.Web/sites@2023-12-01' = if (deployFunctions) {
  name: funcAppName
  location: location
  kind: 'functionapp,linux'
  identity: { type: 'SystemAssigned' }   // <-- the identity everything else trusts
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'Python|3.11'
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      appSettings: [
        { name: 'FUNCTIONS_EXTENSION_VERSION', value: '~4' }
        { name: 'FUNCTIONS_WORKER_RUNTIME', value: 'python' }
        { name: 'AzureWebJobsStorage', value: 'DefaultEndpointsProtocol=https;AccountName=${storage.name};EndpointSuffix=${environment().suffixes.storage};AccountKey=${storage.listKeys().keys[0].value}' }
        { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsights.properties.ConnectionString }
        { name: 'ADX_CLUSTER_URI', value: adx.properties.uri }
        { name: 'ADX_DATABASE', value: adxDatabaseName }
        { name: 'KEY_VAULT_URI', value: keyVault.properties.vaultUri }
      ]
    }
  }
}

// Function MI -> Key Vault "Key Vault Secrets User"
var kvSecretsUserRoleId = '4633458b-17de-408a-b874-0445c86b69e6'
resource kvFuncRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (deployFunctions) {
  name: guid(keyVault.id, funcAppName, kvSecretsUserRoleId)
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', kvSecretsUserRoleId)
    principalId: functionApp!.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// Function MI -> ADX database "Viewer"
resource adxFuncViewer 'Microsoft.Kusto/clusters/databases/principalAssignments@2023-08-15' = if (deployFunctions) {
  parent: adxDb
  name: 'funcViewer'
  properties: {
    principalId: functionApp!.identity.principalId
    principalType: 'App'
    role: 'Viewer'
    tenantId: subscription().tenantId
  }
}

// ---------------------------------------------------------------------------
// Outputs (used by later phases + deploy.sh)
// ---------------------------------------------------------------------------
output adxClusterName string = adx.name
output adxClusterUri string = adx.properties.uri
output adxDatabase string = adxDatabaseName
output keyVaultName string = keyVault.name
output keyVaultUri string = keyVault.properties.vaultUri
output functionAppName string = deployFunctions ? functionApp.name : '(not deployed — deployFunctions=false)'
