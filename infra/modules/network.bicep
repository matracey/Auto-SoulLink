param location string
param virtualNetworkName string
param subnetName string
param tags object

resource virtualNetwork 'Microsoft.Network/virtualNetworks@2024-07-01' = {
  name: virtualNetworkName
  location: location
  tags: tags
  properties: {
    addressSpace: {
      addressPrefixes: [
        '10.20.0.0/24'
      ]
    }
    subnets: [
      {
        name: subnetName
        properties: {
          addressPrefix: '10.20.0.0/27'
          delegations: [
            {
              name: 'container-apps'
              properties: {
                serviceName: 'Microsoft.App/environments'
              }
            }
          ]
        }
      }
    ]
  }
}

output virtualNetworkId string = virtualNetwork.id
output subnetId string = resourceId('Microsoft.Network/virtualNetworks/subnets', virtualNetwork.name, subnetName)
