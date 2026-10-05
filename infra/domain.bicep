targetScope = 'resourceGroup'

@description('Azure region containing the Container Apps environment.')
param location string = 'eastus2'

@description('Name of the existing Container Apps managed environment.')
param containerEnvironmentName string

@description('Generated Container App FQDN returned by main.bicep.')
param containerAppFqdn string

@description('Domain verification ID returned by main.bicep.')
param customDomainVerificationId string

@description('Custom hostname to issue and bind.')
param customDomainName string = 'soullink.colonelcreep.cloud'

@description('Existing Azure DNS zone name.')
param dnsZoneName string = 'colonelcreep.cloud'

@description('Resource group containing the existing Azure DNS zone.')
param dnsZoneResourceGroup string = resourceGroup().name

@description('Subscription containing the existing Azure DNS zone.')
param dnsZoneSubscriptionId string = subscription().subscriptionId

@description('Tags applied to the managed certificate.')
param tags object = {
  application: 'auto-soullink'
}

var relativeRecordName = replace(customDomainName, '.${dnsZoneName}', '')
var certificateName = 'azcrt${uniqueString(subscription().id, resourceGroup().id, customDomainName)}'

module dns 'modules/dns.bicep' = {
  name: 'custom-domain-dns'
  scope: resourceGroup(dnsZoneSubscriptionId, dnsZoneResourceGroup)
  params: {
    dnsZoneName: dnsZoneName
    relativeRecordName: relativeRecordName
    cnameTarget: containerAppFqdn
    verificationId: customDomainVerificationId
  }
}

resource environment 'Microsoft.App/managedEnvironments@2025-01-01' existing = {
  name: containerEnvironmentName
}

resource certificate 'Microsoft.App/managedEnvironments/managedCertificates@2025-01-01' = {
  parent: environment
  name: certificateName
  location: location
  tags: tags
  properties: {
    subjectName: customDomainName
    domainControlValidation: 'CNAME'
  }
  dependsOn: [
    dns
  ]
}

output managedCertificateId string = certificate.id
output customDomainName string = customDomainName
output cnameTarget string = containerAppFqdn
output bindingInstruction string = 'Redeploy main.bicep with bindCustomDomain=true and managedCertificateId set to this deployment output.'
