#!/usr/bin/env python3
"""List meditation images in the image_library database."""
import os
import sys

# Force unbuffered output
os.environ['PYTHONUNBUFFERED'] = '1'

print("=== MEDITATION IMAGE FINDER ===", flush=True)

from dotenv import load_dotenv
load_dotenv()

DATABASE_URL = os.getenv('DATABASE_URL')
if not DATABASE_URL:
    print('ERROR: DATABASE_URL not found in .env')
    sys.exit(1)

print(f"DATABASE_URL found (length={len(DATABASE_URL)})", flush=True)

if '?' not in DATABASE_URL:
    DATABASE_URL += '?sslmode=require'
elif 'sslmode' not in DATABASE_URL:
    DATABASE_URL += '&sslmode=require'

print("Connecting to database...", flush=True)

from sqlalchemy import create_engine, text

try:
    engine = create_engine(DATABASE_URL)
    with engine.connect() as conn:
        print("Connected! Querying image_library...", flush=True)
        
        # First check total count in image_library
        total_result = conn.execute(text("SELECT COUNT(*) FROM image_library"))
        total_count = total_result.scalar()
        print(f"Total images in library: {total_count}", flush=True)
        
        # Check mindfulness specifically
        mind_result = conn.execute(text("SELECT COUNT(*) FROM image_library WHERE category = 'mindfulness'"))
        mind_count = mind_result.scalar()
        print(f"Mindfulness category images: {mind_count}", flush=True)
        
        # Get meditation images
        result = conn.execute(text("""
            SELECT id, image_url, prompt_text, category, variant_type
            FROM image_library 
            WHERE LOWER(prompt_text) LIKE '%meditation%'
               OR LOWER(prompt_text) LIKE '%mindful%'
               OR category = 'mindfulness'
            ORDER BY id DESC
            LIMIT 20
        """))
        rows = result.fetchall()
        
        if not rows:
            print('\nNo meditation images found', flush=True)
        else:
            print(f'\n=== Found {len(rows)} meditation/mindfulness images ===', flush=True)
            for row in rows:
                print(f'\nID: {row.id}', flush=True)
                url = row.image_url[:80] if row.image_url else 'None'
                print(f'  URL: {url}...', flush=True)
                prompt = row.prompt_text[:60] if row.prompt_text else 'None'
                print(f'  Prompt: {prompt}...', flush=True)
                print(f'  Category: {row.category}, Variant: {row.variant_type}', flush=True)
        
        print("\nDone!", flush=True)
        
except Exception as e:
    print(f"ERROR: {e}", flush=True)
    import traceback
    traceback.print_exc()
