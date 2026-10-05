param dnsZoneName string
param relativeRecordName string
param cnameTarget string
param verificationId string

resource dnsZone 'Microsoft.Network/dnsZones@2018-05-01' existing = {
  name: dnsZoneName
}

resource cname 'Microsoft.Network/dnsZones/CNAME@2018-05-01' = {
  parent: dnsZone
  name: relativeRecordName
  properties: {
    TTL: 300
    CNAMERecord: {
      cname: cnameTarget
    }
  }
}

resource verification 'Microsoft.Network/dnsZones/TXT@2018-05-01' = {
  parent: dnsZone
  name: 'asuid.${relativeRecordName}'
  properties: {
    TTL: 300
    TXTRecords: [
      {
        value: [
          verificationId
        ]
      }
    ]
  }
}

output cnameId string = cname.id
output verificationId string = verification.id
