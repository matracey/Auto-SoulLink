from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INFRA = ROOT / "infra"


def text(path):
    return (INFRA / path).read_text(encoding="utf-8")


def test_core_template_defaults_to_east_us_2_and_project_resource_names():
    main = text("main.bicep")
    assert "param location string = 'eastus2'" in main
    assert "uniqueString(subscription().id, resourceGroup().id, location, environmentName)" in main
    assert "autoSoulLink-vnet-${environmentName}" in main
    assert "autoSoulLink-logAnalytics-${environmentName}" in main
    assert "autoSoulLink-managedIdentity-${environmentName}" in main
    assert "auto-soullink-container-app-${toLower(environmentName)}" in main
    assert "autosoullinkst${toLower(environmentName)}${take(resourceToken, 5)}" in main
    assert "autosoullinkacr${toLower(environmentName)}${take(resourceToken, 5)}" in main


def test_network_uses_dedicated_delegated_subnet():
    network = text("modules/network.bicep")
    assert "'10.20.0.0/27'" in network
    assert "Microsoft.App/environments" in network


def test_storage_is_https_only_and_keeps_required_file_share_key_access():
    storage = text("modules/storage.bicep")
    assert "allowBlobPublicAccess: false" in storage
    assert "allowSharedKeyAccess: true" in storage
    assert "minimumTlsVersion: 'TLS1_2'" in storage
    assert "supportsHttpsTrafficOnly: true" in storage
    assert "shareDeleteRetentionPolicy" in storage


def test_environment_connects_logs_vnet_and_azure_files():
    environment = text("modules/environment.bicep")
    assert "destination: 'log-analytics'" in environment
    assert "infrastructureSubnetId: infrastructureSubnetId" in environment
    assert "workloadProfileType: 'Consumption'" in environment
    assert "accessMode: 'ReadWrite'" in environment
    assert "storageAccount.listKeys().keys[0].value" in environment


def test_container_app_is_single_replica_with_http_and_five_tcp_ports():
    app = text("modules/app.bicep")
    assert "targetPort: 8090" in app
    assert "additionalPortMappings" in app
    assert "for port in tcpPorts" in app
    assert "minReplicas: 1" in app and "maxReplicas: 1" in app
    assert "activeRevisionsMode: 'Single'" in app


def test_container_app_mounts_data_and_configures_runtime():
    app = text("modules/app.bicep")
    for value in (
        "SLINK_ADMIN_KEY",
        "SLINK_ALLOWED_HOSTS",
        "SLINK_PUBLIC_HOST",
        "SLINK_TCP_PORT_POOL",
        "SLINK_DATA_DIR",
        "SLINK_ROM_DIR",
    ):
        assert value in app
    assert "mountPath: '/data'" in app
    assert "storageType: 'AzureFile'" in app
    assert "registries:" in app
    assert "identity: identity.id" in app


def test_container_app_has_liveness_and_readiness_probes():
    app = text("modules/app.bicep")
    assert "path: '/healthz'" in app
    assert "path: '/readyz'" in app


def test_consumption_size_derives_valid_cpu_memory_pairs():
    main = text("main.bicep")
    assert "param containerSize string = 'small'" in main
    assert "containerSize == 'large' ? 4" in main
    assert "containerSize == 'medium' ? 2 : 1" in main
    assert "containerSize == 'large' ? '8Gi'" in main
    assert "containerSize == 'medium' ? '4Gi' : '2Gi'" in main


def test_domain_template_uses_existing_azure_dns_zone_and_managed_certificate():
    domain = text("domain.bicep")
    dns = text("modules/dns.bicep")
    assert "Microsoft.Network/dnsZones@2018-05-01" in dns
    assert "Microsoft.Network/dnsZones/CNAME@2018-05-01" in dns
    assert "Microsoft.Network/dnsZones/TXT@2018-05-01" in dns
    assert "asuid.${relativeRecordName}" in dns
    assert "domainControlValidation: 'CNAME'" in domain
    assert "output managedCertificateId" in domain


def test_main_template_binds_domain_only_when_enabled():
    main = text("main.bicep")
    app = text("modules/app.bicep")
    assert "param bindCustomDomain bool = false" in main
    assert "param managedCertificateId string = ''" in main
    assert "customDomains: bindCustomDomain ?" in app


def test_registry_is_private_and_grants_managed_identity_pull():
    registry = text("modules/registry.bicep")
    assert "sku:" in registry and "name: 'Basic'" in registry
    assert "adminUserEnabled: false" in registry
    assert "anonymousPullEnabled: false" in registry
    assert "7f951dda-4ed3-4680-a7ca-43fe172d538d" in registry
    assert "principalId: pullPrincipalId" in registry
