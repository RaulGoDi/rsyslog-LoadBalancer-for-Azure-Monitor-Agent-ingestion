variable "subscription_id" {
  description = "Azure subscription ID used for this lab."
  type        = string

  validation {
    condition     = can(regex("^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$", var.subscription_id))
    error_message = "subscription_id must be a GUID."
  }
}

variable "name_prefix" {
  description = "Short lowercase prefix for lab resource names."
  type        = string
  default     = "rsyslog-lab"

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{1,19}$", var.name_prefix))
    error_message = "name_prefix must be 2-20 lowercase letters, digits, or hyphens and start with a letter or digit."
  }
}

variable "location" {
  description = "Azure region name."
  type        = string
  default     = "spaincentral"
}

variable "admin_username" {
  description = "Linux administrator account name."
  type        = string
  default     = "labadmin"
}

variable "ssh_public_key" {
  description = "SSH public key installed on the lab VMs. Never provide a private key."
  type        = string

  validation {
    condition     = can(regex("^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp[0-9]+) ", trimspace(var.ssh_public_key)))
    error_message = "ssh_public_key must be an OpenSSH Ed25519, RSA, or ECDSA public key."
  }
}

variable "rhel_image_publisher" {
  description = "Marketplace image publisher for RHEL 9 forwarders and collectors."
  type        = string
  default     = "RedHat"
}

variable "rhel_image_offer" {
  description = "Marketplace image offer for RHEL 9 forwarders and collectors."
  type        = string
  default     = "RHEL"
}

variable "rhel_image_sku" {
  description = "Marketplace image SKU for RHEL 9 forwarders and collectors."
  type        = string
  default     = "9_4"
}

variable "ubuntu_image_publisher" {
  description = "Marketplace image publisher for the generator and optional jump host."
  type        = string
  default     = "Canonical"
}

variable "ubuntu_image_offer" {
  description = "Marketplace image offer for the generator and optional jump host."
  type        = string
  default     = "ubuntu-24_04-lts"
}

variable "ubuntu_image_sku" {
  description = "Marketplace image SKU for the generator and optional jump host."
  type        = string
  default     = "server"
}

variable "availability_zones" {
  description = "Optional, pre-validated zones for the forwarder and collector VM scale sets. Leave empty when unsupported."
  type        = list(string)
  default     = []

  validation {
    condition     = length(var.availability_zones) == 0 || length(var.availability_zones) >= 2
    error_message = "Specify either no zones or at least two zones so instances can be spread."
  }
}

variable "enable_jump_host" {
  description = "Create the optional public jump VM. Requires admin_cidr."
  type        = bool
  default     = false
}

variable "admin_cidr" {
  description = "Administrator public IPv4 CIDR allowed to SSH to the optional jump VM, for example 203.0.113.10/32."
  type        = string
  default     = null

  validation {
    condition     = var.admin_cidr == null || (can(cidrhost(var.admin_cidr, 0)) && can(cidrnetmask(var.admin_cidr)))
    error_message = "admin_cidr must be a valid IPv4 CIDR when specified."
  }
}

variable "tags" {
  description = "Additional Azure resource tags."
  type        = map(string)
  default     = {}
}
