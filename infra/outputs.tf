output "resource_group_name" {
  description = "Dedicated resource group created for the lab."
  value       = azurerm_resource_group.lab.name
}

output "log_analytics_workspace_id" {
  description = "Log Analytics workspace resource ID."
  value       = azurerm_log_analytics_workspace.lab.id
}

output "syslog_load_balancer_private_ip" {
  description = "Internal TCP syslog ingress address."
  value       = azurerm_lb.ingress.private_ip_address
}

output "forwarder_scale_set_name" {
  description = "Rsyslog forwarding VM scale set name."
  value       = azurerm_linux_virtual_machine_scale_set.forwarders.name
}

output "collector_scale_set_name" {
  description = "AMA collector VM scale set name."
  value       = azurerm_linux_virtual_machine_scale_set.collectors.name
}

output "jump_host_public_ip" {
  description = "Optional jump host public IP; null when the jump host is disabled."
  value       = var.enable_jump_host ? azurerm_public_ip.jump[0].ip_address : null
}

output "generator_private_ip" {
  description = "Private IP address of the event generator."
  value       = azurerm_network_interface.generator.private_ip_address
}
