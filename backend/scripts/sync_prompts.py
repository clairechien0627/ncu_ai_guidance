import os
import sys
from langfuse import get_client
from dotenv import load_dotenv

# Add the project root to sys.path to import local modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

load_dotenv()

def sync_prompts():
    langfuse = get_client()
    prompts_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "prompts"))
    
    if not os.path.exists(prompts_dir):
        print(f"Error: Prompts directory not found at {prompts_dir}")
        return

    # List of prompts to sync
    prompt_files = [f for f in os.listdir(prompts_dir) if f.endswith(".txt")]
    
    print(f"Found {len(prompt_files)} prompt files. Starting sync...")

    for filename in prompt_files:
        prompt_name = os.path.splitext(filename)[0]
        file_path = os.path.join(prompts_dir, filename)
        
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        
        print(f"Syncing '{prompt_name}'...")
        
        try:
            # Create or update prompt in Langfuse
            # Note: We use 'text' type by default as it's the safest migration path for raw .txt files
            langfuse.create_prompt(
                name=prompt_name,
                prompt=content,
                type="text",
                labels=["production"]
            )
            print(f"Successfully synced '{prompt_name}' and labeled as 'production'.")
        except Exception as e:
            print(f"Failed to sync '{prompt_name}': {e}")

    print("\nSync complete! Check your Langfuse dashboard at: https://cloud.langfuse.com")

if __name__ == "__main__":
    sync_prompts()
