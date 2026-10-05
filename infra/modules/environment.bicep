param location string
param environmentName string
param infrastructureSubnetId string
param logAnalyticsWorkspaceName string
param storageAccountName string
param shareName string
param environmentStorageName string
param tags object

resource workspace 'Microsoft.OperationalInsights/workspaces@2025-02-01' existing = {
  name: logAnalyticsWorkspaceName
}

resource storageAccount 'Microsoft.Storage/storageAccounts@2025-01-01' existing = {
  name: storageAccountName
}

resource environment 'Microsoft.App/managedEnvironments@2025-01-01' = {
  name: environmentName
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: workspace.properties.customerId
        sharedKey: workspace.listKeys().primarySharedKey
      }
    }
    vnetConfiguration: {
      infrastructureSubnetId: infrastructureSubnetId
      internal: false
    }
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
    ]
    zoneRedundant: false
  }
}

resource environmentStorage 'Microsoft.App/managedEnvironments/storages@2025-01-01' = {
  parent: environment
  name: environmentStorageName
  properties: {
    azureFile: {
      accessMode: 'ReadWrite'
      accountName: storageAccount.name
      accountKey: storageAccount.listKeys().keys[0].value
      shareName: shareName
    }
  }
}

output environmentName string = environment.name
output environmentId string = environment.id
output defaultDomain string = environment.properties.defaultDomain
output staticIp string = environment.properties.staticIp
output environmentStorageName string = environmentStorage.name
