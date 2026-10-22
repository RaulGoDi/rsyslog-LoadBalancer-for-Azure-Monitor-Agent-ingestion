locals {
  prefix = var.name_prefix
  tags = merge({
    project     = "rsyslog-ama-load-balancing-lab"
    environment = "lab"
  }, var.tags)

  syslog_log_levels = [
    "Debug",
    "Info",
    "Notice",
    "Warning",
    "Error",
    "Critical",
    "Alert",
    "Emergency",
  ]

  forwarder_name = "${local.prefix}-forwarders"
  collector_name = "${local.prefix}-collectors"
}

resource "azurerm_resource_group" "lab" {
  name     = "${local.prefix}-rg"
  location = var.location
  tags     = local.tags
}

resource "azurerm_virtual_network" "lab" {
  name                = "${local.prefix}-vnet"
  address_space       = ["10.80.0.0/16"]
  location            = azurerm_resource_group.lab.location
  resource_group_name = azurerm_resource_group.lab.name
  tags                = local.tags
}

resource "azurerm_subnet" "generator" {
  name                 = "${local.prefix}-generator-snet"
  resource_group_name  = azurerm_resource_group.lab.name
  virtual_network_name = azurerm_virtual_network.lab.name
  address_prefixes     = ["10.80.1.0/24"]
}

resource "azurerm_subnet" "logging" {
  name                 = "${local.prefix}-logging-snet"
  resource_group_name  = azurerm_resource_group.lab.name
  virtual_network_name = azurerm_virtual_network.lab.name
  address_prefixes     = ["10.80.2.0/24"]
}

resource "azurerm_network_security_group" "generator" {
  name                = "${local.prefix}-generator-nsg"
  location            = azurerm_resource_group.lab.location
  resource_group_name = azurerm_resource_group.lab.name
  tags                = local.tags
}

resource "azurerm_network_security_group" "logging" {
  name                = "${local.prefix}-logging-nsg"
  location            = azurerm_resource_group.lab.location
  resource_group_name = azurerm_resource_group.lab.name
  tags                = local.tags
}

resource "azurerm_network_security_group" "collectors" {
  name                = "${local.prefix}-collectors-nsg"
  location            = azurerm_resource_group.lab.location
  resource_group_name = azurerm_resource_group.lab.name
  tags                = local.tags
}

resource "azurerm_network_security_rule" "collector_tcp_from_logging_subnet" {
  name                        = "Allow-TCP-514-from-logging-subnet"
  priority                    = 100
  direction                   = "Inbound"
  access                      = "Allow"
  protocol                    = "Tcp"
  source_port_range           = "*"
  destination_port_range      = "514"
  source_address_prefix       = azurerm_subnet.logging.address_prefixes[0]
  destination_address_prefix  = "*"
  resource_group_name         = azurerm_resource_group.lab.name
  network_security_group_name = azurerm_network_security_group.collectors.name
}

resource "azurerm_network_security_rule" "collector_tcp_deny_other_vnet" {
  name                        = "Deny-TCP-514-from-other-VNet-sources"
  priority                    = 110
  direction                   = "Inbound"
  access                      = "Deny"
  protocol                    = "Tcp"
  source_port_range           = "*"
  destination_port_range      = "514"
  source_address_prefix       = "VirtualNetwork"
  destination_address_prefix  = "*"
  resource_group_name         = azurerm_resource_group.lab.name
  network_security_group_name = azurerm_network_security_group.collectors.name
}

resource "azurerm_subnet_network_security_group_association" "generator" {
  subnet_id                 = azurerm_subnet.generator.id
  network_security_group_id = azurerm_network_security_group.generator.id
}

resource "azurerm_subnet_network_security_group_association" "logging" {
  subnet_id                 = azurerm_subnet.logging.id
  network_security_group_id = azurerm_network_security_group.logging.id
}

resource "azurerm_network_security_rule" "jump_ssh" {
  count = var.enable_jump_host ? 1 : 0

  name                        = "Allow-SSH-from-jump-host"
  priority                    = 105
  direction                   = "Inbound"
  access                      = "Allow"
  protocol                    = "Tcp"
  source_port_range           = "*"
  destination_port_range      = "22"
  source_address_prefix       = azurerm_network_interface.jump[0].private_ip_address
  destination_address_prefix  = "*"
  resource_group_name         = azurerm_resource_group.lab.name
  network_security_group_name = azurerm_network_security_group.generator.name
}

resource "azurerm_network_security_rule" "jump_ssh_logging" {
  count = var.enable_jump_host ? 1 : 0

  name                        = "Allow-SSH-from-jump-host"
  priority                    = 100
  direction                   = "Inbound"
  access                      = "Allow"
  protocol                    = "Tcp"
  source_port_range           = "*"
  destination_port_range      = "22"
  source_address_prefix       = azurerm_network_interface.jump[0].private_ip_address
  destination_address_prefix  = "*"
  resource_group_name         = azurerm_resource_group.lab.name
  network_security_group_name = azurerm_network_security_group.logging.name
}

resource "azurerm_network_security_rule" "deny_ssh_from_other_vnet_sources" {
  name                        = "Deny-SSH-from-other-VNet-sources"
  priority                    = 110
  direction                   = "Inbound"
  access                      = "Deny"
  protocol                    = "Tcp"
  source_port_range           = "*"
  destination_port_range      = "22"
  source_address_prefix       = "VirtualNetwork"
  destination_address_prefix  = "*"
  resource_group_name         = azurerm_resource_group.lab.name
  network_security_group_name = azurerm_network_security_group.generator.name
}

resource "azurerm_network_security_rule" "deny_ssh_from_other_vnet_logging" {
  name                        = "Deny-SSH-from-other-VNet-sources"
  priority                    = 130
  direction                   = "Inbound"
  access                      = "Deny"
  protocol                    = "Tcp"
  source_port_range           = "*"
  destination_port_range      = "22"
  source_address_prefix       = "VirtualNetwork"
  destination_address_prefix  = "*"
  resource_group_name         = azurerm_resource_group.lab.name
  network_security_group_name = azurerm_network_security_group.logging.name
}

resource "azurerm_network_security_rule" "syslog_to_forwarders" {
  name                        = "Allow-Syslog-TCP-from-generator-subnet"
  priority                    = 110
  direction                   = "Inbound"
  access                      = "Allow"
  protocol                    = "Tcp"
  source_port_range           = "*"
  destination_port_range      = "514"
  source_address_prefix       = azurerm_subnet.generator.address_prefixes[0]
  destination_address_prefix  = "*"
  resource_group_name         = azurerm_resource_group.lab.name
  network_security_group_name = azurerm_network_security_group.logging.name
}

resource "azurerm_network_security_rule" "load_balancer_probe" {
  name                        = "Allow-Azure-Load-Balancer-Probe"
  priority                    = 120
  direction                   = "Inbound"
  access                      = "Allow"
  protocol                    = "Tcp"
  source_port_range           = "*"
  destination_port_range      = "514"
  source_address_prefix       = "AzureLoadBalancer"
  destination_address_prefix  = "*"
  resource_group_name         = azurerm_resource_group.lab.name
  network_security_group_name = azurerm_network_security_group.logging.name
}

resource "azurerm_public_ip" "nat" {
  name                = "${local.prefix}-nat-pip"
  location            = azurerm_resource_group.lab.location
  resource_group_name = azurerm_resource_group.lab.name
  allocation_method   = "Static"
  sku                 = "Standard"
  tags                = local.tags
}

resource "azurerm_nat_gateway" "lab" {
  name                    = "${local.prefix}-nat"
  location                = azurerm_resource_group.lab.location
  resource_group_name     = azurerm_resource_group.lab.name
  sku_name                = "Standard"
  idle_timeout_in_minutes = 10
  tags                    = local.tags
}

resource "azurerm_nat_gateway_public_ip_association" "lab" {
  nat_gateway_id       = azurerm_nat_gateway.lab.id
  public_ip_address_id = azurerm_public_ip.nat.id
}

resource "azurerm_subnet_nat_gateway_association" "generator" {
  subnet_id      = azurerm_subnet.generator.id
  nat_gateway_id = azurerm_nat_gateway.lab.id
}

resource "azurerm_subnet_nat_gateway_association" "logging" {
  subnet_id      = azurerm_subnet.logging.id
  nat_gateway_id = azurerm_nat_gateway.lab.id
}

resource "azurerm_lb" "ingress" {
  name                = "${local.prefix}-ilb"
  location            = azurerm_resource_group.lab.location
  resource_group_name = azurerm_resource_group.lab.name
  sku                 = "Standard"
  tags                = local.tags

  frontend_ip_configuration {
    name                          = "syslog-frontend"
    subnet_id                     = azurerm_subnet.logging.id
    private_ip_address            = "10.80.2.10"
    private_ip_address_allocation = "Static"
  }
}

resource "azurerm_lb_backend_address_pool" "forwarders" {
  name            = "forwarders"
  loadbalancer_id = azurerm_lb.ingress.id
}

resource "azurerm_lb_probe" "forwarders" {
  name                = "rsyslog-tcp-514"
  loadbalancer_id     = azurerm_lb.ingress.id
  protocol            = "Tcp"
  port                = 514
  interval_in_seconds = 5
  number_of_probes    = 2
}

resource "azurerm_lb_rule" "syslog" {
  name                           = "syslog-tcp-514"
  loadbalancer_id                = azurerm_lb.ingress.id
  protocol                       = "Tcp"
  frontend_port                  = 514
  backend_port                   = 514
  frontend_ip_configuration_name = "syslog-frontend"
  backend_address_pool_ids       = [azurerm_lb_backend_address_pool.forwarders.id]
  probe_id                       = azurerm_lb_probe.forwarders.id
  load_distribution              = "Default"
  floating_ip_enabled            = false
  disable_outbound_snat          = true
}

resource "azurerm_linux_virtual_machine_scale_set" "collectors" {
  name                   = local.collector_name
  resource_group_name    = azurerm_resource_group.lab.name
  location               = azurerm_resource_group.lab.location
  sku                    = "Standard_D2s_v5"
  instances              = 2
  zones                  = var.availability_zones
  zone_balance           = length(var.availability_zones) > 0
  single_placement_group = false
  upgrade_mode           = "Manual"
  overprovision          = false
  admin_username         = var.admin_username
  custom_data = base64encode(templatefile("${path.module}/cloud-init/collector.yaml.tftpl", {
    collector_setup_b64 = base64encode(file("${path.module}/scripts/configure-ama-collector.sh"))
  }))
  tags = local.tags

  admin_ssh_key {
    username   = var.admin_username
    public_key = var.ssh_public_key
  }

  identity {
    type = "SystemAssigned"
  }

  source_image_reference {
    publisher = var.rhel_image_publisher
    offer     = var.rhel_image_offer
    sku       = var.rhel_image_sku
    version   = "latest"
  }

  os_disk {
    storage_account_type = "StandardSSD_LRS"
    caching              = "ReadWrite"
    disk_size_gb         = 64
  }

  network_interface {
    name                      = "collector-nic"
    primary                   = true
    network_security_group_id = azurerm_network_security_group.collectors.id

    ip_configuration {
      name      = "internal"
      primary   = true
      subnet_id = azurerm_subnet.logging.id
    }
  }

  depends_on = [
    azurerm_subnet_network_security_group_association.logging,
    azurerm_subnet_nat_gateway_association.logging,
    azurerm_network_security_rule.jump_ssh_logging,
    azurerm_network_security_rule.deny_ssh_from_other_vnet_logging,
    azurerm_network_security_rule.syslog_to_forwarders,
    azurerm_network_security_rule.load_balancer_probe,
    azurerm_network_security_rule.collector_tcp_from_logging_subnet,
    azurerm_network_security_rule.collector_tcp_deny_other_vnet,
  ]
}

resource "azurerm_linux_virtual_machine_scale_set" "forwarders" {
  name                   = local.forwarder_name
  resource_group_name    = azurerm_resource_group.lab.name
  location               = azurerm_resource_group.lab.location
  sku                    = "Standard_D2s_v5"
  instances              = 2
  zones                  = var.availability_zones
  zone_balance           = length(var.availability_zones) > 0
  single_placement_group = false
  upgrade_mode           = "Manual"
  overprovision          = false
  admin_username         = var.admin_username
  custom_data = base64encode(templatefile("${path.module}/cloud-init/forwarder.yaml.tftpl", {
    forwarder_setup_b64      = base64encode(file("${path.module}/scripts/configure-rsyslog-target-pool.sh"))
    discovery_script_b64     = base64encode(file("${path.module}/scripts/discover-collector-ips.py"))
    bootstrap_script_b64     = base64encode(file("${path.module}/scripts/bootstrap-forwarder.sh"))
    subscription_id          = var.subscription_id
    resource_group_name      = azurerm_resource_group.lab.name
    collector_scale_set_name = azurerm_linux_virtual_machine_scale_set.collectors.name
  }))
  tags = local.tags

  admin_ssh_key {
    username   = var.admin_username
    public_key = var.ssh_public_key
  }

  identity {
    type = "SystemAssigned"
  }

  source_image_reference {
    publisher = var.rhel_image_publisher
    offer     = var.rhel_image_offer
    sku       = var.rhel_image_sku
    version   = "latest"
  }

  os_disk {
    storage_account_type = "StandardSSD_LRS"
    caching              = "ReadWrite"
    disk_size_gb         = 64
  }

  network_interface {
    name    = "forwarder-nic"
    primary = true

    ip_configuration {
      name                                   = "internal"
      primary                                = true
      subnet_id                              = azurerm_subnet.logging.id
      load_balancer_backend_address_pool_ids = [azurerm_lb_backend_address_pool.forwarders.id]
    }
  }

  depends_on = [
    azurerm_subnet_network_security_group_association.logging,
    azurerm_subnet_nat_gateway_association.logging,
    azurerm_network_security_rule.jump_ssh_logging,
    azurerm_network_security_rule.deny_ssh_from_other_vnet_logging,
    azurerm_network_security_rule.syslog_to_forwarders,
    azurerm_network_security_rule.load_balancer_probe,
  ]
}

resource "azurerm_role_assignment" "forwarder_collector_discovery" {
  scope                = azurerm_resource_group.lab.id
  role_definition_name = "Reader"
  principal_id         = azurerm_linux_virtual_machine_scale_set.forwarders.identity[0].principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_virtual_machine_scale_set_extension" "ama" {
  name                         = "AzureMonitorLinuxAgent"
  virtual_machine_scale_set_id = azurerm_linux_virtual_machine_scale_set.collectors.id
  publisher                    = "Microsoft.Azure.Monitor"
  type                         = "AzureMonitorLinuxAgent"
  type_handler_version         = "1.0"
  auto_upgrade_minor_version   = true
  automatic_upgrade_enabled    = true
  settings                     = jsonencode({})
}

resource "azurerm_log_analytics_workspace" "lab" {
  name                = "${local.prefix}-law"
  location            = azurerm_resource_group.lab.location
  resource_group_name = azurerm_resource_group.lab.name
  sku                 = "PerGB2018"
  tags                = local.tags
}

resource "azurerm_sentinel_log_analytics_workspace_onboarding" "lab" {
  workspace_id = azurerm_log_analytics_workspace.lab.id
}

resource "azurerm_monitor_data_collection_rule" "syslog" {
  name                = "${local.prefix}-syslog-dcr"
  resource_group_name = azurerm_resource_group.lab.name
  location            = azurerm_resource_group.lab.location
  kind                = "Linux"
  description         = "Collect only local6 Syslog events from the AMA collectors."
  tags                = local.tags

  destinations {
    log_analytics {
      name                  = "lab-workspace"
      workspace_resource_id = azurerm_log_analytics_workspace.lab.id
    }
  }

  data_flow {
    streams      = ["Microsoft-Syslog"]
    destinations = ["lab-workspace"]
  }

  data_sources {
    syslog {
      name           = "local6-syslog"
      streams        = ["Microsoft-Syslog"]
      facility_names = ["local6"]
      log_levels     = local.syslog_log_levels
    }
  }
}

resource "azurerm_monitor_data_collection_rule" "cef" {
  name                = "${local.prefix}-cef-dcr"
  resource_group_name = azurerm_resource_group.lab.name
  location            = azurerm_resource_group.lab.location
  kind                = "Linux"
  description         = "Collect only local7 CEF events from the AMA collectors."
  tags                = local.tags

  destinations {
    log_analytics {
      name                  = "lab-workspace"
      workspace_resource_id = azurerm_log_analytics_workspace.lab.id
    }
  }

  data_flow {
    streams      = ["Microsoft-CommonSecurityLog"]
    destinations = ["lab-workspace"]
  }

  data_sources {
    syslog {
      name           = "local7-cef"
      streams        = ["Microsoft-CommonSecurityLog"]
      facility_names = ["local7"]
      log_levels     = local.syslog_log_levels
    }
  }
}

resource "azurerm_monitor_data_collection_rule_association" "syslog" {
  name                    = "${local.prefix}-syslog-association"
  target_resource_id      = azurerm_linux_virtual_machine_scale_set.collectors.id
  data_collection_rule_id = azurerm_monitor_data_collection_rule.syslog.id
  description             = "Attach the local6 Syslog DCR to the collector VM scale set."
}

resource "azurerm_monitor_data_collection_rule_association" "cef" {
  name                    = "${local.prefix}-cef-association"
  target_resource_id      = azurerm_linux_virtual_machine_scale_set.collectors.id
  data_collection_rule_id = azurerm_monitor_data_collection_rule.cef.id
  description             = "Attach the local7 CEF DCR to the collector VM scale set."
}

resource "azurerm_network_interface" "generator" {
  name                = "${local.prefix}-generator-nic"
  location            = azurerm_resource_group.lab.location
  resource_group_name = azurerm_resource_group.lab.name
  tags                = local.tags

  ip_configuration {
    name                          = "internal"
    subnet_id                     = azurerm_subnet.generator.id
    private_ip_address_allocation = "Dynamic"
  }
}

resource "azurerm_linux_virtual_machine" "generator" {
  name                            = "${local.prefix}-generator"
  resource_group_name             = azurerm_resource_group.lab.name
  location                        = azurerm_resource_group.lab.location
  size                            = "Standard_D2s_v5"
  admin_username                  = var.admin_username
  disable_password_authentication = true
  network_interface_ids           = [azurerm_network_interface.generator.id]
  custom_data = base64encode(templatefile("${path.module}/cloud-init/generator.yaml.tftpl", {
    generator_script_b64 = base64encode(file("${path.module}/../scripts/generate_events.py"))
    admin_username       = var.admin_username
  }))
  tags = local.tags

  admin_ssh_key {
    username   = var.admin_username
    public_key = var.ssh_public_key
  }

  os_disk {
    caching              = "ReadWrite"
    storage_account_type = "StandardSSD_LRS"
    disk_size_gb         = 64
  }

  source_image_reference {
    publisher = var.ubuntu_image_publisher
    offer     = var.ubuntu_image_offer
    sku       = var.ubuntu_image_sku
    version   = "latest"
  }

  depends_on = [
    azurerm_subnet_network_security_group_association.generator,
    azurerm_subnet_nat_gateway_association.generator,
    azurerm_network_security_rule.jump_ssh,
    azurerm_network_security_rule.deny_ssh_from_other_vnet_sources,
    azurerm_network_security_rule.jump_public_ssh,
  ]
}

resource "azurerm_public_ip" "jump" {
  count = var.enable_jump_host ? 1 : 0

  name                = "${local.prefix}-jump-pip"
  location            = azurerm_resource_group.lab.location
  resource_group_name = azurerm_resource_group.lab.name
  allocation_method   = "Static"
  sku                 = "Standard"
  tags                = local.tags
}

resource "azurerm_network_interface" "jump" {
  count = var.enable_jump_host ? 1 : 0

  name                = "${local.prefix}-jump-nic"
  location            = azurerm_resource_group.lab.location
  resource_group_name = azurerm_resource_group.lab.name
  tags                = local.tags

  ip_configuration {
    name                          = "internal"
    subnet_id                     = azurerm_subnet.generator.id
    private_ip_address_allocation = "Dynamic"
    public_ip_address_id          = azurerm_public_ip.jump[0].id
  }
}

resource "azurerm_network_security_rule" "jump_public_ssh" {
  count = var.enable_jump_host ? 1 : 0

  name                        = "Allow-SSH-from-administrator-CIDR"
  priority                    = 100
  direction                   = "Inbound"
  access                      = "Allow"
  protocol                    = "Tcp"
  source_port_range           = "*"
  destination_port_range      = "22"
  source_address_prefix       = coalesce(var.admin_cidr, "127.0.0.1/32")
  destination_address_prefix  = "*"
  resource_group_name         = azurerm_resource_group.lab.name
  network_security_group_name = azurerm_network_security_group.generator.name

  lifecycle {
    precondition {
      condition     = var.admin_cidr != null && can(cidrhost(var.admin_cidr, 0))
      error_message = "A valid admin_cidr is required when enable_jump_host is true."
    }
  }
}

resource "azurerm_linux_virtual_machine" "jump" {
  count = var.enable_jump_host ? 1 : 0

  name                            = "${local.prefix}-jump"
  resource_group_name             = azurerm_resource_group.lab.name
  location                        = azurerm_resource_group.lab.location
  size                            = "Standard_B2s"
  admin_username                  = var.admin_username
  disable_password_authentication = true
  network_interface_ids           = [azurerm_network_interface.jump[0].id]
  tags                            = local.tags

  admin_ssh_key {
    username   = var.admin_username
    public_key = var.ssh_public_key
  }

  os_disk {
    caching              = "ReadWrite"
    storage_account_type = "StandardSSD_LRS"
    disk_size_gb         = 32
  }

  source_image_reference {
    publisher = var.ubuntu_image_publisher
    offer     = var.ubuntu_image_offer
    sku       = var.ubuntu_image_sku
    version   = "latest"
  }

  depends_on = [
    azurerm_subnet_network_security_group_association.generator,
    azurerm_subnet_nat_gateway_association.generator,
    azurerm_network_security_rule.jump_public_ssh,
    azurerm_network_security_rule.jump_ssh,
    azurerm_network_security_rule.deny_ssh_from_other_vnet_sources,
  ]
}
