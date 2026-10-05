param location string
param appName string
param environmentName string
param environmentStorageName string
param identityName string
param containerImage string
@secure()
param adminKey string
param customDomainName string
param bindCustomDomain bool
param managedCertificateId string
param tcpPorts array
param cpuCores int
param memorySize string
param tags object

resource environment 'Microsoft.App/managedEnvironments@2025-01-01' existing = {
  name: environmentName
}

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: identityName
}

var defaultFqdn = '${appName}.${environment.properties.defaultDomain}'

resource app 'Microsoft.App/containerApps@2025-01-01' = {
  name: appName
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identity.id}': {}
    }
  }
  properties: {
    environmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      maxInactiveRevisions: 1
      secrets: [
        {
          name: 'admin-key'
          value: adminKey
        }
      ]
      ingress: {
        external: true
        targetPort: 8090
        transport: 'auto'
        allowInsecure: false
        corsPolicy: {
          allowCredentials: true
          allowedHeaders: [
            '*'
          ]
          allowedMethods: [
            'GET'
            'POST'
            'OPTIONS'
          ]
          allowedOrigins: [
            'https://${customDomainName}'
          ]
        }
        additionalPortMappings: [
          for port in tcpPorts: {
            exposedPort: port
            external: true
            targetPort: port
          }
        ]
        customDomains: bindCustomDomain ? [
          {
            bindingType: 'SniEnabled'
            certificateId: managedCertificateId
            name: customDomainName
          }
        ] : []
      }
    }
    template: {
      containers: [
        {
          name: 'manager'
          image: containerImage
          env: [
            {
              name: 'SLINK_ADMIN_KEY'
              secretRef: 'admin-key'
            }
            {
              name: 'SLINK_ADMIN_COOKIE_SECURE'
              value: 'true'
            }
            {
              name: 'SLINK_ALLOWED_HOSTS'
              value: '${customDomainName},${defaultFqdn}'
            }
            {
              name: 'SLINK_PUBLIC_HOST'
              value: customDomainName
            }
            {
              name: 'SLINK_TCP_PORT_POOL'
              value: join(tcpPorts, ',')
            }
            {
              name: 'SLINK_DATA_DIR'
              value: '/data/runs'
            }
            {
              name: 'SLINK_ROM_DIR'
              value: '/data/roms'
            }
          ]
          resources: {
            cpu: cpuCores
            memory: memorySize
          }
          probes: [
            {
              type: 'Liveness'
              failureThreshold: 3
              httpGet: {
                path: '/healthz'
                port: 8090
                scheme: 'HTTP'
              }
              initialDelaySeconds: 20
              periodSeconds: 30
              timeoutSeconds: 5
            }
            {
              type: 'Readiness'
              failureThreshold: 3
              httpGet: {
                path: '/readyz'
                port: 8090
                scheme: 'HTTP'
              }
              initialDelaySeconds: 10
              periodSeconds: 10
              timeoutSeconds: 5
            }
          ]
          volumeMounts: [
            {
              volumeName: 'slink-data'
              mountPath: '/data'
            }
          ]
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 1
      }
      terminationGracePeriodSeconds: 30
      volumes: [
        {
          name: 'slink-data'
          storageName: environmentStorageName
          storageType: 'AzureFile'
        }
      ]
    }
  }
}

output appName string = app.name
output appId string = app.id
output fqdn string = app.properties.configuration.ingress.fqdn
output customDomainVerificationId string = app.properties.customDomainVerificationId
