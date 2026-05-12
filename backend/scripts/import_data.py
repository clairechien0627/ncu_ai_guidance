import os
import sys
import subprocess
import json
import requests
from pathlib import Path

# Add parent directory to path to import config
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import settings

DATA_PACKAGE_DIR = Path(__file__).parent.parent.parent / "data-package"
COLLECTION_NAME = "reports"

def run_command(command, shell=True):
    print(f"Executing: {command}")
    result = subprocess.run(command, shell=shell, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Error: {result.stderr}")
    return result

def get_container_name(service_part):
    container_cmd = f"docker ps --filter name={service_part} --format \"{{{{.Names}}}}\""
    result = run_command(container_cmd)
    names = result.stdout.strip().split('\n')
    return names[0] if names[0] else None

def import_postgres(manifest):
    print("\n--- Importing PostgreSQL Dump ---")
    dump_file = manifest.get("postgres_dump")
    if not dump_file:
        print("No postgres_dump found in manifest.")
        return False
        
    dump_path = DATA_PACKAGE_DIR / "postgres" / dump_file
    if not dump_path.exists():
        print(f"Dump file not found: {dump_path}")
        return False

    container_name = get_container_name("postgres")
    if not container_name:
        print("Could not find postgres container. Is it running?")
        return False

    print(f"Restoring to container: {container_name}")
    
    # Drop and recreate DB to ensure clean state
    # NOTE: This might fail if there are active connections.
    run_command(f"docker exec {container_name} dropdb -U postgres --if-exists reportdb")
    run_command(f"docker exec {container_name} createdb -U postgres reportdb")
    
    # Restore command
    # -d reportdb: target database
    import_cmd = f"docker exec -i {container_name} pg_restore -U postgres -d reportdb"
    
    try:
        with open(dump_path, "rb") as f:
            result = subprocess.run(import_cmd, shell=True, input=f.read(), capture_output=True)
            if result.returncode == 0:
                print("PostgreSQL import successful.")
                return True
            else:
                print(f"PostgreSQL restore failed: {result.stderr.decode()}")
                # Sometimes pg_restore returns 1 but it's just warnings
                if "warning" in result.stderr.decode().lower():
                     print("Detected warnings but continuing...")
                     return True
                return False
    except Exception as e:
        print(f"Failed to import PostgreSQL: {e}")
        return False

def import_qdrant(manifest):
    print("\n--- Importing Qdrant Snapshot ---")
    snapshot_file = manifest.get("qdrant_snapshot")
    if not snapshot_file:
        print("No qdrant_snapshot found in manifest.")
        return False
        
    snapshot_path = DATA_PACKAGE_DIR / "qdrant" / snapshot_file
    if not snapshot_path.exists():
        print(f"Snapshot file not found: {snapshot_path}")
        return False

    # Upload snapshot and restore
    url = f"{settings.qdrant_url}/collections/{COLLECTION_NAME}/snapshots/upload?priority=snapshot"
    
    try:
        print(f"Uploading and restoring snapshot from {snapshot_file}...")
        with open(snapshot_path, 'rb') as f:
            files = {'snapshot': f}
            response = requests.post(url, files=files)
            response.raise_for_status()
            print("Qdrant import successful.")
            return True
    except Exception as e:
        print(f"Failed to import Qdrant: {e}")
        return False

def main():
    manifest_path = DATA_PACKAGE_DIR / "manifest.json"
    if not manifest_path.exists():
        print(f"Manifest not found at {manifest_path}. Have you exported data first?")
        return

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    print(f"Found data package from: {manifest.get('export_time')}")
    print(f"Embedding Model used: {manifest.get('embedding_model')}")
    
    p_success = import_postgres(manifest)
    q_success = import_qdrant(manifest)
    
    if p_success and q_success:
        print("\nSuccess! All data has been restored.")
    else:
        print("\nImport partially failed. Please check the logs.")

if __name__ == "__main__":
    main()
