from app.core.database import engine
from sqlalchemy import inspect, text

insp = inspect(engine)
print("Tables:", insp.get_table_names())

with engine.connect() as conn:
    result = conn.execute(text("SELECT data_type FROM information_schema.columns WHERE table_name = 'chat_messages' AND column_name = 'id'"))
    rows = list(result)
    if rows:
        print("chat_messages.id type:", rows[0][0])
    else:
        print("chat_messages table not found or id column not found")
