import sys
import os
import shutil

# Get the test directory and project directory dynamically
script_dir = os.path.dirname(os.path.abspath(__file__))
project_dir = os.path.dirname(script_dir)

config.soldb_dir = project_dir

# Find soldb dynamically
if shutil.which('soldb'):
    config.soldb = shutil.which('soldb')
elif os.path.exists(os.path.join(project_dir, 'MyEnv', 'bin', 'soldb')):
    config.soldb = os.path.join(project_dir, 'MyEnv', 'bin', 'soldb')
else:
    config.soldb = "MyEnv/bin/soldb"
config.rpc_url = "http://localhost:8545"
config.chain_id = "1"
config.private_key = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
config.test_contracts = {
    "contract_address": "0x5fbdb2315678afecb367f032d93f642f64180aa3",
    "deploy_tx": "0xe330dabe8d052687fb32549e8ee88472defc8a635acc06e6f5d494338655cb97",
    "test_tx": "0xc019ce682b36617beb62fb5f611e79290c088ab145ab82787bc8b6e0664a97db",
    "ethdebug_dir": os.path.join(project_dir, "examples", "test_debug")
}
# Determine solc path dynamically
solc_path = os.environ.get('SOLC_PATH')
if not solc_path:
    # Try to find solc in PATH
    solc_path = shutil.which('solc')
if not solc_path:
    # Fallback to a default
    solc_path = 'solc'
config.solc_path = solc_path

# Load the main config
lit_config.load_config(config, os.path.join(script_dir, "lit.cfg.py"))
