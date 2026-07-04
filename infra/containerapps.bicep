// CRE Copilot — Azure Container Apps deployment (backend + 4 microservices + collector).
// Reuses the EXISTING ADX cluster and Azure OpenAI account (create those with main.bicep).
// Auth is keyless: one user-assigned managed identity holds ACR pull + ADX viewer + AOAI user.
//
// PREREQ (done by deploy_containerapps.sh): ACR exists and the 3 images are pushed:
//   ${acrName}.azurecr.io/cre-service:${imageTag}
//   ${acrName}.azurecr.io/cre-collector:${imageTag}
//   ${acrName}.azurecr.io/cre-backend:${imageTag}

@description('Location for all resources.')
param location string = resourceGroup().location

@description('Short prefix for resource names.')
param prefix string = 'crecopilot'

@description('Name of the pre-created Azure Container Registry (images already pushed).')
param acrName string

@description('Image tag to deploy.')
param imageTag string = 'latest'

@description('Existing ADX cluster name (from main.bicep).')
param adxClusterName string

@description('Existing ADX database name.')
param adxDatabase string = 'CopilotDb'

@description('Existing Azure OpenAI (Cognitive Services) account name.')
param aoaiName string

@description('Azure OpenAI deployment (model) name.')
param aoaiDeployment string = 'gpt-5-mini'

@description('Collector poll interval (seconds).')
param collectorIntervalSec string = '10'

var suffix = toLower(substring(uniqueString(resourceGroup().id), 0, 6))
var miName  = '${prefix}-mi-${suffix}'
var lawName = '${prefix}-calaw-${suffix}'
var envName = '${prefix}-cae-${suffix}'

// ---- existing resources (referenced, not created) ----
resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = { name: acrName }
resource adx 'Microsoft.Kusto/clusters@2023-08-15' existing = { name: adxClusterName }
resource adxDb 'Microsoft.Kusto/clusters/databases@2023-08-15' existing = {
  parent: adx
  name: adxDatabase
}
resource aoai 'Microsoft.CognitiveServices/accounts@2023-05-01' existing = { name: aoaiName }

// ---- shared managed identity ----
resource mi 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: miName
  location: location
}

// ---- RBAC: ACR pull ----
var acrPullRoleId = '7f951dda-4ed3-4680-a7ca-43fe172d538d'
resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, mi.id, acrPullRoleId)
  scope: acr
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleId)
    principalId: mi.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// ---- RBAC: Azure OpenAI user ----
var aoaiUserRoleId = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
resource aoaiRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(aoai.id, mi.id, aoaiUserRoleId)
  scope: aoai
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', aoaiUserRoleId)
    principalId: mi.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// ---- ADX database viewer for the identity ----
resource adxViewer 'Microsoft.Kusto/clusters/databases/principalAssignments@2023-08-15' = {
  parent: adxDb
  name: 'caViewer'
  properties: {
    principalId: mi.properties.clientId
    principalType: 'App'
    role: 'Viewer'
    tenantId: subscription().tenantId
  }
}

// ---- observability + Container Apps environment ----
resource law 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: lawName
  location: location
  properties: { sku: { name: 'PerGB2018' }, retentionInDays: 30 }
}

resource env 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: envName
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: law.properties.customerId
        sharedKey: law.listKeys().primarySharedKey
      }
    }
  }
}

var acrServer = '${acrName}.azurecr.io'
var domain = env.properties.defaultDomain

// Internal DNS the services/backend/collector use to reach each other.
var svcUrls = {
  checkout: 'https://checkout-api.internal.${domain}'
  payment: 'https://payment-service.internal.${domain}'
  inventory: 'https://inventory-service.internal.${domain}'
  auth: 'https://auth-service.internal.${domain}'
}

// ---- the 4 workload microservices (internal ingress) ----
var services = [
  { name: 'payment-service',   module: 'payment_service' }
  { name: 'inventory-service', module: 'inventory_service' }
  { name: 'auth-service',      module: 'auth_service' }
  { name: 'checkout-api',      module: 'checkout_api' }
]

resource svcApps 'Microsoft.App/containerApps@2024-03-01' = [for s in services: {
  name: s.name
  location: location
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${mi.id}': {} } }
  properties: {
    managedEnvironmentId: env.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: { external: false, targetPort: 8000, transport: 'auto' }
      registries: [ { server: acrServer, identity: mi.id } ]
    }
    template: {
      containers: [ {
        name: s.name
        image: '${acrServer}/cre-service:${imageTag}'
        resources: { cpu: json('0.25'), memory: '0.5Gi' }
        env: concat(
          [ { name: 'SERVICE_MODULE', value: s.module } ],
          s.name == 'checkout-api' ? [
            { name: 'PAYMENT_URL',   value: svcUrls.payment }
            { name: 'INVENTORY_URL', value: svcUrls.inventory }
            { name: 'AUTH_URL',      value: svcUrls.auth }
          ] : []
        )
      } ]
      scale: { minReplicas: 1, maxReplicas: 2 }  // min 1 so blast-radius deps are always reachable
    }
  }
  dependsOn: [ acrPull ]
}]

// ---- telemetry collector (no ingress, always-on) ----
resource collectorApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'cre-collector'
  location: location
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${mi.id}': {} } }
  properties: {
    managedEnvironmentId: env.id
    configuration: {
      activeRevisionsMode: 'Single'
      registries: [ { server: acrServer, identity: mi.id } ]
    }
    template: {
      containers: [ {
        name: 'cre-collector'
        image: '${acrServer}/cre-collector:${imageTag}'
        resources: { cpu: json('0.25'), memory: '0.5Gi' }
        env: [
          { name: 'TELEMETRY_SOURCE', value: 'services' }
          { name: 'COLLECTOR_INTERVAL_SEC', value: collectorIntervalSec }
          { name: 'ADX_CLUSTER_URI', value: adx.properties.uri }
          { name: 'ADX_DATABASE', value: adxDatabase }
          { name: 'AZURE_CLIENT_ID', value: mi.properties.clientId }  // pin MI for DefaultAzureCredential
          { name: 'CHECKOUT_URL', value: svcUrls.checkout }
          { name: 'PAYMENT_URL', value: svcUrls.payment }
          { name: 'INVENTORY_URL', value: svcUrls.inventory }
          { name: 'AUTH_URL', value: svcUrls.auth }
        ]
      } ]
      scale: { minReplicas: 1, maxReplicas: 1 }
    }
  }
  dependsOn: [ acrPull, adxViewer ]
}

// ---- backend / console (external ingress) ----
resource backendApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'cre-backend'
  location: location
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${mi.id}': {} } }
  properties: {
    managedEnvironmentId: env.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: { external: true, targetPort: 8000, transport: 'auto' }
      registries: [ { server: acrServer, identity: mi.id } ]
    }
    template: {
      containers: [ {
        name: 'cre-backend'
        image: '${acrServer}/cre-backend:${imageTag}'
        resources: { cpu: json('0.5'), memory: '1.0Gi' }
        env: [
          { name: 'ADX_CLUSTER_URI', value: adx.properties.uri }
          { name: 'ADX_DATABASE', value: adxDatabase }
          { name: 'AZURE_OPENAI_ENDPOINT', value: aoai.properties.endpoint }
          { name: 'AZURE_OPENAI_DEPLOYMENT', value: aoaiDeployment }
          { name: 'AZURE_CLIENT_ID', value: mi.properties.clientId }
          { name: 'TELEMETRY_SOURCE', value: 'services' }
          { name: 'CHECKOUT_URL', value: svcUrls.checkout }
          { name: 'PAYMENT_URL', value: svcUrls.payment }
          { name: 'INVENTORY_URL', value: svcUrls.inventory }
          { name: 'AUTH_URL', value: svcUrls.auth }
          // PUBLIC_BASE_URL + TEAMS_WEBHOOK_URL set post-deploy (backend FQDN is only known after creation).
        ]
      } ]
      scale: { minReplicas: 1, maxReplicas: 2 }
    }
  }
  dependsOn: [ acrPull, aoaiRole, adxViewer ]
}

output backendFqdn string = backendApp.properties.configuration.ingress.fqdn
output managedIdentityClientId string = mi.properties.clientId
output containerAppsEnv string = env.name
