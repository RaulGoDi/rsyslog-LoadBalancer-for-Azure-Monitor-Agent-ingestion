resource "random_password" "admin_alphanumeric" {
  length      = 9
  special     = false
  min_lower   = 1
  min_upper   = 1
  min_numeric = 1
}

resource "random_shuffle" "admin_password" {
  input        = concat(split("", random_password.admin_alphanumeric.result), ["!"])
  result_count = 10
}

locals {
  admin_password = join("", random_shuffle.admin_password.result)
}
