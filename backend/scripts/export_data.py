import os
import sys
import subprocess
import datetime
import json
import requests
from pathlib import Path

# Add parent directory to path to import config
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import settings

DATA_PACKAGE_DIR = Path(__file__).parent.parent.parent / "data-package"
QDRANT_DIR = DATA_PACKAGE_DIR / "qdrant"
POSTGRES_DIR = DATA_PACKAGE_DIR / "postgres"
COLLECTION_NAME = "reports"

def run_command(command, shell=True):
    print(f"Executing: {command}")
    result = subprocess.run(command, shell=shell, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Error: {result.stderr}")
    return result

def export_qdrant():
    print("\n--- Exporting Qdrant Snapshot ---")
    # Trigger snapshot creation
    url = f"{settings.qdrant_url}/collections/{COLLECTION_NAME}/snapshots"
    try:
        response = requests.post(url)
        response.raise_for_status()
        snapshot_data = response.json()
        snapshot_name = snapshot_data["result"]["name"]
        print(f"Snapshot created: {snapshot_name}")

        # Download snapshot
        download_url = f"{url}/{snapshot_name}"
        target_path = QDRANT_DIR / snapshot_name
        
        print(f"Downloading snapshot to {target_path}...")
        with requests.get(download_url, stream=True) as r:
            r.raise_for_status()
            with open(target_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
        print("Qdrant export successful.")
        return snapshot_name
    except Exception as e:
        print(f"Failed to export Qdrant: {e}")
        return None

def export_postgres():
    print("\n--- Exporting PostgreSQL Dump ---")
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    dump_filename = f"reportdb_{timestamp}.dump"
    target_path = POSTGRES_DIR / dump_filename
    
    # We use docker exec to run pg_dump inside the container
    # Assuming container name is 'try-postgres-1' or similar from docker-compose
    # Better to use the service name if possible, but docker-compose usually prefixes it
    
    # Let's try to find the container name
    container_cmd = "docker ps --filter name=postgres --format \"{{.Names}}\""
    result = run_command(container_cmd)
    container_names = result.stdout.strip().split('\n')
    
    container_name = None
    for name in container_names:
        name = name.strip()
        if not name: continue
        # Check if this container has the reportdb
        check_db_cmd = f"docker exec {name} psql -U postgres -lqt | findstr reportdb"
        check_result = run_command(check_db_cmd)
        if "reportdb" in check_result.stdout:
            container_name = name
            break
            
    if not container_name:
        print("Could not find a postgres container with 'reportdb' database.")
        return None

    print(f"Using container: {container_name}")
    
    # pg_dump command
    # -Fc is custom format (compressed)
    dump_cmd = f"docker exec {container_name} pg_dump -U postgres -Fc reportdb"
    
    try:
        # Run and capture binary output
        print(f"Dumping database to {target_path}...")
        result = subprocess.run(dump_cmd, shell=True, capture_output=True)
        if result.returncode == 0:
            with open(target_path, "wb") as f:
                f.write(result.stdout)
            print("PostgreSQL export successful.")
            return dump_filename
        else:
            print(f"PostgreSQL dump failed: {result.stderr.decode()}")
            return None
    except Exception as e:
        print(f"Failed to export PostgreSQL: {e}")
        return None

def create_manifest(q_file, p_file):
    print("\n--- Creating Manifest ---")
    timestamp = datetime.datetime.now().isoformat()
    
    # Get git hash if available
    git_hash = "unknown"
    try:
        git_result = run_command("git rev-parse HEAD")
        if git_result.returncode == 0:
            git_hash = git_result.stdout.strip()
    except:
        pass

    manifest = {
        "export_time": timestamp,
        "git_commit": git_hash,
        "qdrant_collection": COLLECTION_NAME,
        "qdrant_snapshot": q_file,
        "postgres_dump": p_file,
        "embedding_model": settings.azure_embedding_deployment,
        "instructions": "Run python backend/scripts/import_data.py to restore this package."
    }
    
    with open(DATA_PACKAGE_DIR / "manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    
    # Also a text version for easy reading
    with open(DATA_PACKAGE_DIR / "manifest.txt", "w", encoding="utf-8") as f:
        f.write(f"Export Time: {timestamp}\n")
        f.write(f"Git Commit: {git_hash}\n")
        f.write(f"Embedding Model: {settings.azure_embedding_deployment}\n")
        f.write(f"Qdrant Snapshot: {q_file}\n")
        f.write(f"Postgres Dump: {p_file}\n")

    print("Manifest created.")

def main():
    QDRANT_DIR.mkdir(parents=True, exist_ok=True)
    POSTGRES_DIR.mkdir(parents=True, exist_ok=True)
    
    q_file = export_qdrant()
    p_file = export_postgres()
    
    if q_file and p_file:
        create_manifest(q_file, p_file)
        print(f"\nSuccess! Data package is ready in: {DATA_PACKAGE_DIR}")
    else:
        print("\nExport failed. Please check the logs.")

if __name__ == "__main__":
    main()
