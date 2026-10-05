targetScope = 'resourceGroup'

@description('Short environment name used in deterministic Azure resource names.')
@minLength(1)
@maxLength(12)
param environmentName string = 'prod'

@description('Azure region for all regional resources.')
param location string = 'eastus2'

@description('Tags applied to provisioned resources.')
param tags object = {
  application: 'auto-soullink'
  environment: environmentName
}

@description('Maximum Azure Files capacity in GiB.')
@minValue(1)
param fileShareQuotaGiB int = 5

var resourceToken = uniqueString(subscription().id, resourceGroup().id, location, environmentName)
var names = {
  virtualNetwork: 'azvnt${resourceToken}'
  subnet: 'azsnt${resourceToken}'
  logAnalytics: 'azlog${resourceToken}'
  storageAccount: 'azst${resourceToken}'
  managedIdentity: 'azid${resourceToken}'
  containerEnvironment: 'azcae${resourceToken}'
}

module network 'modules/network.bicep' = {
  name: 'network'
  params: {
    location: location
    virtualNetworkName: names.virtualNetwork
    subnetName: names.subnet
    tags: tags
  }
}

module observability 'modules/observability.bicep' = {
  name: 'observability'
  params: {
    location: location
    workspaceName: names.logAnalytics
    tags: tags
  }
}

module storage 'modules/storage.bicep' = {
  name: 'storage'
  params: {
    location: location
    storageAccountName: names.storageAccount
    shareName: 'slinkdata'
    shareQuotaGiB: fileShareQuotaGiB
    tags: tags
  }
}

module identity 'modules/identity.bicep' = {
  name: 'identity'
  params: {
    location: location
    identityName: names.managedIdentity
    tags: tags
  }
}

module environment 'modules/environment.bicep' = {
  name: 'container-environment'
  params: {
    location: location
    environmentName: names.containerEnvironment
    infrastructureSubnetId: network.outputs.subnetId
    logAnalyticsWorkspaceName: observability.outputs.workspaceName
    storageAccountName: storage.outputs.storageAccountName
    shareName: storage.outputs.shareName
    environmentStorageName: 'slinkdata'
    tags: tags
  }
}

output containerEnvironmentName string = environment.outputs.environmentName
output containerEnvironmentId string = environment.outputs.environmentId
output containerEnvironmentDefaultDomain string = environment.outputs.defaultDomain
output containerEnvironmentStaticIp string = environment.outputs.staticIp
output managedIdentityId string = identity.outputs.identityId
output storageAccountName string = storage.outputs.storageAccountName
output fileShareName string = storage.outputs.shareName
