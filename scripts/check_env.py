#!/usr/bin/env python3
"""
RAG module environment variable verification script
"""

import os
from dotenv import load_dotenv

load_dotenv()

def check_rag_environment():
    """Verify environment variables required for RAG module."""
    
    print("🔍 RAG Module Environment Variable Check")
    print("=" * 50)
    
    # OpenAI settings
    openai_key = os.getenv("OPENAI_API_KEY")
    print(f"✅ OPENAI_API_KEY: {'Configured' if openai_key else '❌ Not configured'}")
    
    # Firecrawl settings
    firecrawl_key = os.getenv("FIRECRAWL_API_KEY")
    firecrawl_url = os.getenv("FIRECRAWL_BASE_URL", "https://api.firecrawl.dev/v1/scrape")
    print(f"✅ FIRECRAWL_API_KEY: {'Configured' if firecrawl_key else '❌ Not configured'}")
    print(f"✅ FIRECRAWL_BASE_URL: {firecrawl_url}")
    
    # Pinecone settings
    pinecone_key = os.getenv("PINECONE_API_KEY")
    pinecone_env = os.getenv("PINECONE_ENVIRONMENT")
    pinecone_index = os.getenv("PINECONE_INDEX")
    
    print(f"✅ PINECONE_API_KEY: {'Configured' if pinecone_key else '❌ Not configured'}")
    print(f"✅ PINECONE_ENVIRONMENT: {pinecone_env or '❌ Not configured'}")
    print(f"✅ PINECONE_INDEX: {pinecone_index or '❌ Not configured'}")
    
    print("\n📋 Summary:")
    if all([openai_key, firecrawl_key, pinecone_key, pinecone_env, pinecone_index]):
        print("🎉 All environment variables are configured!")
    else:
        print("⚠️  Some environment variables are not configured.")
        if not openai_key:
            print("  - OPENAI_API_KEY required")
        if not firecrawl_key:
            print("  - FIRECRAWL_API_KEY required")
        if not pinecone_key:
            print("  - PINECONE_API_KEY required")
        if not pinecone_env:
            print("  - PINECONE_ENVIRONMENT required")
        if not pinecone_index:
            print("  - PINECONE_INDEX required")

if __name__ == "__main__":
    check_rag_environment() 