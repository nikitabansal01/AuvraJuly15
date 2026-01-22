#!/usr/bin/env python3
"""
Script to find and delete corrupted meditation images from the image_library database.

Usage:
    python scripts/delete_corrupted_meditation_image.py --list     # List all meditation images
    python scripts/delete_corrupted_meditation_image.py --delete   # Delete meditation images
"""

import os
import sys
import argparse

# Load environment variables from .env file
from dotenv import load_dotenv
load_dotenv()

# Check for DATABASE_URL
DATABASE_URL = os.getenv('DATABASE_URL')
if not DATABASE_URL:
    print("ERROR: DATABASE_URL not found in .env")
    print("Please set DATABASE_URL in your .env file")
    sys.exit(1)

# Add SSL if needed
if '?' not in DATABASE_URL:
    DATABASE_URL += '?sslmode=require'
elif 'sslmode' not in DATABASE_URL:
    DATABASE_URL += '&sslmode=require'

from sqlalchemy import create_engine, text
from datetime import datetime

def list_meditation_images():
    """List all images in the image_library that contain 'meditation' in prompt_text."""
    engine = create_engine(DATABASE_URL)
    
    with engine.connect() as conn:
        # Find all meditation-related images
        result = conn.execute(text("""
            SELECT id, image_url, prompt_text, category, variant_type, 
                   usage_count, created_at, last_used_at
            FROM image_library 
            WHERE LOWER(prompt_text) LIKE '%meditation%'
               OR LOWER(prompt_text) LIKE '%mindful%'
               OR category = 'mindfulness'
            ORDER BY id DESC
        """))
        
        rows = result.fetchall()
        
        if not rows:
            print("\n✅ No meditation/mindfulness images found in image_library")
            return []
        
        print(f"\n📋 Found {len(rows)} meditation/mindfulness images:\n")
        print("-" * 100)
        
        for row in rows:
            print(f"ID: {row.id}")
            print(f"  URL: {row.image_url[:80]}..." if len(row.image_url) > 80 else f"  URL: {row.image_url}")
            print(f"  Prompt: {row.prompt_text[:60]}..." if len(row.prompt_text) > 60 else f"  Prompt: {row.prompt_text}")
            print(f"  Category: {row.category}, Variant: {row.variant_type}")
            print(f"  Usage: {row.usage_count}, Created: {row.created_at}")
            print("-" * 100)
        
        return rows


def delete_meditation_images(image_ids=None):
    """Delete meditation images from image_library."""
    engine = create_engine(DATABASE_URL)
    
    with engine.connect() as conn:
        if image_ids:
            # Delete specific IDs
            result = conn.execute(text("""
                DELETE FROM image_library 
                WHERE id = ANY(:ids)
                RETURNING id, prompt_text
            """), {"ids": list(image_ids)})
        else:
            # Delete all meditation/mindfulness images
            result = conn.execute(text("""
                DELETE FROM image_library 
                WHERE LOWER(prompt_text) LIKE '%meditation%'
                   OR LOWER(prompt_text) LIKE '%mindful%'
                   OR category = 'mindfulness'
                RETURNING id, prompt_text
            """))
        
        deleted = result.fetchall()
        conn.commit()
        
        if deleted:
            print(f"\n🗑️  Deleted {len(deleted)} images:")
            for row in deleted:
                print(f"  - ID {row.id}: {row.prompt_text[:50]}...")
        else:
            print("\n⚠️  No images were deleted")
        
        return deleted


def clear_all_mindfulness_from_action_plans():
    """Clear hero_image_url from action_plan_items that are mindfulness category.
    This forces regeneration next time."""
    engine = create_engine(DATABASE_URL)
    
    with engine.connect() as conn:
        result = conn.execute(text("""
            UPDATE action_plan_items 
            SET hero_image_url = NULL 
            WHERE category = 'mindfulness'
            AND hero_image_url IS NOT NULL
            RETURNING id, title
        """))
        
        updated = result.fetchall()
        conn.commit()
        
        if updated:
            print(f"\n🔄 Cleared hero_image_url for {len(updated)} mindfulness action_plan_items")
            for row in updated[:5]:  # Show first 5
                print(f"  - ID {row.id}: {row.title}")
            if len(updated) > 5:
                print(f"  ... and {len(updated) - 5} more")
        else:
            print("\n⚠️  No action_plan_items needed clearing")
        
        return updated


def main():
    parser = argparse.ArgumentParser(description='Manage meditation images in image_library')
    parser.add_argument('--list', action='store_true', help='List all meditation images')
    parser.add_argument('--delete', action='store_true', help='Delete all meditation images')
    parser.add_argument('--delete-id', type=int, nargs='+', help='Delete specific image IDs')
    parser.add_argument('--clear-action-plans', action='store_true', 
                        help='Clear hero_image_url from mindfulness action_plan_items')
    
    args = parser.parse_args()
    
    if not any([args.list, args.delete, args.delete_id, args.clear_action_plans]):
        # Default: list images
        args.list = True
    
    print("=" * 60)
    print("  MEDITATION IMAGE CACHE MANAGER")
    print("=" * 60)
    
    if args.list:
        rows = list_meditation_images()
        if rows:
            print(f"\n💡 To delete all these images, run:")
            print(f"   python scripts/delete_corrupted_meditation_image.py --delete")
            print(f"\n💡 To delete specific IDs, run:")
            print(f"   python scripts/delete_corrupted_meditation_image.py --delete-id <id1> <id2> ...")
    
    if args.delete:
        confirm = input("\n⚠️  Delete ALL meditation/mindfulness images? (yes/no): ")
        if confirm.lower() == 'yes':
            delete_meditation_images()
        else:
            print("Aborted.")
    
    if args.delete_id:
        print(f"\nDeleting specific IDs: {args.delete_id}")
        delete_meditation_images(args.delete_id)
    
    if args.clear_action_plans:
        confirm = input("\n⚠️  Clear hero_image_url from all mindfulness action_plan_items? (yes/no): ")
        if confirm.lower() == 'yes':
            clear_all_mindfulness_from_action_plans()
        else:
            print("Aborted.")
    
    print("\n✅ Done!")


if __name__ == "__main__":
    main()
