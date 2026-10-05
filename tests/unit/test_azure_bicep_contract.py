from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
INFRA = ROOT / "infra"


def text(path):
    return (INFRA / path).read_text(encoding="utf-8")


def test_core_template_defaults_to_east_us_2_and_deterministic_names():
    main = text("main.bicep")
    assert "param location string = 'eastus2'" in main
    assert "uniqueString(subscription().id, resourceGroup().id, location, environmentName)" in main


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
