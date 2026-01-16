#!/usr/bin/env python3
"""Query OpenAI API to list available models."""
import os
import sys
import asyncio
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from openai import AsyncOpenAI
except ImportError:
    print("ERROR: openai library not installed")
    sys.exit(1)

async def list_models():
    """List all available OpenAI models."""
    # Get API key from environment
    api_key = os.getenv("OPENAI_API_KEY")
    
    if not api_key:
        # Try loading from .env file
        env_file = Path(__file__).parent.parent / ".env"
        if env_file.exists():
            with open(env_file) as f:
                for line in f:
                    if line.startswith("OPENAI_API_KEY="):
                        api_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                        break
        
        if not api_key:
            print("ERROR: OPENAI_API_KEY not found in environment or .env file")
            sys.exit(1)
    
    client = AsyncOpenAI(api_key=api_key)
    
    try:
        print("Querying OpenAI API for available models...")
        print("-" * 80)
        
        # Get list of models
        models = await client.models.list()
        
        # Filter to only models that can be used for completions/chat
        # and exclude deprecated models
        available_models = []
        for model in models.data:
            model_id = model.id
            # Skip models that are clearly deprecated or internal
            if any(skip in model_id.lower() for skip in ['deprecated', 'instruct', 'ada-001']):
                continue
            
            # Focus on chat/completion models
            if any(prefix in model_id for prefix in ['gpt-', 'o1-', 'o3-', 'o1-preview']):
                available_models.append(model_id)
        
        # Sort models
        available_models.sort()
        
        print(f"\nFound {len(available_models)} available model(s):\n")
        for model in available_models:
            print(f"  ✓ {model}")
        
        print("\n" + "-" * 80)
        print("\nChat/Completion Models Summary:")
        
        # Group by model family
        families = {
            'GPT-4o': [m for m in available_models if 'gpt-4o' in m],
            'GPT-4 Turbo': [m for m in available_models if 'gpt-4-turbo' in m],
            'GPT-4': [m for m in available_models if 'gpt-4' in m and 'gpt-4o' not in m and 'gpt-4-turbo' not in m],
            'GPT-3.5': [m for m in available_models if 'gpt-3.5' in m],
            'O1': [m for m in available_models if 'o1' in m],
            'O3': [m for m in available_models if 'o3' in m],
        }
        
        for family, models in families.items():
            if models:
                print(f"\n  {family}:")
                for model in models:
                    print(f"    - {model}")
        
        # Check for embedding models
        embedding_models = [m.id for m in models.data if 'embedding' in m.id.lower()]
        if embedding_models:
            print(f"\n  Embedding Models:")
            for model in sorted(embedding_models):
                print(f"    - {model}")
        
        print("\n" + "=" * 80)
        print("Query completed successfully!")
        
    except Exception as e:
        print(f"\nERROR: Failed to query OpenAI API: {e}")
        print(f"Error type: {type(e).__name__}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(list_models())

