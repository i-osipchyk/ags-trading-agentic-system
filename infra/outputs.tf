output "functions" {
  value = module.app.function_names
}

output "data_bucket" {
  value = module.data.name
}

output "cycle_state_machine" {
  value = aws_sfn_state_machine.cycle.name
}
