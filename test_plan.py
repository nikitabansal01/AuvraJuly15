import asyncio
import os
import sys
from sqlalchemy.ext.asyncio import create_async_engine

os.environ["ENVIRONMENT"] = "development"
DATABASE_URL = "postgresql+asyncpg://postgres:Nikita%40123@aws-0-ap-south-1.pooler.supabase.com:6543/postgres"

engine = create_async_engine(DATABASE_URL, connect_args={"statement_cache_size": 0, "prepared_statement_cache_size": 0})

from app.core.database import async_engine, get_async_db_session
from app.services.action_plan_generator import get_action_plan_generator

async def main():
    print("Testing get_or_generate_today_plan...")
    async with get_async_db_session() as db:
        generator = get_action_plan_generator()
        try:
            result = await generator.get_or_generate_today_plan(
                user_id="z7PY98cIMiUQOllsqciI9e1vtIm1",
                user_timezone="Asia/Kolkata",
                db=db,
                image_mode="hero_only",
                skip_quality_check=False
            )
            print("RESULT:", result)
        except Exception as e:
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
