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

@description('Public GHCR image tag or digest to deploy.')
param containerImage string = 'ghcr.io/matracey/auto-soullink:latest'

@secure()
@description('Shared administrator key. Pass this at deployment time; never commit it.')
param adminKey string

@description('Public HTTPS host and DNS name used in generated player launchers.')
param customDomainName string = 'soullink.colonelcreep.cloud'

@description('External game TCP ports exposed by the Container App.')
param tcpPorts array = [
  54321
  54322
  54323
  54324
  54325
]

@description('Consumption allocation: small=1 CPU/2 GiB, medium=2 CPU/4 GiB, large=4 CPU/8 GiB.')
@allowed([
  'small'
  'medium'
  'large'
])
param containerSize string = 'small'

@description('Bind the custom domain after its managed certificate has been issued.')
param bindCustomDomain bool = false

@description('Managed certificate resource ID returned by infra/domain.bicep.')
param managedCertificateId string = ''

var resourceToken = uniqueString(subscription().id, resourceGroup().id, location, environmentName)
var cpuCores = containerSize == 'large' ? 4 : containerSize == 'medium' ? 2 : 1
var memorySize = containerSize == 'large' ? '8Gi' : containerSize == 'medium' ? '4Gi' : '2Gi'
var names = {
  virtualNetwork: 'azvnt${resourceToken}'
  subnet: 'azsnt${resourceToken}'
  logAnalytics: 'azlog${resourceToken}'
  storageAccount: 'azst${resourceToken}'
  managedIdentity: 'azid${resourceToken}'
  containerEnvironment: 'azcae${resourceToken}'
  containerApp: 'azapp${resourceToken}'
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

module app 'modules/app.bicep' = {
  name: 'container-app'
  params: {
    location: location
    appName: names.containerApp
    environmentName: environment.outputs.environmentName
    environmentStorageName: environment.outputs.environmentStorageName
    identityName: names.managedIdentity
    containerImage: containerImage
    adminKey: adminKey
    customDomainName: customDomainName
    bindCustomDomain: bindCustomDomain
    managedCertificateId: managedCertificateId
    tcpPorts: tcpPorts
    cpuCores: cpuCores
    memorySize: memorySize
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
output containerAppName string = app.outputs.appName
output containerAppFqdn string = app.outputs.fqdn
output customDomainVerificationId string = app.outputs.customDomainVerificationId
output gameTcpPorts array = tcpPorts
